#!/usr/bin/env python3
"""Tests for the scorer, the re-grader and the consistency audit.

    cd scoring && python3 -m unittest -v test_scoring

Standard library only. The mutation tests copy the bundle to a temporary
directory, corrupt one value, and check that the audit names the problem.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import audit
from composite import business_from_ledger, composite, reliability_from_rates

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
TASKS = sorted(os.listdir(os.path.join(ROOT, "tasks")))


class ComposeTests(unittest.TestCase):
    def test_weights(self):
        self.assertAlmostEqual(composite(1.0, 1.0, 1.0), 1.0)
        self.assertAlmostEqual(composite(0.5, 0.5, 0.5), 0.5)
        self.assertAlmostEqual(composite(1.0, 0.0, 0.0), 0.7)
        self.assertAlmostEqual(composite(0.0, 1.0, 0.0), 0.2)
        self.assertAlmostEqual(composite(0.0, 0.0, 1.0), 0.1)

    def test_continuity_capped_at_one(self):
        self.assertAlmostEqual(composite(0.0, 0.0, 1.7), 0.1)

    def test_business_clipping(self):
        self.assertEqual(business_from_ledger(90.0, 100.0, 10.0), 0.0)
        self.assertEqual(business_from_ledger(130.0, 100.0, 10.0), 1.0)
        self.assertAlmostEqual(business_from_ledger(107.0, 100.0, 10.0), 0.7)

    def test_reliability_ignores_undefined_rates(self):
        self.assertAlmostEqual(reliability_from_rates(0.8, None), 0.8)
        self.assertAlmostEqual(reliability_from_rates(None, 0.4), 0.4)
        self.assertAlmostEqual(reliability_from_rates(0.8, 0.4), 0.6)
        self.assertEqual(reliability_from_rates(None, None), 0.0)


class ThresholdRuleTests(unittest.TestCase):
    def test_exact_tie_sits_one_margin_under(self):
        self.assertAlmostEqual(audit.threshold_from_anchors(0.73, 0.73), 0.72)

    def test_near_tie_sits_under_the_lower_anchor(self):
        self.assertAlmostEqual(audit.threshold_from_anchors(0.7066, 0.7202), 0.6966)

    def test_separated_anchors_use_rounded_midpoint(self):
        self.assertAlmostEqual(audit.threshold_from_anchors(0.678, 0.7122), 0.70)
        self.assertAlmostEqual(audit.threshold_from_anchors(0.705, 0.6428), 0.67)

    def test_order_of_anchors_does_not_matter(self):
        self.assertEqual(audit.threshold_from_anchors(0.6, 0.7), audit.threshold_from_anchors(0.7, 0.6))


class TomlReaderTests(unittest.TestCase):
    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11+")
    def test_fallback_reader_matches_tomllib(self):
        import builtins
        import tomllib
        real_import = builtins.__import__

        def no_tomllib(name, *args, **kwargs):
            if name == "tomllib":
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        for task in TASKS:
            path = os.path.join(ROOT, "tasks", task, "task.toml")
            with open(path, encoding="utf-8") as f:
                data = tomllib.loads(f.read())
            expected = {k: data.get(k, {}) for k in ("shift", "setup", "economics")}
            builtins.__import__ = no_tomllib
            try:
                got = audit.load_task_toml(path)
            finally:
                builtins.__import__ = real_import
            self.assertEqual(got, expected, task)


class ReleasedBundleTests(unittest.TestCase):
    def test_audit_passes_on_release(self):
        a, margins = audit.run(ROOT)
        self.assertEqual(a.failures, [])
        self.assertEqual(len(margins), 15)
        self.assertGreater(a.checks, 300)

    def test_regrade_reproduces_release(self):
        out = subprocess.run([sys.executable, "regrade.py"], cwd=HERE, check=False, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("10/10 trajectories reproduced", out.stdout)


class MutationTests(unittest.TestCase):
    """Corrupt one value in a copy of the bundle; the audit must flag it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "bundle")
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__", "assets"))
        self.cal_path = os.path.join(self.root, "scoring", "calibration.train.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit_json(self, path, fn):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        fn(data)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)

    def failures(self):
        a, _ = audit.run(self.root)
        return a.failures

    def assertFlags(self, needle):
        fails = self.failures()
        self.assertTrue(any(needle in f for f in fails), f"expected {needle!r} in {fails}")

    def test_scale_drift(self):
        self.edit_json(self.cal_path, lambda d: d["tasks"]["cash-crunch"]["7"].update(business_scale=1900.0))
        self.assertFlags("business_scale")

    def test_zero_drift(self):
        self.edit_json(self.cal_path, lambda d: d["tasks"]["season-flip"]["11"].update(business_zero_raw=15000.0))
        self.assertFlags("business_zero_raw")

    def test_wrong_oracle_label(self):
        self.edit_json(self.cal_path, lambda d: d["tasks"]["final-clearance"]["11"].update(oracle="rule-based"))
        self.assertFlags("oracle")

    def test_threshold_drift(self):
        self.edit_json(self.cal_path, lambda d: d["tasks"]["short-runway"]["13"].update(threshold=0.65))
        self.assertFlags("threshold 0.65")

    def test_anchor_composite_drift(self):
        def f(d):
            d["tasks"]["counter-punch"]["13"]["anchors"]["blast-list"]["reward_composite"] = 0.6
        self.edit_json(self.cal_path, f)
        self.assertFlags("anchor blast-list")

    def test_exploiter_above_honest_anchor(self):
        def f(d):
            x = d["tasks"]["cash-crunch"]["11"]["anchors"]["exploiter"]
            x.update(reliability=1.0, continuity=1.0, reward_composite=0.3)
        self.edit_json(self.cal_path, f)
        self.assertFlags("gate (iv)")

    def test_reliability_not_mean_of_rates(self):
        p = os.path.join(self.root, "trajectories", "season-flip__s7__McTjrWu", "score.json")
        self.edit_json(p, lambda d: d.update(reliability=0.9))
        self.assertFlags("reliability 0.9")

    def test_opening_capital_mismatch(self):
        p = os.path.join(self.root, "trajectories", "cash-crunch__s7__HJR93q3", "score.json")
        self.edit_json(p, lambda d: d.update(net_assets=d["net_assets"] + 500))
        self.assertFlags("opening capital")

    def test_windows_played_mismatch(self):
        p = os.path.join(self.root, "trajectories", "counter-punch__s7__yEBzNwi", "score.json")
        self.edit_json(p, lambda d: d.update(windows_played=30.0))
        self.assertFlags("windows_played")

    def test_index_disagrees_with_score(self):
        p = os.path.join(self.root, "trajectories", "index.json")
        self.edit_json(p, lambda d: d[0].update(reward_composite=0.99))
        self.assertFlags("index.json")

    def test_cli_exit_code(self):
        self.edit_json(self.cal_path, lambda d: d["tasks"]["cash-crunch"]["7"].update(business_scale=1900.0))
        out = subprocess.run([sys.executable, os.path.join(HERE, "audit.py"), self.root],
                             check=False, capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("FAIL", out.stdout)


if __name__ == "__main__":
    unittest.main()
