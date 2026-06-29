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

L2d — **Delegation refinement entailment.** Where a delegation declares
  ``refinement.assume`` (the guarantees the parent relies on), those assumptions
  must entail the parent's delegated commitment.

Like ``celcheck`` for L1, this checker is **conservative**: it reports a failure
only when it can *prove* one. Two solvers back the checks:

* For the hard-constraint check (L2a), conjunctions of linear inequalities over
  the reals are decided by **Fourier-Motzkin elimination** over exact rationals
  (``fractions.Fraction``); anything outside that fragment is dropped, which only
  *weakens* the system, so an UNSAT verdict stays sound.
* For entailment (L2d), a small **SAT-modulo-linear-arithmetic** procedure
  handles arbitrary boolean structure (``&&``, ``||``, ``!``, ``==``, ``!=``)
  over linear atoms by enumerating truth assignments and checking each with
  Fourier-Motzkin. It is exact when all atoms are linear; opaque atoms (boolean
  terms, non-linear comparisons, durations/timestamps, abstract types) are
  treated as free booleans, a relaxation that keeps the UNSAT (``holds``)
  direction sound. A failure is reported only when the refutation is exact.

Integer terms are relaxed to reals throughout: an infeasible real relaxation is
infeasible over the integers too (sound for UNSAT), and entailment failures that
touch integer variables are withheld to avoid integer-domain false positives.
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


def linear_form(node, numeric) -> Linear:
    """Reduce an arithmetic AST to a ``Linear`` form, or raise ``NonLinear``."""
    if isinstance(node, Lit):
        if node.type in (celcheck.INT, celcheck.DOUBLE):
            return Linear.constant(Fraction(node.raw))
        raise NonLinear
    if isinstance(node, Ident):
        if node.name in numeric:
            return Linear.var(node.name)
        raise NonLinear
    if isinstance(node, Unary):
        if node.op == "-":
            return linear_form(node.operand, numeric).scale(-1)
        raise NonLinear
    if isinstance(node, Binary):
        if node.op == "+":
            return linear_form(node.left, numeric).add(linear_form(node.right, numeric))
        if node.op == "-":
            return linear_form(node.left, numeric).sub(linear_form(node.right, numeric))
        if node.op == "*":
            return _mul(node.left, node.right, numeric)
        if node.op == "/":
            denom = _const_value(node.right, numeric)
            if denom is None or denom == 0:
                raise NonLinear
            return linear_form(node.left, numeric).scale(Fraction(1) / denom)
        raise NonLinear
    raise NonLinear  # Call / Member / Index -> opaque


def _mul(a, b, numeric) -> Linear:
    ca, cb = _const_value(a, numeric), _const_value(b, numeric)
    if ca is not None:
        return linear_form(b, numeric).scale(ca)
    if cb is not None:
        return linear_form(a, numeric).scale(cb)
    raise NonLinear  # variable * variable


def _const_value(node, numeric):
    try:
        lin = linear_form(node, numeric)
    except NonLinear:
        return None
    return lin.const if not lin.coeffs else None


def comparison_ineq(node, numeric) -> Ineq:
    """Convert a single comparison (``<``, ``<=``, ``>``, ``>=``) to the ``Ineq``
    that is true exactly when the comparison holds. Raises ``NonLinear`` if
    either side is not linear over ``numeric``."""
    diff = linear_form(node.left, numeric).sub(linear_form(node.right, numeric))
    if node.op == "<=":
        return Ineq(diff.coeffs, diff.const, strict=False)
    if node.op == "<":
        return Ineq(diff.coeffs, diff.const, strict=True)
    if node.op == ">=":
        neg = diff.scale(-1)
        return Ineq(neg.coeffs, neg.const, strict=False)
    if node.op == ">":
        neg = diff.scale(-1)
        return Ineq(neg.coeffs, neg.const, strict=True)
    raise NonLinear


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
        return linear_form(node, self.numeric)


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


# --- Boolean formulas over atoms (SAT modulo linear arithmetic) -------------
#
# Entailment needs more than conjunctions of inequalities: a commitment such as
# `a <= 0 || b <= 0`, or the negation of a conjunction, has genuine boolean
# structure. We model a formula as a boolean combination of *atoms*, where each
# atom is either a linear inequality (`LinAtom`) or an opaque condition
# (`OpaqueAtom`: a bare boolean term, a non-linear comparison, a function call,
# ...). Satisfiability is decided by enumerating truth assignments over the
# atoms and checking each candidate's linear atoms with Fourier-Motzkin.
#
# This is exact when every atom is linear. With opaque atoms it is a *relaxation*
# (opaque atoms are treated as free, independent booleans with no theory), so an
# UNSAT verdict is still sound but a SAT verdict is not -- which is exactly the
# asymmetry the entailment caller relies on.

_SAT_ATOM_CAP = 16  # 2**n enumeration guard


@dataclass
class BoolConst:
    value: bool


@dataclass
class LinAtom:
    ineq: Ineq
    key: tuple


@dataclass
class OpaqueAtom:
    key: str


@dataclass
class Not:
    child: object


@dataclass
class And:
    left: object
    right: object


@dataclass
class Or:
    left: object
    right: object


def _ast_key(node) -> str:
    if isinstance(node, Lit):
        return f"lit:{node.type}:{node.raw}"
    if isinstance(node, Ident):
        return f"id:{node.name}"
    if isinstance(node, Unary):
        return f"u{node.op}({_ast_key(node.operand)})"
    if isinstance(node, Binary):
        return f"({_ast_key(node.left)}{node.op}{_ast_key(node.right)})"
    if isinstance(node, Member):
        return f"{_ast_key(node.base)}.{node.name}"
    if isinstance(node, Index):
        return f"{_ast_key(node.base)}[{_ast_key(node.index)}]"
    if isinstance(node, Call):
        return f"{_ast_key(node.func)}({','.join(_ast_key(a) for a in node.args)})"
    return repr(node)


def _ineq_key(ineq: Ineq) -> tuple:
    coeffs = tuple(sorted((v, c) for v, c in ineq.coeffs.items() if c != 0))
    return (coeffs, ineq.const, ineq.strict)


def to_formula(node, defs, numeric, depth=0):
    """Build a boolean ``Formula`` from a CEL AST, inlining named predicates."""
    if isinstance(node, Ident) and node.name in defs and depth < _MAX_INLINE_DEPTH:
        return to_formula(defs[node.name], defs, numeric, depth + 1)
    if isinstance(node, Lit):
        if node.type == celcheck.BOOL:
            return BoolConst(node.raw == "true")
        return OpaqueAtom(_ast_key(node))
    if isinstance(node, Unary) and node.op == "!":
        return Not(to_formula(node.operand, defs, numeric, depth))
    if isinstance(node, Binary):
        if node.op == "&&":
            return And(to_formula(node.left, defs, numeric, depth),
                       to_formula(node.right, defs, numeric, depth))
        if node.op == "||":
            return Or(to_formula(node.left, defs, numeric, depth),
                      to_formula(node.right, defs, numeric, depth))
        if node.op in ("<", "<=", ">", ">="):
            try:
                ineq = comparison_ineq(node, numeric)
            except NonLinear:
                return OpaqueAtom(_ast_key(node))
            return LinAtom(ineq, _ineq_key(ineq))
        if node.op in ("==", "!="):
            try:
                le = comparison_ineq(Binary("<=", node.left, node.right), numeric)
                ge = comparison_ineq(Binary(">=", node.left, node.right), numeric)
            except NonLinear:
                return OpaqueAtom(_ast_key(node))
            eq = And(LinAtom(le, _ineq_key(le)), LinAtom(ge, _ineq_key(ge)))
            return eq if node.op == "==" else Not(eq)
        return OpaqueAtom(_ast_key(node))
    if isinstance(node, Ident):
        return OpaqueAtom(f"id:{node.name}")
    return OpaqueAtom(_ast_key(node))


def conj(parts):
    """Fold a list of formulas into a conjunction (empty -> True)."""
    out = None
    for p in parts:
        out = p if out is None else And(out, p)
    return BoolConst(True) if out is None else out


def _collect_atoms(formula, acc):
    if isinstance(formula, (LinAtom, OpaqueAtom)):
        acc.setdefault(formula.key, formula)
    elif isinstance(formula, Not):
        _collect_atoms(formula.child, acc)
    elif isinstance(formula, (And, Or)):
        _collect_atoms(formula.left, acc)
        _collect_atoms(formula.right, acc)


def _eval(formula, assign) -> bool:
    if isinstance(formula, BoolConst):
        return formula.value
    if isinstance(formula, (LinAtom, OpaqueAtom)):
        return assign[formula.key]
    if isinstance(formula, Not):
        return not _eval(formula.child, assign)
    if isinstance(formula, And):
        return _eval(formula.left, assign) and _eval(formula.right, assign)
    if isinstance(formula, Or):
        return _eval(formula.left, assign) or _eval(formula.right, assign)
    raise TypeError(f"not a formula: {formula!r}")


def formula_sat(formula):
    """Decide satisfiability of a boolean formula over atoms.

    Returns True (satisfiable), False (unsatisfiable), or None (gave up: too many
    atoms to enumerate). Exact when all atoms are ``LinAtom``; a relaxation
    (sound only for the UNSAT direction) when ``OpaqueAtom``s are present.
    """
    acc = {}
    _collect_atoms(formula, acc)
    atoms = list(acc.values())
    if len(atoms) > _SAT_ATOM_CAP:
        return None
    for bits in range(1 << len(atoms)):
        assign = {a.key: bool((bits >> i) & 1) for i, a in enumerate(atoms)}
        if not _eval(formula, assign):
            continue
        lin = []
        for a in atoms:
            if isinstance(a, LinAtom):
                lin.append(a.ineq if assign[a.key] else negate(a.ineq))
        if is_satisfiable(lin):
            return True
    return False


def entailment_status(assume_formula, commitment_formula, int_vars):
    """Classify whether ``assume_formula`` entails ``commitment_formula``.

    Returns one of ``"holds"``, ``"fails"``, or ``"unknown"``. ``"fails"`` is
    reported only when the refutation is sound: the formula has no opaque atoms
    (so SAT is exact) and touches no integer-typed variable (so a real witness is
    a genuine counterexample rather than an integer-domain artefact).
    """
    formula = And(assume_formula, Not(commitment_formula))
    acc = {}
    _collect_atoms(formula, acc)
    atoms = list(acc.values())
    has_opaque = any(isinstance(a, OpaqueAtom) for a in atoms)
    touches_int = any(
        isinstance(a, LinAtom) and (set(a.ineq.coeffs) & int_vars) for a in atoms
    )
    sat = formula_sat(formula)
    if sat is False:
        return "holds"
    if sat is True and not has_opaque and not touches_int:
        return "fails"
    return "unknown"


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
    assumptions *entail* the commitment: ``assume => delegates``, i.e. that
    ``assume AND NOT delegates`` is unsatisfiable.

    Both sides may have arbitrary boolean structure (``&&``, ``||``, ``!``,
    ``==``, ``!=``) over linear atoms; satisfiability is decided by the SAT-modulo
    -linear-arithmetic procedure above. To stay sound, an entailment *failure* is
    reported only when the refutation is exact (see ``entailment_status``):
    commitments that hinge on opaque (non-linear / boolean) atoms or integer
    domains are left unverified rather than flagged.
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
            int_vars = {n for n, t in env.vars.items() if t == celcheck.INT}

            body = defs.get(f.get("delegates"))
            if body is None:
                continue  # delegates is a bare term / not a named commitment

            try:
                assume_parts = [
                    to_formula(celcheck.parse(expr), defs, numeric) for expr in assume
                ]
            except celcheck.CelError:
                continue  # L1 reports parse errors
            # Declared `range` bounds are always-true facts the parent may rely on.
            for ineq in _range_bounds(parent, numeric):
                assume_parts.append(LinAtom(ineq, _ineq_key(ineq)))

            assume_formula = conj(assume_parts)
            commitment_formula = to_formula(body, defs, numeric)

            if entailment_status(assume_formula, commitment_formula, int_vars) == "fails":
                errors.append(
                    f"{pid}: delegation of '{f['delegates']}' to "
                    f"{sorted(f.get('to', []))} is under-refined: assumed "
                    f"guarantees {list(assume)} do not entail '{f['delegates']}'"
                )


def check_system(charters, errors):
    """Run all L2 checks over a fully-loaded set of charters."""
    for c in charters:
        check_satisfiability(c, errors)
    check_inherited_provenance(charters, errors)
    check_delegation_reporting(charters, errors)
    check_delegation_entailment(charters, errors)
