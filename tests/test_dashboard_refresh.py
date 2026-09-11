import threading
import asyncio
import json
import time
import unittest
from unittest.mock import patch
import dashboard.app as dashboard
from scripts.okx_runtime import OKXEnvironment


class DashboardRefreshTests(unittest.TestCase):
    def setUp(self):
        self.env = OKXEnvironment('demo', 'test', 'secret', 'pass')
        self.lock = threading.Lock()
        self.patches = [patch.object(dashboard, 'CACHE_UPDATE_LOCK', self.lock),
                        patch('scripts.okx_runtime.selected_environment', return_value=self.env)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def snapshot(self):
        return {'account_source_id': self.env.identity, 'account': {'total_eq': 10},
                'data_health': {'status': 'LIVE', 'partial': False}}

    def test_stale_reader_returns_immediately_and_never_waits_for_upstream(self):
        cached = self.snapshot()
        with patch.object(dashboard, 'CACHE_DATA', cached), patch.object(dashboard, 'LAST_CACHE_TIME', time.time() - 60), patch.object(dashboard, 'request_cache_refresh') as refresh, patch.object(dashboard, '_update_cache_cycle', side_effect=AssertionError('HTTP read must not call upstream')):
            result = dashboard.monitoring_snapshot()
        self.assertEqual(result['account']['total_eq'], 10)
        self.assertEqual(result['data_health']['status'], 'STALE')
        self.assertEqual(cached['data_health']['status'], 'LIVE')
        refresh.assert_called_once()

    def test_cold_reader_returns_explicit_loading_not_fake_balances(self):
        with patch.object(dashboard, 'CACHE_DATA', {}), patch.object(dashboard, 'request_cache_refresh') as refresh:
            result = dashboard.monitoring_snapshot()
        self.assertTrue(result['initializing'])
        self.assertEqual(result['account'], {})
        self.assertEqual(result['okx_environment'], 'demo')
        self.assertEqual(result['account_source_id'], self.env.identity)
        refresh.assert_called_once()

    def test_refresh_keeps_complete_prompt_history_and_other_full_payload_fields(self):
        cached={**self.snapshot(),'ai_last_prompt':'FULL PROMPT','ai_brain_history':[{'time':'x','ai_last_prompt':'HISTORIC PROMPT'}],
                'review':{'original':'FULL REVIEW'},'state_snapshot':{'data':'KEEP'},'trades':[]}
        with patch.object(dashboard,'CACHE_DATA',cached), patch.object(dashboard,'LAST_CACHE_TIME',time.time()):
            value=json.loads(asyncio.run(dashboard.get_all_data()).body)
        for key in ('ai_last_prompt','ai_brain_history','review','state_snapshot'):
            self.assertEqual(value[key],cached[key])

    def test_wrong_account_snapshot_is_never_served(self):
        cached = self.snapshot()
        cached['account_source_id'] = 'okx:live:another-account'
        with patch.object(dashboard, 'CACHE_DATA', cached), patch.object(dashboard, 'request_cache_refresh'):
            self.assertEqual(dashboard.monitoring_snapshot()['account'], {})

    def test_singleflight_prevents_parallel_refreshes(self):
        self.lock.acquire()
        try:
            with patch.object(dashboard, '_update_cache_cycle') as update:
                self.assertFalse(dashboard.update_cache_cycle())
            update.assert_not_called()
        finally:
            self.lock.release()

    def test_failed_refresh_releases_lock(self):
        with patch.object(dashboard, '_update_cache_cycle', side_effect=RuntimeError('upstream')):
            with self.assertRaises(RuntimeError):
                dashboard.update_cache_cycle()
        self.assertFalse(self.lock.locked())

    def test_async_refresh_is_singleflight_before_thread_start(self):
        with patch.dict('os.environ', {'OKXQUANT_TESTING': '0'}), patch.object(dashboard.threading, 'Thread') as thread:
            try:
                self.assertTrue(dashboard.request_cache_refresh())
                self.assertFalse(dashboard.request_cache_refresh())
                self.assertEqual(thread.call_count, 1)
            finally:
                if self.lock.locked(): self.lock.release()


if __name__ == '__main__':
    unittest.main()
