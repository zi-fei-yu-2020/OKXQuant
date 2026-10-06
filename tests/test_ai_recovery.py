"""Fail-closed tests for delayed recovery after pre-inference CPU overload."""
from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timedelta
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from okxquant_backend.llm_transport import LLMRequestError, public_failure
from okxquant_gateway import ai_recovery
from okxquant_gateway.scheduler import GatewayScheduler, JOBS, BJ_TZ
from okxquant_gateway.store import GatewayStore
import okxquant_gateway.scheduler as scheduler_module
import okxquant_gateway.telemetry as telemetry


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        future = Future()
        self.calls.append((function, args, future))
        return future


class FixedClock(datetime):
    current = datetime(2026, 10, 6, 13, 31, 0, tzinfo=BJ_TZ)

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


class AIRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "gateway.db"
        self.store = GatewayStore(self.db)
        self.slot = "2026-10-06 13:30:00"
        self.expires = "2026-10-06 13:41:00"

    def failure(self, status=503, category="http_error", provider="system_cpu_overloaded"):
        return public_failure(LLMRequestError(status, 1, category, provider))

    def enqueue(self, scheduled="2026-10-06 13:31:00"):
        return self.store.enqueue_ai_recovery(
            account_scope="demo:fixture", slot_start=self.slot,
            scheduled_at=scheduled, expires_at=self.expires,
            failure_fingerprint="fixture", original_model_call_id=None,
        )

    def model_record(self, caller, status, started_at):
        return {
            "caller": caller, "model": "fixture", "reasoning_effort": "high",
            "status": status, "started_at": started_at, "duration_ms": 1,
            "input_chars": 1, "output_chars": 1, "prompt_fingerprint": "fixture",
            "prompt_transport": "python-direct", "input_tokens": None,
            "output_tokens": None, "total_tokens": None, "error_type": "",
        }

    def test_only_fast_exact_cpu_overload_is_eligible(self):
        self.assertTrue(ai_recovery.eligible_failure(self.failure(), 9999))
        self.assertFalse(ai_recovery.eligible_failure(self.failure(), 10000))
        self.assertFalse(ai_recovery.eligible_failure(self.failure(status=500), 100))
        self.assertFalse(ai_recovery.eligible_failure(self.failure(category="request_timeout", status=0, provider=""), 100))
        self.assertFalse(ai_recovery.eligible_failure(self.failure(provider="system_memory_overloaded"), 100))
        empty = self.failure(status=200, category="empty_model_output", provider="")
        self.assertTrue(ai_recovery.eligible_failure(empty, 89_999))
        self.assertFalse(ai_recovery.eligible_failure(empty, 90_000))

    def test_schedule_is_durable_and_idempotent_per_account_slot(self):
        context = {"account_scope": "demo:fixture", "slot_start": self.slot}
        with patch.dict(os.environ, {"OKXQUANT_GATEWAY_DB": str(self.db)}, clear=False), \
             patch.object(ai_recovery, "datetime", FixedClock):
            first = ai_recovery.schedule(failure=self.failure(), duration_ms=800,
                recovery_context=context, original_model_call_id=None, delay_seconds=45)
            second = ai_recovery.schedule(failure=self.failure(), duration_ms=900,
                recovery_context=context, original_model_call_id=None, delay_seconds=90)
        self.assertIsNotNone(first)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.ai_recoveries()), 1)
        self.assertEqual(self.store.ai_recoveries()[0]["attempts"], 0)

    def test_telemetry_schedules_normal_failure_but_never_recovery_failure(self):
        error = LLMRequestError(503, 1, "http_error", "system_cpu_overloaded")
        with patch.object(telemetry, "DB_PATH", self.db), \
             patch("okxquant_gateway.ai_recovery.schedule") as schedule:
            normal = telemetry.ModelCallTelemetry("trading_brain", "m", "high", "s", "u",
                recovery_context={"account_scope": "demo", "slot_start": self.slot})
            normal.finish("failed", error=error)
            recovery = telemetry.ModelCallTelemetry("trading_brain_recovery", "m", "high", "s", "u",
                recovery_context={"account_scope": "demo", "slot_start": self.slot})
            recovery.finish("failed", error=error)
        schedule.assert_called_once()
        self.assertEqual(len(self.store.model_calls()), 2)

    def test_scheduler_launches_one_recovery_with_fresh_trader_process(self):
        recovery = self.enqueue()
        scheduler = GatewayScheduler(self.store)
        self.addCleanup(scheduler.shutdown)
        executor = RecordingExecutor()
        trader = next(job for job in JOBS if job.name == "trader")
        now = datetime(2026, 10, 6, 13, 32, 0, tzinfo=BJ_TZ)
        with patch.object(scheduler, "trader_executor", executor), \
             patch.object(scheduler_module, "current_jobs", return_value=(trader,)), \
             patch.object(scheduler_module, "load_schedule", return_value={}):
            launched = scheduler.tick(now)
        self.assertEqual(launched, ["trader_recovery"])
        self.assertEqual(len(executor.calls), 1)

        function, args, _ = executor.calls[0]
        captured = {}

        def process(command, *, timeout, env_overrides=None):
            captured.update(env_overrides or {})
            row = self.store.ai_recovery(int(recovery["id"]))
            call_id = self.store.record_model_call(self.model_record(
                "trading_brain_recovery", "success", row["started_at"]
            ))
            self.store.set_state(f"ai_recovery.model_success.{recovery['id']}", str(call_id))
            return subprocess.CompletedProcess(command, 0, "fresh cycle complete", "")

        with patch.object(scheduler_module, "current_jobs", return_value=(trader,)), \
             patch.object(scheduler_module, "_run_process", side_effect=process) as run:
            function(*args)
        row = self.store.ai_recovery(int(recovery["id"]))
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(captured["OKXQUANT_AI_RECOVERY"], "1")
        self.assertEqual(captured["OKXQUANT_RECOVERY_ACCOUNT_SCOPE"], "demo:fixture")
        run.assert_called_once()

    def test_recovery_without_successful_model_call_fails_closed(self):
        recovery = self.enqueue()
        scheduler = GatewayScheduler(self.store)
        self.addCleanup(scheduler.shutdown)
        trader = next(job for job in JOBS if job.name == "trader")
        with patch.object(scheduler_module, "current_jobs", return_value=(trader,)), \
             patch.object(scheduler_module, "_run_process", return_value=subprocess.CompletedProcess([], 0, "no model", "")):
            scheduler._execute_recovery(recovery)
        row = self.store.ai_recovery(int(recovery["id"]))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(self.store.job_runs()[0]["return_code"], 1)

    def test_old_slot_or_existing_success_cancels_before_launch(self):
        trader = next(job for job in JOBS if job.name == "trader")
        for existing_success in (False, True):
            with self.subTest(existing_success=existing_success):
                db = Path(self.temp.name) / f"cancel-{existing_success}.db"
                store = GatewayStore(db)
                recovery = store.enqueue_ai_recovery(
                    account_scope="demo", slot_start=self.slot,
                    scheduled_at="2026-10-06 13:31:00", expires_at=self.expires,
                    failure_fingerprint="fixture", original_model_call_id=None,
                )
                if existing_success:
                    store.record_model_call(self.model_record("trading_brain", "success", "2026-10-06 13:30:30"))
                    now = datetime(2026, 10, 6, 13, 32, tzinfo=BJ_TZ)
                else:
                    now = datetime(2026, 10, 6, 13, 45, tzinfo=BJ_TZ)
                scheduler = GatewayScheduler(store)
                executor = RecordingExecutor()
                try:
                    with patch.object(scheduler, "trader_executor", executor), \
                         patch.object(scheduler_module, "current_jobs", return_value=(trader,)), \
                         patch.object(scheduler_module, "load_schedule", return_value={}):
                        scheduler.tick(now)
                    self.assertEqual(store.ai_recovery(int(recovery["id"]))["status"], "cancelled")
                    self.assertFalse(any(call[0] == scheduler._execute_recovery for call in executor.calls))
                finally:
                    scheduler.shutdown()

    def test_restart_never_replays_unknown_running_recovery(self):
        recovery = self.enqueue()
        self.store.claim_ai_recovery(int(recovery["id"]))
        self.store.recover_processing()
        row = self.store.ai_recovery(int(recovery["id"]))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(self.store.job_runs()[0]["return_code"], 125)

    def test_authorization_requires_claimed_row_and_exact_scope_slot(self):
        recovery = self.enqueue()
        claimed = self.store.claim_ai_recovery(int(recovery["id"]))
        env = {
            "OKXQUANT_GATEWAY_DB": str(self.db),
            "OKXQUANT_AI_RECOVERY": "1",
            "OKXQUANT_RECOVERY_ID": str(claimed["id"]),
            "OKXQUANT_RECOVERY_ACCOUNT_SCOPE": claimed["account_scope"],
            "OKXQUANT_RECOVERY_SLOT_START": claimed["slot_start"],
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(ai_recovery.authorized_environment()["id"], claimed["id"])
            os.environ["OKXQUANT_RECOVERY_ACCOUNT_SCOPE"] = "different"
            self.assertIsNone(ai_recovery.authorized_environment())


if __name__ == "__main__":
    unittest.main()
