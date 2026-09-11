"""Read-only dashboard integration boundaries; no real credentials or requests."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from scripts.okx_runtime import OKXEnvironment
import dashboard.app as dashboard
from okxquant_backend.okx_read_service import read_private_resource
from okxquant_backend.okx_setup import diagnose_okx_runtime


class OKXReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.demo = OKXEnvironment('demo', 'fake-key', 'fake-secret', 'fake-pass')

    def test_all_resources_are_get_only_and_keep_environment(self):
        with patch('okxquant_backend.okx_read_service._request', return_value=[]) as request, patch('okxquant_backend.okx_read_service.read_algo_orders', return_value=[]) as algos:
            for resource in ('balance', 'positions', 'orders', 'bills'):
                read_private_resource(resource, self.demo)
            read_private_resource('algos', self.demo, 'BTC-USDT-SWAP')
        self.assertEqual(request.call_count, 4)
        for call in request.call_args_list:
            self.assertEqual(call.args[0], 'GET')
            self.assertIs(call.args[3], self.demo)
            self.assertNotIn('cancel', call.args[1])
        algos.assert_called_once_with(self.demo, priority='monitor')

    def test_arbitrary_resource_or_missing_credentials_never_sends(self):
        with patch('okxquant_backend.okx_read_service._request') as request:
            with self.assertRaises(ValueError):
                read_private_resource('close-position', self.demo)
            with self.assertRaises(ValueError):
                read_private_resource('balance', OKXEnvironment('demo', '', '', ''))
        request.assert_not_called()

    def test_api_key_dashboard_does_not_require_cli(self):
        with patch('okxquant_backend.okx_read_service.read_private_resource', return_value=[{'totalEq': '100'}]) as rest, patch.object(dashboard, 'run_json_cmd_status') as cli:
            ok, data, error = dashboard.read_account_resource('balance', self.demo)
        self.assertTrue(ok)
        self.assertEqual(data[0]['totalEq'], '100')
        self.assertEqual(error, '')
        rest.assert_called_once_with('balance', self.demo, '')
        cli.assert_not_called()

    def test_failed_rest_does_not_retry_in_another_environment(self):
        with patch('okxquant_backend.okx_read_service.read_private_resource', side_effect=RuntimeError('upstream unavailable')) as rest, patch.object(dashboard, 'run_json_cmd_status') as cli:
            ok, data, _ = dashboard.read_account_resource('balance', self.demo)
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertEqual(rest.call_count, 1)
        cli.assert_not_called()

    def test_oauth_dashboard_preserves_cli_read_path(self):
        with patch.object(dashboard, 'run_json_cmd_status', return_value=(True, [], '')) as cli:
            self.assertTrue(dashboard.read_account_resource('balance', OKXEnvironment('demo', '', '', ''))[0])
        self.assertIn('account balance', cli.call_args.args[0])
        self.assertIn('--demo', cli.call_args.args[0])

    def test_rest_ready_is_distinct_from_execution_ready(self):
        with patch('okxquant_backend.okx_setup.shutil.which', return_value=None), patch('scripts.okx_runtime.selected_environment', return_value=self.demo), patch('okxquant_backend.okx_read_service.read_private_resource', return_value=[]):
            status = diagnose_okx_runtime('demo', True)
        self.assertTrue(status['read_only_ready'])
        self.assertTrue(status['read_probe']['ok'])
        self.assertFalse(status['ready'])
        self.assertFalse(status['cli']['installed'])

    def test_unconfirmed_baseline_does_not_turn_demo_funds_into_profit(self):
        def read(resource, environment, inst_id=""):
            rows = [{'totalEq': '100000', 'details': [{'ccy': 'USDT', 'eq': '100000', 'availBal': '100000'}]}] if resource == 'balance' else []
            return True, rows, ''
        baseline = {'initial_capital': 10000, 'reset_time': '1970-01-01 00:00:00', 'baseline_configured': False}
        with patch.object(dashboard, 'CACHE_DATA', {}), patch('scripts.okx_runtime.selected_environment', return_value=self.demo), patch.object(dashboard, 'read_account_resource', side_effect=read), patch('okxquant_backend.account_baseline.load_account_baseline', return_value=baseline), patch.object(dashboard, 'persist_dashboard_cache'):
            dashboard.update_cache_cycle()
            account = dashboard.CACHE_DATA['account']
            self.assertEqual(account['total_eq'], 100000)
            self.assertIsNone(account['cum_net_pnl'])
            self.assertIsNone(account['cum_roi_pct'])
            self.assertIsNone(account['initial_capital'])
            self.assertFalse(account['baseline_configured'])
            self.assertEqual(dashboard.CACHE_DATA['snapshots'], [])

    def test_five_positions_share_one_algo_read_and_failure_is_unknown_not_unprotected(self):
        names = ['BTC','ETH','SOL','DOGE','SUI']
        positions = [{'instId': n+'-USDT-SWAP', 'pos':'4','posSide':'long','avgPx':'100','markPx':'102','lever':'3','upl':'8','uplRatio':'.02'} for n in names]
        algos = [{'instId': n+'-USDT-SWAP', 'algoId':str(i), 'state':'live','posSide':'long','side':'sell','reduceOnly':'true','sz':'4','tpTriggerPx':'110','slTriggerPx':'95'} for i,n in enumerate(names)]
        baseline = {'initial_capital': 10000, 'reset_time':'1970-01-01 00:00:00', 'baseline_configured':False}
        for failed in (False, True):
            calls=[]
            def read(resource, env, inst_id=''):
                calls.append((resource,inst_id))
                if resource=='positions': return True, positions, ''
                if resource=='balance': return True,[{'totalEq':'1000','details':[{'ccy':'USDT','eq':'1000','availBal':'900'}]}],''
                if resource=='algos': return (False,None,'rate_limited') if failed else (True,algos,'')
                return True,[],''
            with patch.object(dashboard,'CACHE_DATA',{}), patch('scripts.okx_runtime.selected_environment',return_value=self.demo), patch.object(dashboard,'read_account_resource',side_effect=read), patch.object(dashboard,'load_position_trackers',return_value={}), patch('okxquant_backend.account_baseline.load_account_baseline',return_value=baseline), patch.object(dashboard,'persist_dashboard_cache'):
                dashboard.update_cache_cycle()
                result=dashboard.CACHE_DATA['positions_summary']['items']
                self.assertEqual(len(result),5)
                self.assertEqual([c for c in calls if c[0]=='algos'], [('algos','')])
                if failed:
                    self.assertTrue(all(p['protectionStatus'] in ('unknown_stale','verification_stale') for p in result))
                else:
                    self.assertTrue(all(p['protectionStatus']=='fully_protected' for p in result))

    def test_failed_demo_does_not_reuse_live_or_other_account_cache(self):
        for identity in ('okx:live:other', 'okx:demo:other', None):
            stale = {'account': {'total_eq': 500}, 'account_source_id': identity}
            with patch.object(dashboard, 'CACHE_DATA', stale), patch('scripts.okx_runtime.selected_environment', return_value=self.demo), patch.object(dashboard, 'read_account_resource', return_value=(False, None, 'unavailable')):
                dashboard.update_cache_cycle()
                self.assertEqual(dashboard.CACHE_DATA['data_health']['status'], 'OFFLINE')
                self.assertEqual(dashboard.CACHE_DATA['account'], {})
                self.assertEqual(dashboard.CACHE_DATA['okx_environment'], 'demo')


if __name__ == '__main__':
    unittest.main()
