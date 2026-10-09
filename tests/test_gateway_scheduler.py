"""Gateway Scheduler timing and migration tests."""
from __future__ import annotations
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from okxquant_gateway.scheduler import GatewayScheduler, JOBS
from okxquant_gateway.store import GatewayStore

BJ = timezone(timedelta(hours=8))


class GatewaySchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.now = datetime(2026, 9, 1, 18, 0, tzinfo=BJ)

    def tearDown(self):
        self.scheduler.shutdown()
        self.temp.cleanup()

    def test_migration_baseline_prevents_immediate_launch(self):
        self.scheduler.initialize_migration_baseline(self.now)
        with patch("okxquant_gateway.scheduler.load_schedule", return_value={}):
            self.assertEqual(self.scheduler.tick(self.now), [])

    def test_interval_job_becomes_due_on_aligned_trader_boundary(self):
        trader = next(spec for spec in JOBS if spec.name == "trader")
        boundary = self.now.replace(minute=15, second=0)
        self.store.set_state("job.last.trader", boundary.replace(minute=0).isoformat())
        self.assertTrue(self.scheduler.due(trader, boundary, {}))
        self.assertFalse(self.scheduler.due(trader, boundary.replace(second=11), {}))

    def test_minute_jobs_are_staggered_away_from_the_trader_boundary(self):
        factors = next(spec for spec in JOBS if spec.name == "factor_library")
        market = next(spec for spec in JOBS if spec.name == "market_observations")
        self.assertNotIn("demo_scalp",[spec.name for spec in JOBS])
        boundary = self.now.replace(minute=15, second=0)
        for spec in (factors, market):
            self.store.set_state(f"job.last.{spec.name}", boundary.replace(minute=14).isoformat())
            self.assertFalse(self.scheduler.due(spec, boundary, {}))
            phased = boundary + timedelta(seconds=spec.phase_seconds)
            self.assertTrue(self.scheduler.due(spec, phased, {}))

    def test_daily_job_runs_once_per_time_slot(self):
        briefing = next(spec for spec in JOBS if spec.name == "daily_briefing")
        schedule = {"briefing_times": ["08:00", "20:00"]}
        at_eight = self.now.replace(hour=8)
        self.assertTrue(self.scheduler.due(briefing, at_eight, schedule))
        self.store.set_state("job.last.daily_briefing", at_eight.isoformat())
        self.assertFalse(self.scheduler.due(briefing, at_eight, schedule))
        self.assertTrue(self.scheduler.due(briefing, at_eight.replace(hour=20), schedule))

    def test_tick_batches_last_run_reads_for_idle_jobs(self):
        jobs=(next(spec for spec in JOBS if spec.name=='position_guard'),
              next(spec for spec in JOBS if spec.name=='market_observations'))
        for spec in jobs:self.store.set_state(f'job.last.{spec.name}',self.now.isoformat())
        with patch('okxquant_gateway.scheduler.current_jobs',return_value=jobs), \
             patch('okxquant_gateway.scheduler.load_schedule',return_value={}), \
             patch.object(self.store,'get_states',wraps=self.store.get_states) as bulk, \
             patch.object(self.store,'get_state',wraps=self.store.get_state) as single:
            self.assertEqual(self.scheduler.tick(self.now),[])
        bulk.assert_called_once_with([f'job.last.{spec.name}' for spec in jobs])
        single.assert_not_called()

    def test_runtime_state_survives_store_reopen(self):
        self.store.set_state("job.last.news", self.now.isoformat())
        reopened = GatewayStore(self.store.path)
        self.assertEqual(reopened.get_state("job.last.news"), self.now.isoformat())

    def test_self_improvement_is_deferred_around_trader_slots(self):
        trader = next(spec for spec in JOBS if spec.name == "trader")
        review = next(spec for spec in JOBS if spec.name == "self_improvement")
        self.assertEqual(review.timeout_seconds, 480)
        jobs = (trader, review)
        with patch("okxquant_gateway.scheduler.current_jobs", return_value=jobs):
            running = Future()
            self.scheduler.running["trader"] = running
            self.assertFalse(self.scheduler._self_improvement_window_open(self.now, review))
            self.scheduler.running.clear()
            self.assertTrue(self.scheduler._self_improvement_window_open(self.now.replace(minute=3), review))
            self.assertFalse(self.scheduler._self_improvement_window_open(self.now.replace(minute=7), review))


if __name__ == "__main__":
    unittest.main()
