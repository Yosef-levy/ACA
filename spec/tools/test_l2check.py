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


def _charter(cid, terms=None, constraints=None, delegation=None):
    return {
        "aca_version": "0.1",
        "kind": "Charter",
        "object": {"id": cid, "role": cid},
        "representation": {"terms": terms or []},
        "constraints": constraints or [],
        "delegation": delegation or [],
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


if __name__ == "__main__":
    unittest.main()
