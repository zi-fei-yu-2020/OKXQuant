import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from scripts import strategy_engine_runtime as engine, demo_scalp
from scripts.risk_policy import RiskRejected
from scripts.position_lifecycle import identity

class MinuteRetirementTests(unittest.TestCase):
    def test_legacy_demo_enabled_file_cannot_reactivate(self):
        with tempfile.TemporaryDirectory() as d,patch.object(demo_scalp,'CONFIG',Path(d)/'demo.json'):
            demo_scalp.CONFIG.write_text(json.dumps({'enabled':True,'version':'demo-scalp-v2'}))
            s=engine.status(SimpleNamespace(mode='demo',identity='demo-a'))
            self.assertEqual(s['status'],'retired');self.assertFalse(s['enabled']);self.assertFalse(s['authorized'])
            self.assertEqual(s['existing_position_management'],'continues')
    def test_live_authorization_cannot_write_a_new_consent(self):
        with patch.object(engine,'_save') as save:
            with self.assertRaisesRegex(RiskRejected,'retired'):
                engine.authorize_live(SimpleNamespace(mode='live'),'admin')
            save.assert_not_called()
    def test_minute_removed_but_safety_and_ai_jobs_remain(self):
        from okxquant_gateway.scheduler import JOBS
        jobs={j.name:j for j in JOBS}
        self.assertNotIn('demo_scalp',jobs);self.assertEqual(jobs['trader'].interval_seconds,900)
        self.assertTrue({'position_guard','ledger_sync','execution_quality','evidence_sync'}<=set(jobs))
    def test_all_minute_markers_are_blocked_but_ai_scalp_horizon_is_not(self):
        for payload in [{'entry_engine':'demo_scalp_v2'},{'features':{'strategy_engine':'demo_scalp_v2'}},
                        {'decision':{'strategy_engine':'demo_scalp_v2'}},{'mode':{'engine':'demo_scalp_v2'}},
                        {'setup':'scalp_breakout_1m'},{'entry_context':{'version':'scalp-management-v8'}}]:
            with self.assertRaises(RiskRejected):engine.assert_entry_supported(payload)
        engine.assert_entry_supported({'horizon':'scalp','entry_engine':'ai_trader','setup':'closed_range_breakout'})
    def test_dispatch_rejects_retired_plan_before_exchange_authority(self):
        from scripts.decision_authorization import entry_dispatch_guard
        with patch('okxquant_backend.account_connections.assert_current') as current:
            with self.assertRaises(RiskRejected):
                with entry_dispatch_guard(SimpleNamespace(mode='demo'),{'entry_engine':'demo_scalp_v2'}):self.fail('must not dispatch')
            current.assert_not_called()
    def test_retired_position_is_exit_only_and_not_relabelled_as_ai(self):
        pos={'instId':'BTC-USDT-SWAP','posSide':'long','posId':'p','cTime':'1'}
        trackers={'BTC-USDT-SWAP_long':{'mode':{'engine':'demo_scalp_v2'},'positionIdentity':identity(pos,'scope')}}
        before=json.dumps(trackers,sort_keys=True)
        with self.assertRaisesRegex(RiskRejected,'exit-only'):engine.assert_existing_position_can_increase([pos],'scope',trackers)
        self.assertEqual(json.dumps(trackers,sort_keys=True),before)
    def test_stale_or_other_account_tracker_does_not_reclassify_new_ai_position(self):
        old={'instId':'BTC-USDT-SWAP','posSide':'long','posId':'old','cTime':'1'}
        current={**old,'posId':'new','cTime':'2'}
        trackers={'BTC-USDT-SWAP_long':{'mode':{'engine':'demo_scalp_v2'},'positionIdentity':identity(old,'scope')}}
        engine.assert_existing_position_can_increase([current],'scope',trackers)
        engine.assert_existing_position_can_increase([old],'another-scope',trackers)
    def test_legacy_enable_api_returns_gone_without_writes(self):
        from okxquant_backend import app as api
        from fastapi import HTTPException
        payload=api.LiveEngineAuthorizationRequest(enabled=True,confirmation='ENABLE LIVE SCALP',expected_binding={})
        with patch.object(api,'require_superadmin',return_value={'username':'test'}),patch.object(engine,'_save') as save:
            with self.assertRaises(HTTPException) as caught:api.strategy_engine_authorize_api(payload)
            self.assertEqual(caught.exception.status_code,410);save.assert_not_called()
