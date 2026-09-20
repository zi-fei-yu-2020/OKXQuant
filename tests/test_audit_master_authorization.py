import copy,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
from scripts.decision_authorization import validate_management,assert_strategy
from scripts.risk_policy import RiskRejected

class DecisionAuthorizationAudit(unittest.TestCase):
    def setUp(self):
        self.env=SimpleNamespace(identity='demo-a',connection_id='account-a',binding_version=2)
        self.pos={'instId':'BTC-USDT-SWAP','posSide':'long','pos':'2','posId':'position-a','cTime':'1000'}
        self.positions={self.pos['instId']:self.pos}
        self.payload={'timestamp':1000,'account_scope':'demo-a','connection_id':'account-a','binding_version':2,
                      'execution_profile_signature':'exec-a','strategy_profile_hash':'strategy-a','position_basis':[dict(self.pos)]}
        for target,options in [('okxquant_backend.account_connections.assert_current',{}),('scripts.prompt_library.active_profile',{'return_value':{'id':'custom'}}),('scripts.execution_profiles.runtime',{'return_value':{'signature':'exec-a'}}),('scripts.trading_prompt.profile_signature',{'return_value':'strategy-a'})]:
            p=patch(target,**options);p.start();self.addCleanup(p.stop)

    def check(self,payload=None,positions=None):
        return validate_management(payload or self.payload,self.env,positions or self.positions,target='BTC-USDT-SWAP',now=1010)

    def test_matching_live_identity_is_authorized(self):self.check()
    def test_old_future_and_nonfinite_envelopes_not_authorized(self):
        for ts in (0,1011,float('nan'),True):
            with self.subTest(timestamp=ts),self.assertRaises(RiskRejected):self.check({**self.payload,'timestamp':ts})
    def test_account_connection_and_binding_are_all_required(self):
        for key,value in [('account_scope','live-b'),('connection_id','account-b'),('binding_version',3)]:
            with self.subTest(key=key),self.assertRaises(RiskRejected):self.check({**self.payload,key:value})
    def test_strategy_and_execution_edits_invalidate_old_authorization(self):
        for key in ('strategy_profile_hash','execution_profile_signature'):
            with self.subTest(key=key),self.assertRaises(RiskRejected):self.check({**self.payload,key:'changed'})
    def test_same_instrument_reopened_or_resized_not_old_position(self):
        for key,value in [('posId','position-b'),('cTime','2000'),('pos','3'),('posSide','short')]:
            with self.subTest(key=key),self.assertRaises(RiskRejected):self.check(positions={'BTC-USDT-SWAP':{**self.pos,key:value}})
    def test_missing_lifecycle_evidence_cannot_manage_by_symbol_only(self):
        for key in ('posId','cTime'):
            position={**self.pos,key:None}
            with self.subTest(key=key),self.assertRaises(RiskRejected):self.check(positions={'BTC-USDT-SWAP':position})
    def test_original_numeric_representation_does_not_invalidate_same_position(self):
        self.check(positions={'BTC-USDT-SWAP':{**self.pos,'pos':2.0}})
    def test_entry_hash_checks_same_profile_text_edits(self):
        assert_strategy({'strategy_profile_hash':'strategy-a'})
        with self.assertRaises(RiskRejected):assert_strategy({'strategy_profile_hash':'old'})
    def test_actual_consumer_returns_before_write_for_wrong_account(self):
        import io,json
        import ai_factor_trader as trader
        payload={**self.payload,'timestamp':time.time(),'account_scope':'wrong','instructions':[{'instId':'BTC-USDT-SWAP','action':'CLOSE_MARKET','confidence':99}]}
        with patch.object(trader.os.path,'exists',return_value=True),patch('builtins.open',return_value=io.StringIO(json.dumps(payload))),patch.object(trader.market,'_selected',return_value=self.env),patch.object(trader,'close_position_confirmed') as close,patch.object(trader,'run_cmd_result') as write:
            actions=[];trader.execute_ai_position_management(self.positions,{},'test',actions)
            close.assert_not_called();write.assert_not_called();self.assertTrue(actions)
