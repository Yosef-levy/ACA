"""Tests for the L2 compositional checker (l2check.py).

Run from anywhere:
    python3 -m unittest discover -s spec/tools
    python3 -m pytest spec/tools
"""
import os
import sys
import unittest
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import l2check  # noqa: E402


def ineq(coeffs, const, strict=False):
    return l2check.Ineq({k: Fraction(v) for k, v in coeffs.items()}, Fraction(const), strict)


class SatisfiabilityEngineTests(unittest.TestCase):
    def test_empty_system_is_satisfiable(self):
        self.assertTrue(l2check.is_satisfiable([]))

    def test_single_bound_satisfiable(self):
        # x - 100 <= 0
        self.assertTrue(l2check.is_satisfiable([ineq({"x": 1}, -100)]))

    def test_contradictory_bounds_unsatisfiable(self):
        # x >= 200  -> 200 - x <= 0  ->  -x + 200 <= 0
        # x <= 100  -> x - 100 <= 0
        sys_ = [ineq({"x": -1}, 200), ineq({"x": 1}, -100)]
        self.assertFalse(l2check.is_satisfiable(sys_))

    def test_strict_self_contradiction(self):
        # x < x  ->  x - x < 0  ->  0 < 0  (false)
        self.assertFalse(l2check.is_satisfiable([ineq({}, 0, strict=True)]))

    def test_two_variable_chain_unsat(self):
        # x <= y, y <= 5, x >= 10  -> infeasible
        sys_ = [
            ineq({"x": 1, "y": -1}, 0),   # x - y <= 0
            ineq({"y": 1}, -5),           # y - 5 <= 0
            ineq({"x": -1}, 10),          # 10 - x <= 0
        ]
        self.assertFalse(l2check.is_satisfiable(sys_))

    def test_two_variable_chain_sat(self):
        sys_ = [
            ineq({"x": 1, "y": -1}, 0),   # x <= y
            ineq({"y": 1}, -5),           # y <= 5
            ineq({"x": -1}, 1),           # x >= 1
        ]
        self.assertTrue(l2check.is_satisfiable(sys_))


class AtomExtractionTests(unittest.TestCase):
    def setUp(self):
        self.extractor = l2check.AtomExtractor(defs={}, numeric_vars={"x", "y"})

    def _extract(self, expr):
        out = []
        self.extractor.extract(l2check.celcheck.parse(expr), out)
        return out

    def test_conjunction_splits(self):
        out = self._extract("x <= 10.0 && y >= 2.0")
        self.assertEqual(len(out), 2)

    def test_disjunction_dropped(self):
        self.assertEqual(self._extract("x <= 10.0 || y >= 2.0"), [])

    def test_equality_becomes_two_inequalities(self):
        out = self._extract("x == 5.0")
        self.assertEqual(len(out), 2)

    def test_negation_flips_comparison(self):
        out = self._extract("!(x <= 10.0)")  # becomes x > 10.0
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].strict)

    def test_nonlinear_dropped(self):
        self.assertEqual(self._extract("x * y <= 10.0"), [])

    def test_opaque_identifier_dropped(self):
        # `z` is not numeric -> the atom cannot be linearised -> dropped.
        self.assertEqual(self._extract("z <= 10.0"), [])


class FormulaSatTests(unittest.TestCase):
    def _f(self, expr, numeric=("x", "y")):
        return l2check.to_formula(l2check.celcheck.parse(expr), {}, set(numeric))

    def test_linear_conjunction_unsat(self):
        self.assertIs(l2check.formula_sat(self._f("x >= 2.0 && x <= 1.0")), False)

    def test_disjunction_sat(self):
        self.assertIs(l2check.formula_sat(self._f("x >= 2.0 || x <= 1.0")), True)

    def test_negation_of_conjunction(self):
        # !(x <= 1 && x >= 0)  is satisfiable (e.g. x = 5)
        self.assertIs(l2check.formula_sat(self._f("!(x <= 1.0 && x >= 0.0)")), True)

    def test_opaque_atom_relaxed_to_free_bool(self):
        # size(x) is opaque; the comparison is treated as a free boolean -> SAT.
        self.assertIs(l2check.formula_sat(self._f("size(x) <= 0")), True)

    def test_equality_unsat_with_conflicting_bound(self):
        # x == 0 && x >= 1  is unsatisfiable
        self.assertIs(l2check.formula_sat(self._f("x == 0.0 && x >= 1.0")), False)

    def test_entailment_holds_and_fails(self):
        tight = self._f("x <= 0.1")
        loose = self._f("x <= 0.2")
        self.assertEqual(l2check.entailment_status(tight, loose, set()), "holds")
        self.assertEqual(l2check.entailment_status(loose, tight, set()), "fails")


def _charter(cid, terms=None, constraints=None, delegation=None, exposure=None,
             success=None):
    return {
        "aca_version": "0.1",
        "kind": "Charter",
        "object": {"id": cid, "role": cid},
        "representation": {"terms": terms or [], "success": success or []},
        "constraints": constraints or [],
        "delegation": delegation or [],
        "exposure": exposure or [],
    }


class CheckSatisfiabilityTests(unittest.TestCase):
    def test_jointly_unsatisfiable_hard_constraints(self):
        charter = _charter(
            "c",
            terms=[{"name": "reserve", "type": "real"}],
            constraints=[
                {"id": "floor", "expr": "reserve >= 200.0", "scope": "local",
                 "owner": "c", "severity": "hard"},
                {"id": "cap", "expr": "reserve <= 100.0", "scope": "local",
                 "owner": "c", "severity": "hard"},
            ],
        )
        errors = []
        l2check.check_satisfiability(charter, errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("unsatisfiable", errors[0])

    def test_soft_constraints_are_ignored(self):
        charter = _charter(
            "c",
            terms=[{"name": "reserve", "type": "real"}],
            constraints=[
                {"id": "floor", "expr": "reserve >= 200.0", "scope": "local",
                 "owner": "c", "severity": "hard"},
                {"id": "cap", "expr": "reserve <= 100.0", "scope": "local",
                 "owner": "c", "severity": "soft"},
            ],
        )
        errors = []
        l2check.check_satisfiability(charter, errors)
        self.assertEqual(errors, [])

    def test_range_bounds_create_contradiction(self):
        # range [0, 1] plus a hard "x >= 2" is unsatisfiable.
        charter = _charter(
            "c",
            terms=[{"name": "x", "type": "real", "range": [0, 1]}],
            constraints=[
                {"id": "hi", "expr": "x >= 2.0", "scope": "local",
                 "owner": "c", "severity": "hard"},
            ],
        )
        errors = []
        l2check.check_satisfiability(charter, errors)
        self.assertEqual(len(errors), 1)

    def test_opaque_constraints_do_not_false_positive(self):
        # A boolean predicate over a duration is not modelled; no false UNSAT.
        charter = _charter(
            "c",
            terms=[{"name": "eta", "type": "duration"}],
            constraints=[
                {"id": "ontime", "expr": "eta <= eta", "scope": "local",
                 "owner": "c", "severity": "hard"},
            ],
        )
        errors = []
        l2check.check_satisfiability(charter, errors)
        self.assertEqual(errors, [])


class InheritedProvenanceTests(unittest.TestCase):
    def test_inherited_without_delegation_edge_fails(self):
        parent = _charter("parent")  # no delegation to child
        child = _charter(
            "child",
            constraints=[
                {"id": "ic", "expr": "x <= 1.0", "scope": "inherited",
                 "owner": "parent", "severity": "hard"},
            ],
        )
        errors = []
        l2check.check_inherited_provenance([parent, child], errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("no delegation edge", errors[0])

    def test_inherited_with_delegation_edge_ok(self):
        parent = _charter(
            "parent",
            delegation=[{"delegates": "g", "to": ["child"], "objective": "x"}],
        )
        child = _charter(
            "child",
            constraints=[
                {"id": "ic", "expr": "x <= 1.0", "scope": "inherited",
                 "owner": "parent", "severity": "hard"},
            ],
        )
        errors = []
        l2check.check_inherited_provenance([parent, child], errors)
        self.assertEqual(errors, [])

    def test_self_owned_inherited_is_flagged(self):
        child = _charter(
            "child",
            constraints=[
                {"id": "ic", "expr": "x <= 1.0", "scope": "inherited",
                 "owner": "child", "severity": "hard"},
            ],
        )
        errors = []
        l2check.check_inherited_provenance([child], errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("owned by the object itself", errors[0])


class DelegationReportingTests(unittest.TestCase):
    def test_child_exposes_all_reported_symbols(self):
        parent = _charter(
            "parent",
            delegation=[{"delegates": "g", "to": ["child"], "objective": "x",
                         "reporting": ["a", "b"]}],
        )
        child = _charter(
            "child",
            exposure=[{"to": "parent", "view": ["a", "b"], "trigger": "on_change"}],
        )
        errors = []
        l2check.check_delegation_reporting([parent, child], errors)
        self.assertEqual(errors, [])

    def test_missing_reported_symbol_fails(self):
        parent = _charter(
            "parent",
            delegation=[{"delegates": "g", "to": ["child"], "objective": "x",
                         "reporting": ["a", "b"]}],
        )
        child = _charter(
            "child",
            exposure=[{"to": "parent", "view": ["a"], "trigger": "on_change"}],
        )
        errors = []
        l2check.check_delegation_reporting([parent, child], errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("'b'", errors[0])

    def test_exposure_to_other_target_does_not_count(self):
        parent = _charter(
            "parent",
            delegation=[{"delegates": "g", "to": ["child"], "objective": "x",
                         "reporting": ["a"]}],
        )
        child = _charter(
            "child",
            exposure=[{"to": "someone-else", "view": ["a"], "trigger": "on_change"}],
        )
        errors = []
        l2check.check_delegation_reporting([parent, child], errors)
        self.assertEqual(len(errors), 1)

    def test_no_reporting_clause_is_ok(self):
        parent = _charter(
            "parent",
            delegation=[{"delegates": "g", "to": ["child"], "objective": "x"}],
        )
        child = _charter("child")
        errors = []
        l2check.check_delegation_reporting([parent, child], errors)
        self.assertEqual(errors, [])


class NegateTests(unittest.TestCase):
    def test_negate_non_strict_becomes_strict(self):
        # x - 5 <= 0  negated  ->  -(x - 5) < 0
        n = l2check.negate(ineq({"x": 1}, -5, strict=False))
        self.assertEqual(n.coeffs["x"], Fraction(-1))
        self.assertEqual(n.const, Fraction(5))
        self.assertTrue(n.strict)

    def test_double_negation_is_identity(self):
        i = ineq({"x": 2}, -3, strict=True)
        nn = l2check.negate(l2check.negate(i))
        self.assertEqual(nn.coeffs["x"], i.coeffs["x"])
        self.assertEqual(nn.const, i.const)
        self.assertEqual(nn.strict, i.strict)


def _delegating_parent(assume, delegates="hazard_ok", commitment="hazard <= 0.2"):
    return _charter(
        "parent",
        terms=[{"name": "hazard", "type": "real", "range": [0, 1]}],
        success=[{"name": delegates, "expr": commitment}],
        delegation=[{
            "delegates": delegates, "to": ["child"], "objective": "x",
            "reporting": ["hazard"], "refinement": {"assume": assume},
        }],
    )


class DelegationEntailmentTests(unittest.TestCase):
    def test_assumption_entails_commitment(self):
        parent = _delegating_parent(["hazard <= 0.1"])
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(errors, [])

    def test_assumption_too_weak_fails(self):
        parent = _delegating_parent(["hazard <= 0.3"])
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("under-refined", errors[0])

    def test_no_refinement_is_skipped(self):
        parent = _charter(
            "parent",
            terms=[{"name": "hazard", "type": "real"}],
            success=[{"name": "hazard_ok", "expr": "hazard <= 0.2"}],
            delegation=[{"delegates": "hazard_ok", "to": ["child"], "objective": "x"}],
        )
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(errors, [])

    def test_conjunctive_assumption_entails(self):
        parent = _delegating_parent(["hazard <= 0.15 && hazard >= 0.0"])
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(errors, [])

    def test_disjunctive_assumption_too_weak_fails(self):
        # h <= 0.3 || h <= 0.05  is just  h <= 0.3, which does not entail h <= 0.2.
        parent = _delegating_parent(["hazard <= 0.3 || hazard <= 0.05"])
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(len(errors), 1)

    def test_disjunctive_commitment_entailed(self):
        # h <= 0.1 entails (h <= 0.5 || h >= 2.0) via the first disjunct.
        parent = _delegating_parent(
            ["hazard <= 0.1"], commitment="hazard <= 0.5 || hazard >= 2.0")
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(errors, [])

    def test_equality_commitment_failure(self):
        # h <= 0.1 does not pin h == 0.0, so entailment fails (and is exact).
        parent = _delegating_parent(
            ["hazard <= 0.1"], commitment="hazard == 0.0")
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(len(errors), 1)

    def test_opaque_commitment_not_flagged(self):
        # Commitment over a bare boolean term is opaque -> cannot be refuted.
        parent = _charter(
            "parent",
            terms=[{"name": "flag", "type": "bool"},
                   {"name": "hazard", "type": "real"}],
            success=[{"name": "done", "expr": "flag"}],
            delegation=[{"delegates": "done", "to": ["child"], "objective": "x",
                         "reporting": ["hazard"],
                         "refinement": {"assume": ["hazard <= 0.1"]}}],
        )
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(errors, [])

    def test_integer_domain_not_flagged(self):
        # Over reals 2n <= 1 does not entail n <= 0 (n = 0.3), but over the
        # integers it does. The int gate must suppress this false positive.
        parent = _charter(
            "parent",
            terms=[{"name": "n", "type": "int"}],
            success=[{"name": "bound", "expr": "n <= 0"}],
            delegation=[{"delegates": "bound", "to": ["child"], "objective": "x",
                         "reporting": ["n"],
                         "refinement": {"assume": ["2 * n <= 1"]}}],
        )
        errors = []
        l2check.check_delegation_entailment([parent], errors)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
