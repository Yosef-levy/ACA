"""Tests for the L1 CEL subset checker (celcheck.py).

Run from anywhere:
    python3 -m unittest discover -s spec/tools
    python3 -m pytest spec/tools
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import celcheck  # noqa: E402


def env_from_terms(*terms, states=None):
    """Build an Env from inline term dicts (mirrors a charter's R)."""
    charter = {"representation": {"terms": list(terms)}}
    if states:
        charter["representation"]["states"] = [{"name": s} for s in states]
    return celcheck.build_env(charter)


class TokenizeParseTests(unittest.TestCase):
    def test_tokenize_operators_and_numbers(self):
        toks = celcheck.tokenize("a >= 0.5 && b != 3")
        kinds = [(t.kind, t.value) for t in toks]
        self.assertIn(("op", ">="), kinds)
        self.assertIn(("op", "&&"), kinds)
        self.assertIn(("num", "0.5"), kinds)

    def test_unterminated_string_raises(self):
        with self.assertRaises(celcheck.CelError):
            celcheck.tokenize("x == 'oops")

    def test_parse_trailing_tokens_raises(self):
        with self.assertRaises(celcheck.CelError):
            celcheck.parse("a b")

    def test_parse_precedence(self):
        ast = celcheck.parse("a || b && c")
        # || is lowest precedence, so the root is the || node.
        self.assertIsInstance(ast, celcheck.Binary)
        self.assertEqual(ast.op, "||")
        self.assertEqual(ast.right.op, "&&")


class TypeCheckTests(unittest.TestCase):
    def test_undeclared_identifier(self):
        env = env_from_terms({"name": "x", "type": "real"})
        errs = celcheck.check_expr("x <= y", env)
        self.assertTrue(any("not declared in R" in e for e in errs))

    def test_bool_operator_on_number(self):
        env = env_from_terms({"name": "x", "type": "real"})
        errs = celcheck.check_expr("x && true", env)
        self.assertTrue(any("expects bool" in e for e in errs))

    def test_incomparable_types(self):
        env = env_from_terms(
            {"name": "x", "type": "real"},
            {"name": "s", "type": "string"},
        )
        errs = celcheck.check_expr("x < s", env)
        self.assertTrue(any("order-compare" in e for e in errs))

    def test_numeric_comparison_ok(self):
        env = env_from_terms(
            {"name": "x", "type": "real"},
            {"name": "y", "type": "int"},
        )
        self.assertEqual(celcheck.check_expr("x <= y + 1", env), [])

    def test_state_literal_must_be_declared(self):
        env = env_from_terms({"name": "x", "type": "real"}, states=["planning"])
        ok = celcheck.check_expr("state == 'planning'", env)
        bad = celcheck.check_expr("state == 'flying'", env)
        self.assertEqual(ok, [])
        self.assertTrue(any("state literal" in e for e in bad))

    def test_enum_literal_must_be_in_values(self):
        env = env_from_terms({"name": "lbl", "type": "enum", "values": ["a", "b"]})
        self.assertEqual(celcheck.check_expr("lbl == 'a'", env), [])
        self.assertTrue(any("enum literal" in e for e in celcheck.check_expr("lbl == 'z'", env)))

    def test_abstract_type_is_opaque(self):
        # Operations on namespaced/abstract types must not raise false positives.
        env = env_from_terms({"name": "region", "type": "geo.region"})
        self.assertEqual(celcheck.check_expr("region == region", env), [])

    def test_known_function_return_type(self):
        env = env_from_terms(
            {"name": "eta", "type": "duration"},
            {"name": "deadline", "type": "timestamp"},
        )
        self.assertEqual(celcheck.check_expr("eta <= duration(deadline - now)", env), [])

    def test_parse_error_reported(self):
        env = env_from_terms({"name": "x", "type": "real"})
        errs = celcheck.check_expr("x <=", env)
        self.assertTrue(any("parse error" in e for e in errs))


class BuildEnvTests(unittest.TestCase):
    def test_now_is_timestamp(self):
        env = celcheck.build_env({"representation": {}})
        self.assertEqual(env.vars.get("now"), celcheck.TIMESTAMP)

    def test_predicates_and_success_are_bool(self):
        charter = {
            "representation": {
                "terms": [{"name": "x", "type": "real"}],
                "predicates": [{"name": "p", "expr": "x > 0"}],
                "success": [{"name": "ok", "expr": "p"}],
            }
        }
        env = celcheck.build_env(charter)
        self.assertEqual(env.vars["p"], celcheck.BOOL)
        self.assertEqual(env.vars["ok"], celcheck.BOOL)

    def test_states_register_state_var(self):
        env = env_from_terms({"name": "x", "type": "real"}, states=["a", "b"])
        self.assertEqual(env.vars["state"], celcheck.STRING)
        self.assertEqual(env.states, {"a", "b"})


if __name__ == "__main__":
    unittest.main()
