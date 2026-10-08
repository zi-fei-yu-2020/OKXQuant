import threading
import asyncio
import json
import time
import unittest
import tempfile
from pathlib import Path
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

    def test_refresh_returns_small_monitoring_projection_and_keeps_core_fields(self):
        cached={
            **self.snapshot(),
            'ai_last_prompt':'FULL PROMPT' * 1000,
            'ai_brain_history':[{'time':'x','status':'failed','failure_reason':'request timeout',
                                 'model_failure':{'category':'request_timeout','attempts':1},
                                 'ai_last_prompt':'HISTORIC PROMPT' * 1000,'macro_assessment':'summary',
                                 'council_transcript':{'advisors':{'risk':{'role_name':'risk','content':'FULL TRANSCRIPT' * 1000}}}}],
            'review':{'original':'FULL REVIEW','ai_last_prompt':'EMBEDDED PROMPT' * 1000},
            'state_snapshot':{'data':'KEEP'},
            'trades':[{'id':'t1','instId':'BTC-USDT-SWAP','net_pnl':1,
                       'opening_features':{'candles':['x' * 1000] * 100},
                       'holding_observations':{'samples':999}}],
            'factors':[{'instId':'BTC-USDT-SWAP','ai_last_prompt':'FACTOR PROMPT' * 1000}],
        }
        with patch.object(dashboard,'CACHE_DATA',cached), patch.object(dashboard,'LAST_CACHE_TIME',time.time()):
            response=asyncio.run(dashboard.get_all_data())
            value=json.loads(response.body)
        self.assertNotIn('ai_last_prompt', value)
        self.assertNotIn('ai_last_prompt', value['review'])
        self.assertNotIn('ai_last_prompt', value['factors'][0])
        self.assertEqual(value['state_snapshot'], cached['state_snapshot'])
        self.assertEqual(value['payload_profile'], 'monitoring-summary-v1')
        self.assertNotIn('opening_features', value['trades'][0])
        self.assertNotIn('holding_observations', value['trades'][0])
        self.assertNotIn('ai_last_prompt', value['ai_brain_history'][0])
        self.assertNotIn('content', value['ai_brain_history'][0]['council_transcript']['advisors']['risk'])
        self.assertEqual(value['ai_brain_history'][0]['status'],'failed')
        self.assertEqual(value['ai_brain_history'][0]['failure_reason'],'request timeout')
        self.assertEqual(value['ai_brain_history'][0]['model_failure']['attempts'],1)
        self.assertLess(len(response.body), 20_000)

    def test_deferred_prompt_and_ai_history_details_remain_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            prompt_file=Path(temp_dir) / 'prompt.txt'
            history_file=Path(temp_dir) / 'history.json'
            prompt_file.write_text('FULL PROMPT', encoding='utf-8')
            (Path(temp_dir)/'ai_brain_last_prompt_meta.json').write_text(json.dumps({'account_scope':self.env.identity,'sha256':__import__('hashlib').sha256(b'FULL PROMPT').hexdigest()}))
            record={'time':'2026-10-06 01:00:00','macro_assessment':'summary','account_scope':self.env.identity,
                    'council_transcript':{'advisors':{'risk':{'role_name':'risk','content':'FULL TRANSCRIPT'}}}}
            history_file.write_text(json.dumps([record]), encoding='utf-8')
            history_id=dashboard._history_id(record)
            with patch.object(dashboard,'AI_LAST_PROMPT_FILE',str(prompt_file)), \
                 patch.object(dashboard,'AI_HISTORY_FILE',str(history_file)):
                prompt=json.loads(asyncio.run(dashboard.get_ai_last_prompt()).body)
                summaries=json.loads(asyncio.run(dashboard.get_ai_history()).body)
                detail=json.loads(asyncio.run(dashboard.get_ai_history_detail(history_id)).body)
        self.assertEqual(prompt['prompt'],'FULL PROMPT')
        self.assertEqual(summaries['items'][0]['history_id'],history_id)
        self.assertNotIn('content',summaries['items'][0]['council_transcript']['advisors']['risk'])
        self.assertEqual(detail['council_transcript']['advisors']['risk']['content'],'FULL TRANSCRIPT')

    def test_trade_list_is_compact_but_detail_keeps_audit_evidence(self):
        row={'id':'trade-1','environment_id':self.env.identity,'instId':'BTC-USDT-SWAP',
             'net_pnl':1.2,'opening_features':{'candles':['large evidence']}}
        with patch.object(dashboard,'_read_ledger_rows',return_value=[row]):
            listing=json.loads(asyncio.run(dashboard.get_trades()).body)
            detail=json.loads(asyncio.run(dashboard.get_trade_detail('trade-1')).body)
        self.assertNotIn('opening_features',listing['items'][0])
        self.assertEqual(detail['opening_features'],row['opening_features'])

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
