"""Account scope migration and concurrent baseline regressions (offline)."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from okxquant_backend import account_baseline as baseline, account_connections as center
from scripts.okx_runtime import OKXEnvironment

DEMO = "okx:demo:account:demo-A"
LIVE = "okx:live:account:live-A"
OTHER = "okx:demo:account:demo-B"


class BaselineScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "account_initial_state.json"
        for target, name, value in ((baseline, "BASELINE_FILE", self.path), (center, "DATA", self.root)):
            patcher = patch.object(target, name, value); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.dict("os.environ", {"INITIAL_CAPITAL": "", "INITIAL_CAPITAL_ACCOUNT_SCOPE": ""})
        patcher.start(); self.addCleanup(patcher.stop)

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_unowned_legacy_is_never_assigned_to_live_and_read_is_nonmutating(self):
        original = {"initial_capital": 4000, "reset_time": "2026-09-01 00:00:00", "total_trades": 7}
        self.write(original); before = self.path.read_bytes()
        live = baseline.load_account_baseline(LIVE)
        self.assertFalse(live["baseline_configured"])
        self.assertEqual(live["account_scope"], LIVE)
        self.assertEqual(live["reset_time"], "1970-01-01 00:00:00")
        self.assertNotIn("total_trades", live)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(baseline.load_account_baseline(DEMO)["initial_capital"], 4000)

    def test_explicit_legacy_live_scope_is_compatible(self):
        self.write({"initial_capital": 500, "account_scope": LIVE, "reset_time": "2026-01-01 00:00:00"})
        self.assertEqual(baseline.load_account_baseline(LIVE)["initial_capital"], 500)
        self.assertFalse(baseline.load_account_baseline(DEMO)["baseline_configured"])
        baseline.update_initial_capital(800, DEMO)
        self.assertEqual(baseline.load_account_baseline(LIVE)["initial_capital"], 500)

    def test_same_scope_update_keeps_history_and_original_legacy(self):
        original = {"initial_capital": 4000, "reset_time": "2026-09-01 00:00:00", "total_trades": 7}
        self.write(original)
        result = baseline.update_initial_capital(5000.125, DEMO)
        self.assertEqual(result["previous_initial_capital"], 4000)
        self.assertEqual(result["initial_capital"], 5000.12)
        self.assertEqual(result["total_trades"], 7)
        self.assertEqual(result["reset_time"], original["reset_time"])
        self.assertEqual(json.loads(self.path.read_text())["legacy_baseline"], original)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_demo_live_demo_roundtrip_retains_distinct_values(self):
        baseline.update_initial_capital(300, DEMO)
        baseline.update_initial_capital(8500, LIVE)
        baseline.update_initial_capital(700, OTHER)
        for scope, amount in ((DEMO, 300), (LIVE, 8500), (OTHER, 700)):
            value = baseline.load_account_baseline(scope)
            self.assertEqual(value["initial_capital"], amount)
            self.assertEqual(value["account_scope"], scope)

    def test_archive_migrates_outgoing_baseline_not_into_incoming_account(self):
        original = {"initial_capital": 300, "total_trades": 7}
        self.write(original)
        env = OKXEnvironment("demo", "D", "S", "P", account_scope=DEMO)
        with patch("scripts.okx_runtime.selected_environment", return_value=env):
            center._archive_runtime()
        self.assertTrue(self.path.exists())
        self.assertEqual(baseline.load_account_baseline(DEMO)["initial_capital"], 300)
        self.assertFalse(baseline.load_account_baseline(LIVE)["baseline_configured"])
        self.assertFalse(baseline.load_account_baseline(OTHER)["baseline_configured"])
        baseline.update_initial_capital(8000, LIVE)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["legacy_baseline"], original)
        self.assertNotIn("total_trades", saved)

    def test_environment_capital_requires_scope_for_live(self):
        with patch.dict("os.environ", {"INITIAL_CAPITAL": "999"}):
            self.assertTrue(baseline.load_account_baseline(DEMO)["baseline_configured"])
            self.assertFalse(baseline.load_account_baseline(LIVE)["baseline_configured"])
        with patch.dict("os.environ", {"INITIAL_CAPITAL": "999", "INITIAL_CAPITAL_ACCOUNT_SCOPE": LIVE}):
            self.assertEqual(baseline.load_account_baseline(LIVE)["initial_capital"], 999)
            self.assertFalse(baseline.load_account_baseline(DEMO)["baseline_configured"])

    def test_omitted_scope_resolves_current_identity_each_time(self):
        for mode, scope, amount in (("demo", DEMO, 300), ("live", LIVE, 8000)):
            env = OKXEnvironment(mode, "K", "S", "P", account_scope=scope)
            with patch("scripts.okx_runtime.selected_environment", return_value=env):
                baseline.update_initial_capital(amount)
                self.assertEqual(baseline.load_account_baseline()["account_scope"], scope)
                self.assertEqual(baseline.load_account_baseline()["initial_capital"], amount)

    def test_corrupt_document_is_not_overwritten(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError): baseline.update_initial_capital(500, DEMO)
        self.assertEqual(self.path.read_text(), "{broken")

    def test_invalid_amounts_do_not_write(self):
        for value in (0, -1, True, float("nan"), float("inf"), 1e20):
            with self.subTest(value=value), self.assertRaises(ValueError):
                baseline.update_initial_capital(value, DEMO)
        self.assertFalse(self.path.exists())

    def test_concurrent_scopes_are_not_lost(self):
        def write(i): baseline.update_initial_capital(100 + i, f"okx:demo:account:{i}")
        with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(write, range(16)))
        saved = json.loads(self.path.read_text())
        self.assertEqual(len(saved["baselines"]), 16)
        for i in range(16):
            self.assertEqual(baseline.load_account_baseline(f"okx:demo:account:{i}")["initial_capital"], 100+i)


    def test_explicitly_unconfigured_legacy_placeholder_is_not_claimed(self):
        self.write({"initial_capital":10000,"baseline_configured":False})
        self.assertFalse(baseline.load_account_baseline(DEMO)["baseline_configured"])
        self.assertFalse(baseline.load_account_baseline(LIVE)["baseline_configured"])
