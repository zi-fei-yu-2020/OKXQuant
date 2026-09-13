import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import instrument_support as support
import ai_factor_trader as trader
import ai_brain_trader as brain
import okxquant_backend.app as app_module
from fastapi.testclient import TestClient
from okxquant_backend.admin_auth import AdminAuthStore

POOL = [{'instId': 'BTC-USDT-SWAP', 'name': 'BTC'}, {'instId': 'WLD-USDT-SWAP', 'name': 'WLD'}]
CATALOG = {'code': '0', 'data': [{'instId': 'BTC-USDT-SWAP', 'state': 'live', 'settleCcy': 'USDT'}]}


class InstrumentSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(support, 'DATA_DIR', Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        p = patch.object(support.market, 'CACHE_DIR', Path(self.temp.name) / 'locks'); p.start(); self.addCleanup(p.stop)

    def test_successful_full_catalog_proves_missing_instrument_unsupported(self):
        with patch.object(support.market, 'get_json', return_value=CATALOG) as read:
            result = support.pool_support(POOL, 'demo', refresh=True)
        self.assertEqual(read.call_args.kwargs['simulated'], True)
        self.assertTrue(result['items']['BTC-USDT-SWAP']['can_open'])
        missing = result['items']['WLD-USDT-SWAP']
        self.assertEqual(missing['status'], 'unsupported')
        self.assertIn('模拟盘不支持', missing['label'])
        self.assertEqual(result['supported_count'], 1)
        self.assertFalse(missing['can_open'])

    def test_http_failure_empty_catalog_and_malformed_catalog_are_unknown(self):
        for value in ({'data': []}, {'data': [{}]}):
            with patch.object(support.market, 'get_json', return_value=value):
                result = support.pool_support(POOL, 'demo', refresh=True)
                self.assertEqual(result['status'], 'unknown')
                self.assertTrue(all(i['status'] == 'unknown' and not i['can_open'] for i in result['items'].values()))
        with patch.object(support.market, 'get_json', side_effect=TimeoutError()):
            self.assertEqual(support.opening_status('WLD-USDT-SWAP', 'demo')['status'], 'unknown')

    def test_suspended_contract_is_unavailable_not_missing(self):
        rows = {'data': [{**CATALOG['data'][0], 'state': 'suspend'}]}
        with patch.object(support.market, 'get_json', return_value=rows):
            result = support.opening_status('BTC-USDT-SWAP', 'demo')
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(result['can_open'])

    def test_environment_caches_are_isolated_and_do_not_rewrite_timestamps(self):
        with patch.object(support.market, 'get_json', return_value=CATALOG) as read:
            a = support.pool_support(POOL, 'demo', refresh=True)
            b = support.pool_support(POOL, 'demo', refresh=True)
            self.assertEqual(a['checked_at'], b['checked_at'])
            self.assertEqual(read.call_count, 1)
            support.pool_support(POOL, 'live', refresh=True)
            self.assertEqual(read.call_count, 2)
            self.assertFalse(read.call_args.kwargs['simulated'])

    def test_expired_failed_refresh_does_not_reuse_supported_status(self):
        with patch.object(support.market, 'get_json', return_value=CATALOG):
            support.pool_support(POOL, 'demo', refresh=True)
        p = support._path('demo'); value = json.loads(p.read_text()); value['checked_at'] -= 120; p.write_text(json.dumps(value))
        with patch.object(support.market, 'get_json', side_effect=TimeoutError()):
            self.assertEqual(support.opening_status('BTC-USDT-SWAP', 'demo')['status'], 'unknown')

    def test_cache_only_ui_read_never_blocks_on_network(self):
        with patch.object(support.market, 'get_json', side_effect=AssertionError('UI must not wait for network')) as read, patch.object(support, '_background') as background:
            result = support.pool_support(POOL, 'demo')
        read.assert_not_called(); background.assert_called_once_with('demo')
        self.assertEqual(result['status'], 'unknown')

    def test_display_grace_does_not_grant_opening_authority(self):
        with patch.object(support.market,'get_json',return_value=CATALOG):support.pool_support(POOL,'demo',refresh=True)
        path=support._path('demo');snapshot=json.loads(path.read_text());snapshot['checked_at']-=90;path.write_text(json.dumps(snapshot))
        with patch.object(support,'_background') as background:
            display=support.pool_support(POOL,'demo')
        self.assertEqual(display['items']['BTC-USDT-SWAP']['status'],'refreshing')
        self.assertFalse(display['items']['BTC-USDT-SWAP']['can_open'])
        self.assertEqual(display['checked_at'],snapshot['checked_at'])
        background.assert_called_once()
        with patch.object(support.market,'get_json',side_effect=TimeoutError()):
            self.assertEqual(support.opening_status('BTC-USDT-SWAP','demo')['status'],'unknown')

    def test_new_pool_item_uses_existing_catalog_not_old_pool_snapshot(self):
        with patch.object(support.market, 'get_json', return_value=CATALOG):
            support.pool_support(POOL[:1], 'demo', refresh=True)
            result = support.pool_support(POOL, 'demo', refresh=True)
        self.assertEqual(result['items']['WLD-USDT-SWAP']['status'], 'unsupported')

    def test_held_unsupported_instrument_is_retained_for_management(self):
        with patch.object(support.market, 'get_json', return_value=CATALOG):
            eligible, status = support.trading_universe(POOL, [], 'demo')
            self.assertEqual([i['name'] for i in eligible], ['BTC'])
            eligible, status = support.trading_universe(POOL, [{'instId': 'WLD-USDT-SWAP'}], 'demo')
            self.assertEqual([i['name'] for i in eligible], ['BTC', 'WLD'])
            self.assertFalse(status['items']['WLD-USDT-SWAP']['can_open'])

    def test_unknown_catalog_keeps_held_positions_but_no_new_candidates(self):
        with patch.object(support.market, 'get_json', side_effect=TimeoutError()):
            eligible, status = support.trading_universe(POOL, [{'instId': 'BTC-USDT-SWAP'}], 'demo')
        self.assertEqual([i['name'] for i in eligible], ['BTC'])
        self.assertTrue(all(not i['can_open'] for i in status['items'].values()))

    def test_order_boundary_blocks_unsupported_unknown_without_cli_side_effects(self):
        for status in ('unsupported', 'unknown', 'unavailable'):
            with patch.object(trader.market, '_selected', return_value=SimpleNamespace(mode='demo', simulated=True)), patch.object(trader.support, 'opening_status', return_value={'status': status, 'can_open': False, 'message': 'not eligible'}), patch.object(trader, 'run_cmd_result') as write, patch.object(trader, 'run_json_cmd') as ticker:
                ok, message = trader.submit_protected_limit_order('WLD-USDT-SWAP', 'buy', 'long', 1, 1, 2, .5)
            self.assertFalse(ok); self.assertEqual(message, 'not eligible')
            write.assert_not_called(); ticker.assert_not_called()

    def test_supported_order_keeps_existing_protected_order_payload(self):
        with patch.object(trader.market, '_selected', return_value=SimpleNamespace(mode='live', simulated=False, identity='test-live')), patch.object(trader.entry_gateway, 'prepare', return_value=({'size': 1, 'entry': 100, 'stop': 90, 'take_profit': 120}, 'okxquanttest')), patch.object(trader.support, 'opening_status', return_value={'can_open': True}), patch.object(trader, 'okx_private_command', side_effect=lambda x:x), patch.object(trader, 'run_cmd_result', return_value={'ok': True, 'data': [{'ordId': '123'}]}) as write:
            self.assertEqual(trader.submit_protected_limit_order('BTC-USDT-SWAP', 'buy', 'long', 1, 100, 120, 90), (True, '123'))
        command = write.call_args.args[0]
        for part in ('--ordType limit', '--tpTriggerPx 120', '--slTriggerPx 90', '--sz 1'):
            self.assertIn(part, command)

    def test_unknown_environment_is_not_coerced_to_live_or_demo(self):
        with self.assertRaises(ValueError): support.pool_support(POOL, 'invalid', refresh=True)


class InstrumentSupportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.auth = AdminAuthStore(Path(self.temp.name) / 'admin.db'); self.auth.initialize_from_legacy('InitialAdmin123456')
        p=patch.object(app_module, 'admin_auth', self.auth); p.start(); self.addCleanup(p.stop)
        self.client = TestClient(app_module.app)
        login=self.client.post('/api/v1/admin/auth/login', json={'username':'admin','password':'InitialAdmin123456'})
        self.headers={'X-OKXQuant-Session':login.json()['session_token']}

    def test_preview_is_authenticated_and_does_not_change_environment(self):
        self.assertEqual(self.client.get('/api/v1/admin/instruments/support').status_code, 401)
        with patch.object(app_module, 'pool_support', return_value={'environment':'live','items':{}}) as check, patch.object(app_module, 'update_env') as save:
            response=self.client.get('/api/v1/admin/instruments/support?environment=live', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(check.call_args.args[1], 'live'); save.assert_not_called()
        self.assertEqual(self.client.get('/api/v1/admin/instruments/support?environment=wrong', headers=self.headers).status_code, 400)

    def test_add_unsupported_instrument_returns_readable_error_without_saving(self):
        with patch.object(app_module, 'load_instruments', return_value=POOL[:1]), patch.object(app_module, 'opening_status', return_value={'status':'unsupported','can_open':False,'message':'WLD-USDT-SWAP 模拟盘不支持，仅供行情观察'}), patch.object(app_module, 'save_instruments') as save:
            response=self.client.post('/api/v1/admin/instruments', json={'inst_id':'WLD-USDT-SWAP'}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn('模拟盘不支持', response.json()['detail']); save.assert_not_called()


if __name__ == '__main__': unittest.main()
