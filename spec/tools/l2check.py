"""ACA-Spec L2 (compositional validity) reference prototype.

L1 asks whether each charter is *locally* well-formed. L2 asks whether the
commitments a system hands to its objects can hold *together*. This module
implements a small, dependency-free, deliberately conservative subset of the L2
checks described in ``aca-spec.md`` §10:

L2a — **Hard-constraint satisfiability.** For each object, the conjunction of
  every ``severity: hard`` constraint that governs it (any scope: ``local`` /
  ``inherited`` / ``shared``), together with the numeric ``range`` bounds from
  its ``R``, must be satisfiable. A contradiction here means the object can
  never act validly no matter what it decides.

L2b — **Inherited-constraint provenance.** Every ``scope: inherited`` constraint
  names an ``owner``; that owner must actually delegate to this object via a
  delegation edge (``F``). An inherited commitment with no delegation behind it
  is a dangling obligation.

Like ``celcheck`` for L1, this checker is **conservative**: it reports a failure
only when it can *prove* one inside a decidable fragment (conjunctions of linear
inequalities over the reals). Everything it cannot model -- disjunctions (``||``),
``!=``, non-linear terms, durations/timestamps, opaque abstract types -- is
dropped from the system being tested. Dropping conjuncts only *weakens* the
system, so any "unsatisfiable" verdict on the modelled subset is sound for the
full system; the cost is that some real contradictions are reported as
``unknown`` (i.e. not flagged) rather than producing false positives.

Satisfiability is decided by Fourier-Motzkin elimination over exact rationals
(``fractions.Fraction``), which is complete for linear real arithmetic. Integer
terms are relaxed to reals: an infeasible real relaxation is infeasible over the
integers too, so reporting UNSAT stays sound.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import celcheck
from celcheck import Binary, Call, Ident, Index, Lit, Member, Unary

_MAX_INLINE_DEPTH = 64


class NonLinear(Exception):
    """Raised when an expression falls outside the modelled linear fragment."""


# --- Linear forms -----------------------------------------------------------


@dataclass
class Linear:
    """A linear form ``sum(coeff_i * var_i) + const`` over numeric variables."""

    coeffs: dict  # var name -> Fraction
    const: Fraction

    @staticmethod
    def constant(c):
        return Linear({}, Fraction(c))

    @staticmethod
    def var(name):
        return Linear({name: Fraction(1)}, Fraction(0))

    def scale(self, k):
        k = Fraction(k)
        return Linear({v: c * k for v, c in self.coeffs.items()}, self.const * k)

    def add(self, other):
        coeffs = dict(self.coeffs)
        for v, c in other.coeffs.items():
            coeffs[v] = coeffs.get(v, Fraction(0)) + c
        return Linear(coeffs, self.const + other.const)

    def sub(self, other):
        return self.add(other.scale(-1))


@dataclass
class Ineq:
    """``sum(coeffs * vars) + const <op> 0`` where op is ``<`` (strict) or ``<=``."""

    coeffs: dict
    const: Fraction
    strict: bool

    def vars(self):
        return {v for v, c in self.coeffs.items() if c != 0}


# --- Expression -> linear atoms ---------------------------------------------


class AtomExtractor:
    """Turn a constraint AST into a list of linear ``Ineq`` constraints.

    Anything outside the linear fragment is silently dropped (it contributes no
    constraint), which weakens the system and keeps UNSAT verdicts sound.
    """

    def __init__(self, defs, numeric_vars):
        self.defs = defs  # predicate/success name -> body AST (for inlining)
        self.numeric = numeric_vars  # set of var names with a numeric CEL type

    def extract(self, node, out, depth=0):
        """Collect inequalities implied by a boolean expression into ``out``."""
        node = self._inline(node, depth)
        if isinstance(node, Binary) and node.op == "&&":
            self.extract(node.left, out, depth)
            self.extract(node.right, out, depth)
            return
        if isinstance(node, Binary) and node.op == "||":
            return  # disjunction: not modelled, drop (conservative)
        if isinstance(node, Unary) and node.op == "!":
            neg = self._negate(node.operand, depth)
            if neg is not None:
                self.extract(neg, out, depth)
            return
        if isinstance(node, Binary) and node.op in ("<", "<=", ">", ">=", "=="):
            self._emit_comparison(node, out)
            return
        # bare predicate identifier already inlined; anything else is opaque.

    def _inline(self, node, depth):
        """Replace a predicate/success identifier with its (parsed) body."""
        if isinstance(node, Ident) and node.name in self.defs and depth < _MAX_INLINE_DEPTH:
            return self._inline(self.defs[node.name], depth + 1)
        return node

    def _negate(self, node, depth):
        node = self._inline(node, depth)
        if isinstance(node, Binary) and node.op in ("<", "<=", ">", ">="):
            flip = {"<": ">=", "<=": ">", ">": "<=", ">=": "<"}[node.op]
            return Binary(flip, node.left, node.right)
        return None  # !=, !(a && b), opaque: drop

    def _emit_comparison(self, node, out):
        try:
            left = self._linear(node.left)
            right = self._linear(node.right)
        except NonLinear:
            return
        diff = left.sub(right)  # left - right <op> 0
        op = node.op
        if op == "<=":
            out.append(Ineq(diff.coeffs, diff.const, strict=False))
        elif op == "<":
            out.append(Ineq(diff.coeffs, diff.const, strict=True))
        elif op == ">=":
            neg = diff.scale(-1)
            out.append(Ineq(neg.coeffs, neg.const, strict=False))
        elif op == ">":
            neg = diff.scale(-1)
            out.append(Ineq(neg.coeffs, neg.const, strict=True))
        elif op == "==":
            out.append(Ineq(diff.coeffs, diff.const, strict=False))
            neg = diff.scale(-1)
            out.append(Ineq(neg.coeffs, neg.const, strict=False))

    def _linear(self, node) -> Linear:
        if isinstance(node, Lit):
            if node.type in (celcheck.INT, celcheck.DOUBLE):
                return Linear.constant(Fraction(node.raw))
            raise NonLinear
        if isinstance(node, Ident):
            if node.name in self.numeric:
                return Linear.var(node.name)
            raise NonLinear
        if isinstance(node, Unary):
            if node.op == "-":
                return self._linear(node.operand).scale(-1)
            raise NonLinear
        if isinstance(node, Binary):
            if node.op == "+":
                return self._linear(node.left).add(self._linear(node.right))
            if node.op == "-":
                return self._linear(node.left).sub(self._linear(node.right))
            if node.op == "*":
                return self._mul(node.left, node.right)
            if node.op == "/":
                denom = self._const_value(node.right)
                if denom is None or denom == 0:
                    raise NonLinear
                return self._linear(node.left).scale(Fraction(1) / denom)
            raise NonLinear
        raise NonLinear  # Call / Member / Index -> opaque

    def _mul(self, a, b) -> Linear:
        ca, cb = self._const_value(a), self._const_value(b)
        if ca is not None:
            return self._linear(b).scale(ca)
        if cb is not None:
            return self._linear(a).scale(cb)
        raise NonLinear  # variable * variable

    def _const_value(self, node):
        try:
            lin = self._linear(node)
        except NonLinear:
            return None
        return lin.const if not lin.coeffs else None


# --- Fourier-Motzkin satisfiability -----------------------------------------


def _trivially_false(ineq: Ineq) -> bool:
    """A variable-free inequality that cannot hold."""
    if ineq.vars():
        return False
    if ineq.strict:
        return ineq.const >= 0  # const < 0 required
    return ineq.const > 0       # const <= 0 required


def is_satisfiable(ineqs) -> bool:
    """Return False only if the conjunction of ``ineqs`` is provably infeasible.

    Complete for linear real arithmetic via Fourier-Motzkin elimination.
    """
    system = [Ineq(dict(i.coeffs), i.const, i.strict) for i in ineqs]

    # Eliminate variables one at a time.
    remaining_vars = set()
    for i in system:
        remaining_vars |= i.vars()

    while remaining_vars:
        v = next(iter(remaining_vars))
        pos, neg, zero = [], [], []
        for i in system:
            cv = i.coeffs.get(v, Fraction(0))
            if cv > 0:
                pos.append(i)
            elif cv < 0:
                neg.append(i)
            else:
                zero.append(i)

        combined = list(zero)
        for p in pos:
            ap = p.coeffs[v]
            for n in neg:
                an = n.coeffs[v]  # an < 0
                # p.scale(-an) + n.scale(ap): both multipliers > 0, v cancels.
                lp = Linear(dict(p.coeffs), p.const).scale(-an)
                ln = Linear(dict(n.coeffs), n.const).scale(ap)
                s = lp.add(ln)
                s.coeffs.pop(v, None)
                merged = Ineq(s.coeffs, s.const, strict=p.strict or n.strict)
                if _trivially_false(merged):
                    return False
                combined.append(merged)

        system = combined
        remaining_vars = set()
        for i in system:
            remaining_vars |= i.vars()

    for i in system:
        if _trivially_false(i):
            return False
    return True


# --- System-level L2 checks --------------------------------------------------


def _numeric_vars(env: celcheck.Env) -> set:
    return {n for n, t in env.vars.items() if t in (celcheck.INT, celcheck.DOUBLE)}


def _definitions(charter) -> dict:
    """predicate/success name -> parsed body AST (best-effort; skip unparseable)."""
    defs = {}
    r = charter.get("representation", {})
    for group in ("predicates", "success"):
        for item in r.get(group, []):
            try:
                defs[item["name"]] = celcheck.parse(item["expr"])
            except celcheck.CelError:
                pass
    return defs


def _range_bounds(charter, numeric):
    """Inequalities from each numeric term's declared ``range: [lo, hi]``."""
    out = []
    for term in charter.get("representation", {}).get("terms", []):
        rng = term.get("range")
        name = term.get("name")
        if rng and name in numeric and len(rng) == 2:
            lo, hi = rng
            out.append(Ineq({name: Fraction(-1)}, Fraction(lo), strict=False))   # lo - x <= 0
            out.append(Ineq({name: Fraction(1)}, -Fraction(hi), strict=False))   # x - hi <= 0
    return out


def check_satisfiability(charter, errors):
    """L2a: the object's hard constraints must be jointly satisfiable."""
    cid = charter["object"]["id"]
    env = celcheck.build_env(charter)
    numeric = _numeric_vars(env)
    extractor = AtomExtractor(_definitions(charter), numeric)

    ineqs = list(_range_bounds(charter, numeric))
    hard = [
        c for c in charter.get("constraints", [])
        if c.get("severity", "hard") == "hard"
    ]
    for c in hard:
        try:
            ast = celcheck.parse(c["expr"])
        except celcheck.CelError:
            continue  # L1 already reports parse errors
        extractor.extract(ast, ineqs)

    if not is_satisfiable(ineqs):
        ids = ", ".join(sorted(c["id"] for c in hard))
        errors.append(
            f"{cid}: hard constraints are jointly unsatisfiable "
            f"(over modelled linear terms): {{{ids}}}"
        )


def check_inherited_provenance(charters, errors):
    """L2b: every inherited constraint traces to a delegation edge from its owner."""
    by_id = {c["object"]["id"]: c for c in charters}
    # delegator -> set of object ids it delegates to (the H edges).
    delegates_to = {}
    for c in charters:
        cid = c["object"]["id"]
        targets = set()
        for f in c.get("delegation", []):
            targets.update(f.get("to", []))
        delegates_to[cid] = targets

    for c in charters:
        cid = c["object"]["id"]
        for con in c.get("constraints", []):
            if con.get("scope") != "inherited":
                continue
            owner = con.get("owner")
            if owner == cid:
                errors.append(
                    f"{cid}: inherited constraint '{con['id']}' is owned by the "
                    f"object itself (inherited constraints come from a parent)"
                )
                continue
            if owner not in by_id:
                continue  # L0 reports unknown object ids
            if cid not in delegates_to.get(owner, set()):
                errors.append(
                    f"{cid}: inherited constraint '{con['id']}' claims owner "
                    f"'{owner}', but '{owner}' has no delegation edge to '{cid}'"
                )


def check_system(charters, errors):
    """Run all L2 checks over a fully-loaded set of charters."""
    for c in charters:
        check_satisfiability(c, errors)
    check_inherited_provenance(charters, errors)
