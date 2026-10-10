from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import runtime_features
from scripts import ai_brain_trader
import ai_factor_trader as trader
from scripts import demo_scalp
from scripts import ledger_monitor
from scripts import okx_read_commands
from scripts.okx_runtime import OKXEnvironment
from okxquant_gateway import scheduler


class LightweightProducerTests(unittest.TestCase):
    def test_inline_factor_and_entry_producers_return_disabled_without_importing_writers(self):
        with patch("scripts.runtime_features.is_enabled", return_value=False):
            factor = ai_brain_trader._refresh_optional_factor_snapshot()
            entry = ai_brain_trader._record_optional_entry_research("acct", [], {}, "sig")
            scalp = demo_scalp.run_optional_scalp_research("acct", [], [], {})
        self.assertEqual(factor["status"], "disabled")
        self.assertEqual(entry["status"], "disabled")
        self.assertEqual(scalp["status"], "disabled")
        self.assertFalse(scalp["order_authorized"])

    def test_collectors_stop_before_market_io_when_disabled(self):
        from scripts import market_observations, factor_library
        with patch("scripts.runtime_features.is_enabled", return_value=False), \
             patch("scripts.instrument_pool.load_instruments") as instruments, \
             patch.object(factor_library.market, "begin_signal_frame") as begin:
            observations = market_observations.collect()
            factors = factor_library.update_factor_library()
        instruments.assert_not_called()
        begin.assert_not_called()
        self.assertEqual(observations["status"], "disabled")
        self.assertEqual(factors["status"], "disabled")

    def test_scheduler_disables_only_scheduled_research_and_review_jobs(self):
        # Bootstrap env applies only when no operator choice has been saved.
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime_features, "CONFIG_PATH", Path(directory) / "runtime_features.json"), \
             patch.dict(os.environ, {"OKXQUANT_RUNTIME_PROFILE": "light"}), \
             patch("scripts.okx_runtime._load_dotenv", return_value={"OKXQUANT_AUTOTRADE_ENABLED": "1"}), \
             patch.object(scheduler, "backup_job_specs", return_value=()):
            jobs = scheduler.current_jobs()
        names = {job.name for job in jobs}
        self.assertNotIn("factor_library", names)
        self.assertNotIn("market_observations", names)
        self.assertNotIn("self_improvement", names)
        self.assertIn("position_guard", names)
        self.assertIn("ledger_sync", names)
        self.assertIn("news", names)
        self.assertIn("trader", names)


class LightweightReconciliationTests(unittest.TestCase):
    SCOPE = "okx:demo:flat-proof"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.mkdir(exist_ok=True)
        self.evidence_path = self.root / "strategy_evidence.db"
        from scripts import strategy_evidence
        self.evidence_patch = patch.object(strategy_evidence, "DB_PATH", self.evidence_path)
        self.evidence_patch.start()
        self.addCleanup(self.evidence_patch.stop)
        self.data_patch = patch.object(ledger_monitor, "DATA", self.root)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.env_patch = patch("scripts.okx_runtime.selected_environment", return_value=type("Env", (), {"identity": self.SCOPE})())
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.temp.cleanup)
        with strategy_evidence.connection():
            pass

    def _state(self, *, ledger_rows=None, pending=1, pending_since=1000, handled="r1", status="ok"):
        ledger_monitor.atomic("ledger_sync_status.json", {
            "status": status, "pending_settlements": pending, "pending_since": pending_since,
            "handled_request": handled, "environment_id": self.SCOPE,
        })
        ledger_monitor.atomic("ledger_refresh_request.json", {"id": handled, "at": 900})
        ledger_monitor.atomic("trading_ledger.json", ledger_rows if ledger_rows is not None else [
            {"status": "closed_pending", "environment_id": self.SCOPE},
        ])

    def _insert_intent(self, scope, state, payload="not-json"):
        import sqlite3
        with sqlite3.connect(self.evidence_path) as db:
            db.execute("INSERT INTO intents VALUES (?,?,?,?,?,?,?)",
                       (f"intent-{state}-{scope}", scope, f"decision-{state}-{scope}", "BTC-USDT-SWAP",
                        state, 1500.0, payload))

    def test_old_pending_plus_new_holding_reconciles_within_one_minute(self):
        self._state(pending=8, ledger_rows=[
            *[{"status": "closed_pending", "environment_id": self.SCOPE} for _ in range(8)],
            {"status": "holding", "environment_id": self.SCOPE},
        ])
        self.assertTrue(ledger_monitor.should_run(1940, 2000))

    def test_old_pending_plus_unknown_acknowledged_or_pending_intent_reconciles_within_one_minute(self):
        for state in ("unknown", "acknowledged", "pending"):
            with self.subTest(state=state):
                self._state()
                self._insert_intent(self.SCOPE, state, payload="malformed payload is intentionally never parsed")
                self.assertTrue(ledger_monitor.should_run(1940, 2000))

    def test_old_pending_proven_flat_and_no_scoped_intents_backs_off(self):
        self._state(ledger_rows=[
            {"status": "closed_pending", "environment_id": self.SCOPE},
            {"status": "holding", "environment_id": "other-account"},
        ])
        self._insert_intent("different-account", "unknown", payload="{}")
        activity = ledger_monitor._LEDGER_ACTIVITY_CACHE.read(self.root / "trading_ledger.json")
        self.assertEqual(activity["scopes"][self.SCOPE]["closed_pending_count"], 1)
        self.assertEqual(activity["scopes"]["other-account"]["holding_count"], 1)
        self.assertFalse(ledger_monitor._has_unresolved_intents(self.SCOPE))
        self.assertFalse(ledger_monitor._active_reconciliation_state({"environment_id": self.SCOPE}, require_pending=True))
        self.assertFalse(ledger_monitor.should_run(1800, 2000))
        self.assertTrue(ledger_monitor.should_run(1700, 2000))

    def test_corrupt_or_missing_activity_or_evidence_cannot_extend_backoff(self):
        self._state()
        (self.root / "trading_ledger.json").write_text("{broken", encoding="utf-8")
        self.assertTrue(ledger_monitor.should_run(1940, 2000))
        self._state()
        self.evidence_path.unlink()
        for sidecar in (Path(str(self.evidence_path) + "-wal"), Path(str(self.evidence_path) + "-shm")):
            sidecar.unlink(missing_ok=True)
        self.assertTrue(ledger_monitor.should_run(1940, 2000))
        self.evidence_path.write_bytes(b"not a sqlite database")
        self.assertTrue(ledger_monitor.should_run(1940, 2000))

    def test_generation_cache_invalidates_when_a_scoped_intent_is_added(self):
        self._state()
        self.assertFalse(ledger_monitor.should_run(1800, 2000))
        self._insert_intent(self.SCOPE, "unknown", payload="not-json")
        self.assertTrue(ledger_monitor.should_run(1940, 2000))

    def test_malformed_settlement_metadata_uses_active_interval_not_backoff(self):
        self._state()
        ledger_monitor.atomic("ledger_sync_status.json", {
            "status": "ok", "pending_settlements": "many", "pending_since": 1000,
            "handled_request": "r1", "environment_id": self.SCOPE,
        })
        self.assertTrue(ledger_monitor.should_run(1940, 2000))

    def test_new_refresh_request_interrupts_settlement_backoff(self):
        self._state()
        ledger_monitor.atomic("ledger_refresh_request.json", {"id": "new", "at": 1995})
        self.assertTrue(ledger_monitor.should_run(1995, 2000))


class LightweightReadCommandTests(unittest.TestCase):
    def setUp(self):
        self.demo = OKXEnvironment("demo", "key", "secret", "pass")

    def test_allowlisted_static_reads_use_same_selected_environment(self):
        for command, resource in (
            ("okx --demo account balance --json", "balance"),
            ("okx --demo account positions --json", "positions"),
            ("okx --demo swap orders --json 2>/dev/null", "orders"),
        ):
            with self.subTest(resource=resource), patch("okxquant_backend.okx_read_service.read_private_resource", return_value=[]) as read:
                result = okx_read_commands.read_result(command, self.demo)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), [])
            read.assert_called_once_with(resource, environment=self.demo)

    def test_native_read_failure_is_not_empty_account_or_cli_retry(self):
        with patch("okxquant_backend.okx_read_service.read_private_resource", side_effect=OSError("offline")) as read:
            result = okx_read_commands.read_result("okx --demo account positions --json", self.demo)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("unavailable", result.stderr)
        read.assert_called_once_with("positions", environment=self.demo)

    def test_unknown_write_wrong_account_and_oauth_commands_keep_existing_path(self):
        self.assertIsNone(okx_read_commands.read_result("okx --demo swap cancel BTC-USDT-SWAP --ordId 1 --json", self.demo))
        self.assertNotEqual(okx_read_commands.read_result("okx --live account balance --json", self.demo).returncode, 0)
        oauth = OKXEnvironment("demo", "", "", "")
        self.assertIsNone(okx_read_commands.read_result("okx --demo account balance --json", oauth))
        with patch("okxquant_backend.okx_read_service.read_private_resource") as read:
            okx_read_commands.read_result("okx --demo swap amend --json", self.demo)
        read.assert_not_called()

    def test_trader_read_callers_consume_native_process_and_fail_closed(self):
        import subprocess
        from unittest.mock import Mock
        good = subprocess.CompletedProcess("okx --demo account positions --json", 0, '[{"instId":"BTC-USDT-SWAP","pos":"1"}]', "")
        bad = subprocess.CompletedProcess("okx --demo account positions --json", -1, "", "Native read unavailable")
        with patch.object(trader.market, "_selected", return_value=self.demo), \
             patch("okxquant_backend.account_connections.assert_current"), \
             patch("scripts.okx_read_commands.read_result", return_value=good) as native, \
             patch.object(trader.subprocess, "run") as cli:
            result = trader.run_cmd_result("okx --demo account positions --json")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"][0]["pos"], "1")
        native.assert_called_once()
        cli.assert_not_called()

        with patch.object(trader.market, "_selected", return_value=self.demo), \
             patch("okxquant_backend.account_connections.assert_current"), \
             patch("scripts.okx_read_commands.read_result", return_value=bad), \
             patch.object(trader.subprocess, "run") as cli:
            result = trader.run_cmd_result("okx --demo account positions --json")
            json_result = trader.run_json_cmd("okx --demo account balance --json")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])
        self.assertIsNone(json_result)
        cli.assert_not_called()

    def test_ai_brain_pending_order_path_consumes_completed_process_contract(self):
        expected = __import__("subprocess").CompletedProcess("okx --demo swap orders --json", 0, '[{"instId":"BTC-USDT-SWAP","ordId":"7"}]', "")
        with patch("scripts.okx_read_commands.read_result", return_value=expected) as native, \
             patch.object(ai_brain_trader.subprocess, "run") as cli:
            result = ai_brain_trader._read_pending_orders_command("okx --demo swap orders --json", self.demo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)[0]["ordId"], "7")
        native.assert_called_once()
        cli.assert_not_called()


class BrainOwnershipRegressionTests(unittest.TestCase):
    def test_execute_cycle_keeps_single_owner_lock(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "brain.lock"
            with patch.object(ai_brain_trader, "DATA_DIR", temporary), \
                 patch.object(ai_brain_trader, "AI_BRAIN_LOCK_FILE", str(lock_path)), \
                 patch.object(ai_brain_trader, "atomic_write_json"), \
                 patch.object(ai_brain_trader, "get_cpa_client_config") as config:
                with lock_path.open("a+") as owner:
                    fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        result = ai_brain_trader.execute_batch_ai_brain_cycle()
                    finally:
                        fcntl.flock(owner.fileno(), fcntl.LOCK_UN)
                self.assertIsNone(result)
                self.assertTrue(ai_brain_trader.get_last_inference_error())
                config.assert_not_called()

    def test_paired_prompt_metadata_hashes_exact_text_and_uses_original_scope(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            environment = OKXEnvironment("demo", "key", "secret", "pass")
            prompt_path = Path(temporary) / "ai_brain_last_prompt.txt"
            meta_path = Path(temporary) / "ai_brain_last_prompt_meta.json"
            with patch.object(ai_brain_trader, "DATA_DIR", temporary), \
                 patch.object(ai_brain_trader, "AI_LAST_PROMPT_FILE", str(prompt_path)), \
                 patch.object(ai_brain_trader, "assert_cycle_current") as validate:
                text = "frozen prompt \u4e2d\u6587"
                metadata = ai_brain_trader.publish_last_prompt_snapshot(text, environment, 123.0)
            validate.assert_called_once_with(environment, 123.0)
            self.assertEqual(prompt_path.read_bytes(), text.encode("utf-8"))
            saved = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["account_scope"], environment.identity)
            self.assertEqual(saved["environment_id"], environment.identity)
            self.assertEqual(saved["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
            self.assertEqual(metadata, saved)
            self.assertIsInstance(saved["generated_at"], (int, float))


if __name__ == "__main__":
    unittest.main()
