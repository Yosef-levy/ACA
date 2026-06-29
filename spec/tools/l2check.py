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

L2c — **Delegation reporting coverage.** Each ``delegation`` entry lists the
  ``reporting`` symbols the parent expects from the child. The child must expose
  those symbols back to the parent (an ``exposure`` entry with ``to: <parent>``
  whose ``view`` covers them); otherwise the parent cannot observe the
  commitment it delegated.

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

    def extract(self, node, out, depth=0, dropped=None):
        """Collect inequalities implied by a boolean expression into ``out``.

        When ``dropped`` is a list, any conjunct that falls outside the modelled
        linear fragment is appended to it. A caller can therefore tell whether
        the expression was modelled *exactly* (``dropped`` stayed empty) -- which
        is what entailment reasoning requires to stay sound.
        """
        node = self._inline(node, depth)
        if isinstance(node, Binary) and node.op == "&&":
            self.extract(node.left, out, depth, dropped)
            self.extract(node.right, out, depth, dropped)
            return
        if isinstance(node, Binary) and node.op == "||":
            if dropped is not None:
                dropped.append("||")
            return  # disjunction: not modelled, drop (conservative)
        if isinstance(node, Unary) and node.op == "!":
            neg = self._negate(node.operand, depth)
            if neg is not None:
                self.extract(neg, out, depth, dropped)
            elif dropped is not None:
                dropped.append("!")
            return
        if isinstance(node, Binary) and node.op in ("<", "<=", ">", ">=", "=="):
            self._emit_comparison(node, out, dropped)
            return
        # bare predicate identifier already inlined; anything else is opaque.
        if dropped is not None:
            dropped.append(node)

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

    def _emit_comparison(self, node, out, dropped=None):
        try:
            left = self._linear(node.left)
            right = self._linear(node.right)
        except NonLinear:
            if dropped is not None:
                dropped.append(node)
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


def negate(ineq: Ineq) -> Ineq:
    """The logical negation of a single inequality.

    ``e <= 0`` becomes ``e > 0`` (i.e. ``-e < 0``); ``e < 0`` becomes
    ``-e <= 0``. Strictness flips.
    """
    return Ineq({v: -c for v, c in ineq.coeffs.items()}, -ineq.const, not ineq.strict)


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


def check_delegation_reporting(charters, errors):
    """L2c: a child must expose, back to its parent, what the parent delegates.

    Each ``delegation`` entry lists ``reporting`` symbols the parent expects the
    child to surface (in the child's vocabulary). For the delegation edge to be
    well-formed, the child must have an ``exposure`` entry targeting the parent
    whose ``view`` covers every reporting symbol; otherwise the parent has no way
    to observe the commitment it delegated.
    """
    by_id = {c["object"]["id"]: c for c in charters}
    for parent in charters:
        pid = parent["object"]["id"]
        for f in parent.get("delegation", []):
            reporting = set(f.get("reporting", []))
            if not reporting:
                continue
            for child_id in f.get("to", []):
                child = by_id.get(child_id)
                if child is None:
                    continue  # L0 reports unknown object ids
                exposed = set()
                for e in child.get("exposure", []):
                    if e.get("to") == pid:
                        exposed.update(e.get("view", []))
                missing = reporting - exposed
                if missing:
                    errors.append(
                        f"{pid}: delegation of '{f['delegates']}' to '{child_id}' "
                        f"expects reporting {sorted(reporting)}, but '{child_id}' "
                        f"does not expose {sorted(missing)} to '{pid}'"
                    )


def check_delegation_entailment(charters, errors):
    """L2d: assumed child guarantees must entail the parent's delegated commitment.

    A delegation may carry ``refinement.assume`` -- the guarantees the parent
    relies on the child to provide, written in the parent's R extended with the
    delegation's ``reporting`` symbols. The parent's ``delegates`` symbol names a
    commitment defined in the parent's R. This check verifies that the
    assumptions *entail* the commitment: ``assume => delegates``.

    Entailment ``A => B`` holds iff ``A and not B`` is unsatisfiable. Because the
    satisfiability engine is conservative (it drops everything outside the linear
    fragment), this check only runs when the assumptions are modelled *exactly*
    (no dropped conjuncts). Under that condition both SAT and UNSAT verdicts are
    sound, so a reported entailment failure is a genuine counterexample, not an
    artefact of approximation. Commitments outside the linear fragment are simply
    left unverified rather than flagged.
    """
    for parent in charters:
        pid = parent["object"]["id"]
        defs = _definitions(parent)
        base_env = celcheck.build_env(parent)
        for f in parent.get("delegation", []):
            assume = (f.get("refinement") or {}).get("assume", [])
            if not assume:
                continue

            env = celcheck.Env(
                vars=dict(base_env.vars),
                states=set(base_env.states),
                enums=dict(base_env.enums),
            )
            for sym in f.get("reporting", []):
                env.vars.setdefault(sym, celcheck.DYN)
            numeric = _numeric_vars(env)
            extractor = AtomExtractor(defs, numeric)

            # Model the assumptions exactly; bail out if anything is dropped, so
            # we never report a failure we cannot soundly justify.
            assume_atoms = list(_range_bounds(parent, numeric))
            modelled = True
            for expr in assume:
                try:
                    ast = celcheck.parse(expr)
                except celcheck.CelError:
                    modelled = False
                    break
                dropped = []
                extractor.extract(ast, assume_atoms, dropped=dropped)
                if dropped:
                    modelled = False
                    break
            if not modelled:
                continue

            # Model the delegated commitment. Dropping conjuncts here is safe:
            # the real commitment is only *stronger*, so a counterexample to a
            # modelled conjunct is a counterexample to the whole commitment.
            body = defs.get(f.get("delegates"))
            if body is None:
                continue  # delegates is a bare term / not a named commitment
            commit_atoms = []
            extractor.extract(body, commit_atoms)
            if not commit_atoms:
                continue  # commitment not expressible in the linear fragment

            for atom in commit_atoms:
                if is_satisfiable(assume_atoms + [negate(atom)]):
                    errors.append(
                        f"{pid}: delegation of '{f['delegates']}' to "
                        f"{sorted(f.get('to', []))} is under-refined: assumed "
                        f"guarantees {list(assume)} do not entail "
                        f"'{f['delegates']}'"
                    )
                    break


def check_system(charters, errors):
    """Run all L2 checks over a fully-loaded set of charters."""
    for c in charters:
        check_satisfiability(c, errors)
    check_inherited_provenance(charters, errors)
    check_delegation_reporting(charters, errors)
    check_delegation_entailment(charters, errors)
