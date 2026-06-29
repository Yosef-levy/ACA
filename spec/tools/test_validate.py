"""Integration tests for the validator across conformance levels.

These exercise validate.main() on the shipped examples, so they require the
runtime dependencies (pyyaml, jsonschema).

Run from anywhere:
    python3 -m unittest discover -s spec/tools
    python3 -m pytest spec/tools
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
EXAMPLES = os.path.join(REPO_ROOT, "examples")

sys.path.insert(0, HERE)

import validate  # noqa: E402


def run(*args):
    """Invoke the validator like the CLI; return its exit code."""
    return validate.main(["validate.py", *args])


class DroneMissionTests(unittest.TestCase):
    SYSTEM = os.path.join(EXAMPLES, "drone-mission", "system.aca.yaml")

    def test_passes_l0(self):
        self.assertEqual(run("--level", "L0", self.SYSTEM), 0)

    def test_passes_l1(self):
        self.assertEqual(run("--level", "L1", self.SYSTEM), 0)

    def test_passes_l2(self):
        self.assertEqual(run("--level", "L2", self.SYSTEM), 0)


class BrokenSensorTests(unittest.TestCase):
    CHARTER = os.path.join(EXAMPLES, "invalid", "broken-sensor.aca.yaml")

    def test_passes_l0(self):
        self.assertEqual(run("--level", "L0", self.CHARTER), 0)

    def test_fails_l1(self):
        self.assertEqual(run("--level", "L1", self.CHARTER), 1)


class OverBudgetTests(unittest.TestCase):
    SYSTEM = os.path.join(EXAMPLES, "invalid", "over-budget", "system.aca.yaml")

    def test_passes_l1(self):
        self.assertEqual(run("--level", "L1", self.SYSTEM), 0)

    def test_fails_l2(self):
        self.assertEqual(run("--level", "L2", self.SYSTEM), 1)


class DelegationRefinementTests(unittest.TestCase):
    SYSTEM = os.path.join(EXAMPLES, "delegation-refinement", "system.aca.yaml")

    def test_passes_l1(self):
        self.assertEqual(run("--level", "L1", self.SYSTEM), 0)

    def test_passes_l2(self):
        self.assertEqual(run("--level", "L2", self.SYSTEM), 0)


class UnderRefinedTests(unittest.TestCase):
    SYSTEM = os.path.join(EXAMPLES, "invalid", "under-refined", "system.aca.yaml")

    def test_passes_l1(self):
        self.assertEqual(run("--level", "L1", self.SYSTEM), 0)

    def test_fails_l2(self):
        self.assertEqual(run("--level", "L2", self.SYSTEM), 1)


class CliTests(unittest.TestCase):
    def test_unknown_level_rejected(self):
        self.assertEqual(run("--level", "L9", "anything.yaml"), 2)

    def test_no_paths_prints_usage(self):
        self.assertEqual(run(), 2)


if __name__ == "__main__":
    unittest.main()
