"""Independent trading cross-review: only this file is new; production stays read-only.

Run in WSL with scripts/run_tests.py --pattern test_cross_review_trading.py.
ReleaseBlockingRegressions assert the desired invariant (fail until the owner fixes
it). Other tests document the bounded PASS surface. No historical test imports.
"""
from contextlib import ExitStack
from dataclasses import replace
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ai_factor_trader as trader
from scripts import capital_pool as pool, execution_profiles as profiles
from scripts import execution_leverage as leverage, strategy_engine_runtime as engine
from scripts import entry_gateway as gateway, entry_reconciliation as reconciliation
from scripts import decision_authorization as authorization, position_lifecycle as lifecycle
from scripts import risk_policy as risk, strategy_evidence as evidence
from scripts import prompt_library, trading_prompt
from okxquant_backend import account_connections

AT=1_788_825_600.
INST='CROSS-USDT-SWAP'
META={'instId':INST,'ctType':'linear','settleCcy':'USDT','state':'live',
      'ctVal':'1','lotSz':'.0001','minSz':'.0001','tickSz':'.01'}


def environment(mode='demo'):
    return SimpleNamespace(identity='okx:'+mode+':cross-fixture',mode=mode,simulated=mode=='demo',
                           configured=True,connection_id='cross-fixture',binding_version=7,
                           base_url='https://www.okx.com',api_key='offline-key',secret_key='offline-secret',
                           passphrase='offline-passphrase',cli_env=lambda:{})


def balance(eq=300,usd=330,at=AT):
    return {'uTime':str(int(at*1000)),'totalEq':str(usd),
            'details':[{'ccy':'USDT','eq':str(eq),'availEq':str(eq),'availBal':str(eq),'liab':'0'}]}


def observation(eq=300,at=AT):
    return {'at':at,'equity':eq,'equity_currency':'USDT','external_flow_origin':AT,
            'external_flow_total':0.}


def frozen_binding(env):
    return {**engine._identity(env),'profile_signature':'profile','execution_signature':'execution',
            'policy_signature':'policy','limits':{'max_leverage':20.,'per_trade_equity_pct':.004,
                                                  'minimum_net_rr':2.}}


class IsolatedTradingCase(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.directory=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(evidence,'DB_PATH',self.directory/'evidence.db'))
        self.stack.enter_context(patch.object(pool,'CONFIG_FILE',self.directory/'pool.json'))
        self.stack.enter_context(patch.object(engine,'STATE_FILE',self.directory/'engine.json'))
        self.stack.enter_context(patch.object(trader.algo_reader,'STATE_DIR',self.directory/'algo-cache'))
        self.env=environment()

    def enter(self,*contexts):
        return [self.stack.enter_context(context) for context in contexts]

    def store_equity(self,state):
        with evidence.connection() as db:
            db.execute('INSERT OR REPLACE INTO equity_state VALUES (?,?)',
                       (self.env.identity,evidence.canonical(state)))

    def consent(self):
        self.env=environment('live')
        self.enter(patch.object(account_connections,'assert_current'),
                   patch.object(engine,'_current_binding',side_effect=lambda env:frozen_binding(env)),
                   patch.object(engine,'_autotrade_enabled',return_value=True))
        return engine.authorize_live(self.env,'offline-cross-review-admin')

    def gateway_with_position_basis(self, basis):
        profile={'id':'cross-profile','execution_profile':'standard'}
        execution=profiles.runtime(profile)
        current={'instId':INST,'posSide':'long','pos':'1','posId':'new-position','cTime':'2000',
                 'avgPx':'100','markPx':'100','imr':'34','liqPx':'0'}
        protection={'instId':INST,'algoId':'oco-new','ordType':'oco','state':'live','posSide':'long',
                    'side':'sell','reduceOnly':'true','sz':'1','slTriggerPx':'99','tpTriggerPx':'110'}
        decision={'action':'BUY_LONG','contract_version':trading_prompt.VERSION,'contract_valid':True,
                  'valid_until':AT+300,'horizon':'swing','strategy_profile_hash':trading_prompt.profile_signature(profile)}
        record={'instrument':INST,'as_of_ms':int(AT*1000),'decision':decision,'position_basis':basis,
                'features':{'structure_1h':'1H_SWING_BULL'},'execution_profile_signature':execution['signature'],
                'connection_id':self.env.connection_id,'binding_version':self.env.binding_version}
        evidence.append(self.env.identity,'decision',record,event_id='position-basis-decision')
        def private(method,path,params,env,**kwargs):
            self.assertEqual(method,'GET')
            if path.endswith('/positions'):return [current]
            if path.endswith('/orders-pending'):return []
            if path.endswith('/balance'):return [balance(300,300)]
            if path.endswith('/leverage-info'):return [{'posSide':'long','lever':'3'}]
            if path.endswith('/bills'):return []
            self.fail('Unexpected request: '+path)
        def public(url,**kwargs):
            return {'data':[META]} if '/instruments?' in url else {'data':[{'last':'100','ts':str(int(AT*1000))}]}
        with patch.object(account_connections,'assert_current'), \
             patch.object(profiles,'runtime',return_value=execution), \
             patch.object(prompt_library,'active_profile',return_value=profile), \
             patch.object(risk,'load_policy',return_value=risk.Policy()), \
             patch.object(risk,'ledger_daily_drawdown',return_value={'blocked':False}), \
             patch.object(gateway,'_request',side_effect=private), \
             patch.object(gateway.public_market,'get_json',side_effect=public), \
             patch.object(gateway,'read_algo_orders',return_value=[protection]), \
             patch.object(gateway.time,'time',return_value=AT):
            return gateway._prepare(self.env,inst_id=INST,side='long',entry=100,stop=98,take_profit=110,
                                    requested_size=1,budget=15,decision_id='position-basis-decision',decision_at=AT,horizon='swing')


class ReleaseBlockingRegressions(IsolatedTradingCase):
    def test_old_scale_decision_cannot_attach_to_reopened_same_size_position(self):
        with self.assertRaises(risk.RiskRejected):
            self.gateway_with_position_basis({'side':'long','size':1,'posId':'old-position','cTime':'1000'})

    def test_standard_disabled_pool_budget_stays_in_usdt_not_totalEq_usd(self):
        allocation=pool.admit(self.env,observation(),balance(),[],[],{INST:META},inst_id=INST,
                              available=300,policy=risk.Policy(),leverage_reader=lambda *_:3)
        plan=risk.order_plan(metadata=META,side='long',entry=100,stop=98,take_profit=110,
                             requested_size=1000,budget_usdt=15,equity=allocation.equity,
                             available=allocation.available,leverage=3,policy=allocation.policy)
        self.assertEqual(allocation.equity,300.,'USDT observation is 300; 330 is USD totalEq, not a USDT budget')
        self.assertAlmostEqual(plan['risk_budget_usdt'],1.5)

    def test_standard_no_pool_does_not_gain_dedicated_account_requirement(self):
        snapshot=balance();snapshot['details'].append({'ccy':'BTC','eq':'.001','cashBal':'.001','liab':'0'})
        try:
            state=gateway.equity_guard(self.env,snapshot,risk.Policy())
        except risk.RiskRejected as exc:
            self.fail('Standard/no-pool was newly subject to pool-only dedicated-account guard: '+str(exc))
        self.assertEqual(state['equity_currency'],'USDT')
        self.assertEqual(state['equity'],300)

    def test_small300_blocked_guard_fallback_accepts_same_usdt_balance_version(self):
        cfg=pool.Config(environment='demo',allocation_id='execution-small300-v1',budget_usdt=300)
        scope=self.env.identity+':execution:small300'
        old=pool.advance(None,cfg,scope,observation(),flat=True,policy=risk.Policy())
        pool._save(old)
        state=risk.update_equity_state(None,equity=270,at=AT+60,cash_flow=0,complete=True)
        state.update(equity_currency='USDT',external_flow_origin=AT,external_flow_total=0.,blocked=True)
        self.store_equity(state)
        try:
            result=profiles.observe_existing(self.env,None,risk.Policy(),balance=balance(270,280,AT+60))
        except risk.RiskRejected as exc:
            self.fail('Same timestamp/USDT amount must not be compared with repriced USD totalEq: '+str(exc))
        self.assertEqual(result['pool_nav'],270)

    def test_stale_entry_cleanup_must_not_cancel_reduce_only_exit(self):
        order={'instId':INST,'ordId':'exit-only','state':'live','cTime':str(int((AT-600)*1000)),
               'reduceOnly':'true','posSide':'long','sz':'1','accFillSz':'0'}
        read={'ok':True,'data':[order],'stdout':'','stderr':''}
        cancel={'ok':True,'data':[],'stdout':'','stderr':''}
        with patch.object(trader,'run_cmd_result',side_effect=[read,cancel]) as run, \
             patch.object(trader,'okx_private_command',side_effect=lambda command:command), \
             patch.object(trader.time,'time',return_value=AT):
            ok,_=trader.clean_stale_open_orders()
        self.assertTrue(ok)
        self.assertEqual(run.call_count,1,'Entry TTL must leave reduceOnly risk-reducing exits untouched')

    def test_live_revocation_after_preflight_before_dispatch_is_rechecked(self):
        authorized=self.consent()
        evidence.append(self.env.identity,'decision',{'instrument':INST,'features':{'strategy_engine':engine.ENGINE_ID},
            'execution_profile_signature':authorized['execution_signature'],
            'decision':{'horizon':'scalp','engine_binding':{key:authorized[key] for key in (*engine.BINDING_FIELDS,'record_id')}}},
            event_id='cross-decision')
        plan={'instId':INST,'scope':self.env.identity,'size':1.,'entry':100.,'stop':98.,'take_profit':110.,
              'horizon':'scalp','entry_engine':'demo_scalp_v2','decision_id':'cross-decision',
              'strategy_mode':{'engine':'demo_scalp_v2'},'setup':'scalp_breakout_1m'}
        def prepare(*args,**kwargs):
            self.assertTrue(engine.status(self.env)['enabled'])
            return plan,evidence.begin_intent(self.env.identity,'cross-decision',INST,plan)
        def revoke(*args,**kwargs):
            engine.disable_live(self.env)
            self.assertFalse(engine.status(self.env)['enabled'])
        with patch.object(trader.market,'_selected',return_value=self.env), \
             patch.object(trader.support,'opening_status',return_value={'can_open':True}), \
             patch.object(trader.market,'get_json',return_value={}), \
             patch.object(trader.entry_gateway,'prepare',side_effect=prepare), \
             patch.object(trader,'save_horizon_intent',side_effect=revoke), \
             patch.object(trader,'okx_private_command',side_effect=lambda command:command), \
             patch.object(trader.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='offline probe')) as dispatch:
            accepted,_=trader.submit_protected_limit_order(INST,'buy','long',1,100,110,98,
                risk_budget_usdt=1.2,decision_id='cross-decision',decision_at=AT,horizon='scalp')
        self.assertFalse(accepted)
        self.assertEqual(dispatch.call_count,0,'Consent was durably disabled before local order dispatch, but no final check prevented it')

        with evidence.connection() as db:
            state=db.execute('SELECT state FROM intents WHERE scope=? AND decision_id=?',
                             (self.env.identity,'cross-decision')).fetchone()[0]
        self.assertEqual(state,'not_submitted')
        self.assertEqual(reconciliation.recoverable_intents(self.env.identity),[])



class PassedTradingBoundaries(IsolatedTradingCase):
    def test_same_lifecycle_standard_gateway_can_reserve_scale_in(self):
        plan,client=self.gateway_with_position_basis({'side':'long','size':1,'posId':'new-position','cTime':'2000'})
        self.assertGreater(plan['size'],0)
        self.assertLessEqual(plan['risk_usdt'],1.5)
        self.assertEqual(plan['leverage'],3)
        self.assertEqual(len(client),32)

    def test_unknown_cloud_protection_is_not_cancelled_or_blindly_repaired(self):
        with patch.object(trader.market,'_selected',return_value=self.env), \
             patch.object(trader.algo_reader,'read_algo_orders',side_effect=RuntimeError('unavailable')), \
             patch.object(trader,'run_cmd_result') as write:
            verified,detail=trader.ensure_cloud_position_protection(INST,'long',1,110,98)
        self.assertFalse(verified);self.assertIn('UNKNOWN',detail);write.assert_not_called()

    def test_small300_effective_rates_and_leverage_are_horizon_specific(self):
        small=profiles.SMALL_300
        policy=replace(risk.Policy(),**{k:small[k] for k in small if k in risk.Policy.__dataclass_fields__})
        for horizon,pct,lev in (('scalp',.004,20.),('swing',.005,5.)):
            effective=leverage.mode_policy(policy,horizon,self.env)
            self.assertEqual(effective.per_trade_equity_pct,pct)
            self.assertEqual(effective.max_leverage,lev)
            plan=risk.order_plan(metadata=META,side='long',entry=100,stop=98,take_profit=110,
                requested_size=1000,budget_usdt=15,equity=300,available=270,leverage=lev,policy=effective)
            self.assertAlmostEqual(plan['risk_budget_usdt'],1.2 if horizon=='scalp' else 1.5)
            self.assertLessEqual(plan['risk_usdt'],plan['risk_budget_usdt'])

    def test_leverage_does_not_multiply_stop_risk_budget(self):
        outputs=[]
        for lev in (1,3,5):
            outputs.append(risk.order_plan(metadata=META,side='long',entry=100,stop=98,take_profit=110,
                requested_size=1000,budget_usdt=1.2,equity=300,available=270,leverage=lev))
        self.assertEqual(len({p['risk_usdt'] for p in outputs}),1)
        self.assertEqual(len({p['size'] for p in outputs}),1)
        self.assertAlmostEqual(outputs[0]['margin_usdt'],outputs[-1]['margin_usdt']*5)

    def test_small300_conversion_already_uses_usdt_even_when_optional_pool_disabled(self):
        p=replace(risk.Policy(),per_trade_equity_pct=.02,portfolio_stop_pct=.06)
        allocation=pool.Budget(False,330,300,p,{},pool.config_signature())
        result=profiles.cap_allocation(allocation,profiles.SMALL_300,[],[],{INST:META},INST,lambda *_:3,
                                       env=self.env,observation=observation(),balance=balance())
        self.assertEqual(result.equity,300)
        self.assertEqual(result.available,270)

    def test_live_is_not_enabled_by_demo_config_or_missing_consent(self):
        self.env=environment('live')
        with patch.object(account_connections,'assert_current'), \
             patch.object(engine,'_current_binding',return_value=frozen_binding(self.env)), \
             patch.object(engine,'_autotrade_enabled',return_value=True):
            status=engine.status(self.env)
        self.assertFalse(status['enabled']);self.assertFalse(status['authorized'])

    def test_live_regrant_rotates_record_id_old_decision_cannot_reuse_it(self):
        first=self.consent()
        old={key:first[key] for key in (*engine.BINDING_FIELDS,'record_id')}
        evidence.append(self.env.identity,'decision',{'instrument':INST,'execution_profile_signature':first['execution_signature'],
            'features':{'strategy_engine':engine.ENGINE_ID},'decision':{'horizon':'scalp','engine_binding':old}},event_id='d1')
        leverage._check_live_decision(self.env,INST,'d1',first)
        engine.disable_live(self.env)
        self.assertIsNone(leverage._live_binding(self.env,'scalp'))
        second=engine.authorize_live(self.env,'offline-cross-review-admin')
        self.assertNotEqual(first['record_id'],second['record_id'])
        with self.assertRaises(risk.RiskRejected):leverage._check_live_decision(self.env,INST,'d1',second)

    def test_account_binding_change_revokes_saved_live_consent(self):
        self.consent()
        self.env.binding_version+=1
        status=engine.status(self.env)
        self.assertFalse(status['enabled']);self.assertEqual(status['status'],'binding_changed')

    def test_existing_position_leverage_is_not_changed(self):
        self.assertEqual(leverage.choose(risk.Policy(),'scalp',self.env,3,[{'instId':INST}],{'leverage':20}),3)
        with patch.object(account_connections,'assert_current'), patch.object(evidence,'append') as journal:
            request=Mock(side_effect=lambda method,path,params,env: [{'instId':INST,'pos':'1'}] if path.endswith('/positions') else [])
            with self.assertRaises(risk.RiskRejected):leverage.apply(self.env,INST,'long',5,3,'d',request)
        self.assertTrue(all(call.args[0]=='GET' for call in request.call_args_list));journal.assert_not_called()

    def test_legacy_usdt_migration_preserves_blocked_state_and_records_bridge(self):
        old=risk.update_equity_state(None,equity=330,at=AT,cash_flow=0,complete=True)
        old.update(blocked=True,day_anchor=360.,peak=400.)
        self.store_equity(old)
        with patch.object(gateway,'_request',return_value=[]):
            with self.assertRaises(risk.RiskRejected):gateway.equity_guard(self.env,balance(300,299,AT+60),risk.Policy())
        with evidence.connection() as db:
            saved=json.loads(db.execute('SELECT payload FROM equity_state WHERE scope=?',(self.env.identity,)).fetchone()[0])
        self.assertTrue(saved['blocked']);self.assertEqual(saved['equity_currency'],'USDT')
        self.assertTrue(saved['currency_migration']['interval_unattributed'])
        self.assertFalse(saved['currency_migration']['historical_fx_reconstructed'])

    def test_unknown_timeout_receipt_never_becomes_absence_or_blind_resend(self):
        client=evidence.begin_intent(self.env.identity,'d',INST,{'instId':INST})
        with patch.object(gateway,'_request',side_effect=TimeoutError('offline')) as request:
            with self.assertRaises(risk.RiskRejected):gateway.reconcile_intents(self.env)
        self.assertEqual(evidence.unresolved(self.env.identity)[0][0],client)
        self.assertTrue(all(call.args[0]=='GET' for call in request.call_args_list))
        with self.assertRaises(sqlite3.IntegrityError):evidence.begin_intent(self.env.identity,'d',INST,{'instId':INST})

    def test_acknowledged_order_missing_from_complete_scans_is_not_cleared(self):
        plan={'instId':INST,'intent_created_at':time.time()-300,'intent_state':'acknowledged',
              'intent_order_ids':['exchange-order']}
        request=Mock(return_value=[])
        with self.assertRaisesRegex(risk.RiskRejected,'Acknowledged entry not located'):
            reconciliation.locate(self.env,'client',plan,request)
        self.assertTrue(all(call.args[0]=='GET' for call in request.call_args_list))

    def test_pending_receipt_uses_current_snapshot_without_reissuing_order(self):
        plan={'instId':INST,'intent_created_at':time.time()-10,'intent_state':'pending','intent_order_ids':['order']}
        order={'instId':INST,'clOrdId':'client','ordId':'order','state':'partially_filled'}
        request=Mock(side_effect=AssertionError('must not reissue'))
        actual,proof=reconciliation.locate(self.env,'client',plan,request,pending_orders=[order])
        self.assertEqual(actual,order);self.assertEqual(proof['reconciliation'],'fresh_preflight_pending_snapshot')
        request.assert_not_called()

    def test_old_tracker_lifecycle_is_not_reused_and_cloud_stop_not_mutated(self):
        previous={'positionIdentity':{'scope':'old-account','posId':'old'},'trailingStopPx':123}
        trackers={'key':copy.deepcopy(previous)}
        position={'instId':INST,'posSide':'long','posId':'new','cTime':'12345'}
        status=lifecycle.reconcile(trackers,'key',position,self.env.identity)
        self.assertEqual(status,'reset');self.assertNotIn('key',trackers)
        with evidence.connection() as db:
            saved=json.loads(db.execute("SELECT payload FROM events WHERE kind='tracker_lifecycle_reset'").fetchone()[0])
        self.assertEqual(saved['previous'],previous);self.assertFalse(saved['cloud_stop_changed'])

    def test_changed_management_position_or_account_binding_is_rejected(self):
        profile={'id':'cross-profile','execution_profile':'standard'}
        pos={'instId':INST,'posSide':'long','pos':'1','posId':'old','cTime':'1'}
        payload={'timestamp':AT,'account_scope':self.env.identity,'connection_id':self.env.connection_id,
                 'binding_version':self.env.binding_version,'strategy_profile_hash':trading_prompt.profile_signature(profile),
                 'execution_profile_signature':profiles.runtime(profile)['signature'],'position_basis':[pos]}
        with patch.object(account_connections,'assert_current'), patch.object(prompt_library,'active_profile',return_value=profile):
            authorization.validate_management(payload,self.env,{INST:pos},target=INST,now=AT)
            with self.assertRaises(risk.RiskRejected):
                authorization.validate_management(payload,self.env,{INST:{**pos,'posId':'new'}},target=INST,now=AT)
            with self.assertRaises(risk.RiskRejected):
                authorization.validate_management({**payload,'binding_version':0},self.env,{INST:pos},target=INST,now=AT)
