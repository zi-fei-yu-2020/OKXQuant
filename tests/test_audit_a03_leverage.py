"""Explicit scope-bound LIVE opt-in uses the unchanged DEMO 1M leverage policy."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import strategy_engine_runtime as engine, execution_leverage as leverage, strategy_evidence as evidence
from scripts import risk_policy as risk
from scripts.okx_runtime import OKXEnvironment

INST='BTC-USDT-SWAP'


class LiveLeverageTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(evidence,'DB_PATH',root/'e.db'))
        self.current=self.stack.enter_context(patch('okxquant_backend.account_connections.assert_current'))
        self.live=OKXEnvironment('live','a03-fake','fake','fake',connection_id='c1',binding_version=7,account_scope='live:account')
        self.demo=replace(self.live,mode='demo',account_scope='demo:account')
        self.binding={'environment':'live','enabled':True,'authorized':True,'status':'ready',
                      'engine_version':'demo-scalp-v2','account_scope':self.live.identity,
                      'connection_id':'c1','binding_version':7,'profile_signature':'profile-hash',
                      'execution_signature':'execution-hash','policy_signature':'policy-hash',
                      'record_id':'confirmed-live-1','limits':{'max_leverage':20}}
        self.reader=self.stack.enter_context(patch.object(engine,'status',return_value=self.binding))
        self.policy=risk.Policy(max_leverage=6,scalp_max_leverage=20,per_trade_equity_pct=.02)
        self.decision=evidence.append(self.live.identity,'decision',{
            'instrument':INST,'features':{'strategy_engine':'demo_scalp_v2'},
            'execution_profile_signature':'execution-hash',
            'decision':{'horizon':'scalp','strategy_engine':'demo_scalp_v2',
                        'engine_binding':{key:self.binding[key] for key in (*engine.BINDING_FIELDS,'record_id')}}})

    def request(self, *, held=None, pending=None, actual=10, timeout=False):
        def run(method,path,params,env):
            self.assertIs(env,self.live)
            if path.endswith('/positions'):return held or []
            if path.endswith('/orders-pending'):return pending or []
            if path.endswith('/set-leverage'):
                self.assertEqual(method,'POST')
                if timeout:raise TimeoutError('do-not-store-private-error')
                return [{'lever':str(params['lever'])}]
            if path.endswith('/leverage-info'):return [{'posSide':'long','lever':str(actual)}]
            raise AssertionError(path)
        return Mock(side_effect=run)

    def apply(self, request, decision=None, **kw):
        kw.setdefault('horizon','scalp')
        return leverage.apply(self.live,INST,'long',10,3,decision or self.decision,request,**kw)

    def test_opted_in_live_and_demo_have_identical_effective_policy_and_target(self):
        for cap in (12,20):
            with self.subTest(cap=cap):
                base=replace(self.policy,scalp_max_leverage=cap)
                demo=leverage.mode_policy(base,'scalp',self.demo)
                live=leverage.mode_policy(base,'scalp',self.live)
                self.assertEqual(vars(live),vars(demo))
                self.assertEqual(live.max_leverage,cap)
                self.assertEqual(leverage.choose(live,'scalp',self.live,3,[],{}),10)
                self.assertEqual(leverage.choose(live,'scalp',self.live,3,[],{'leverage':20}),cap)

    def test_effective_small300_risk_percentages_are_not_retuned(self):
        scalp=leverage.mode_policy(self.policy,'scalp',self.live)
        swing=leverage.mode_policy(self.policy,'swing',self.live)
        self.assertEqual(scalp.per_trade_equity_pct,.004)
        self.assertEqual(swing.per_trade_equity_pct,.005)
        self.assertEqual(300*scalp.per_trade_equity_pct,1.2)
        self.assertEqual(300*swing.per_trade_equity_pct,1.5)
        self.assertEqual(self.policy.per_trade_equity_pct,.02)

    def test_missing_gate_or_unreadable_gate_does_not_enable_live(self):
        for error in (ImportError('not installed'),OSError('private text')):
            with self.subTest(error=type(error).__name__):
                self.reader.side_effect=error
                self.assertEqual(leverage.mode_policy(self.policy,'scalp',self.live).max_leverage,6)
                self.assertEqual(leverage.choose(self.policy,'scalp',self.live,3,[],{'leverage':20}),3)
                request=self.request()
                with self.assertRaises(risk.RiskRejected):self.apply(request)
                request.assert_not_called()

    def test_wrong_scope_binding_version_disabled_or_unconfirmed_cannot_opt_in(self):
        cases=({'account_scope':'other'}, {'connection_id':'c2'}, {'binding_version':8},
               {'authorized':False}, {'authorized':'true'}, {'enabled':False},
               {'status':'confirmation_required'}, {'environment':'demo'},
               {'engine_version':'different'}, {'policy_signature':''},
               {'profile_signature':''}, {'execution_signature':''})
        for changes in cases:
            with self.subTest(changes=changes):
                self.reader.return_value={**self.binding,**changes}
                self.assertEqual(leverage.mode_policy(self.policy,'scalp',self.live).max_leverage,6)
                self.assertEqual(leverage.choose(self.policy,'scalp',self.live,3,[],{}),3)
                request=self.request()
                with self.assertRaises(risk.RiskRejected):self.apply(request)
                request.assert_not_called()

    def test_demo_never_depends_on_live_authorization(self):
        self.reader.side_effect=AssertionError('DEMO must not inspect live opt-in')
        policy=leverage.mode_policy(self.policy,'scalp',self.demo)
        self.assertEqual(policy.max_leverage,20)
        self.assertEqual(leverage.choose(policy,'scalp',self.demo,3,[],{}),10)
        self.reader.assert_not_called()

    def test_live_one_minute_authorization_does_not_grant_swing_auto_leverage(self):
        policy=leverage.mode_policy(self.policy,'swing',self.live)
        self.assertEqual(policy.max_leverage,5)
        self.assertEqual(leverage.choose(policy,'swing',self.live,3,[],{'leverage':5}),3)
        request=self.request()
        with self.assertRaises(risk.RiskRejected):self.apply(request,horizon='swing')
        request.assert_not_called()

    def test_existing_position_keeps_current_leverage_even_when_opted_in(self):
        policy=leverage.mode_policy(self.policy,'scalp',self.live)
        self.reader.reset_mock()
        self.assertEqual(leverage.choose(policy,'scalp',self.live,3,[{'pos':'1'}],{'leverage':20}),3)
        self.reader.assert_not_called()
        for side in ('long','short'):
            request=self.request(held=[{'instId':INST,'posSide':side,'pos':'1'}])
            with self.assertRaisesRegex(risk.RiskRejected,'exposure'):self.apply(request)
            self.assertFalse(any(c.args[0]=='POST' for c in request.call_args_list))

    def test_pending_entry_prevents_releveraging_instrument(self):
        request=self.request(pending=[{'instId':INST,'ordId':'pending'}])
        with self.assertRaisesRegex(risk.RiskRejected,'exposure'):self.apply(request)
        self.assertFalse(any(c.args[0]=='POST' for c in request.call_args_list))

    def test_authorized_apply_writes_once_and_reads_back_under_same_scope(self):
        request=self.request()
        self.assertEqual(self.apply(request),10)
        writes=[c for c in request.call_args_list if c.args[0]=='POST']
        self.assertEqual(len(writes),1)
        self.assertEqual(writes[0].args[2],{'instId':INST,'mgnMode':'cross','lever':'10'})
        events=evidence.export_events(self.live.identity,'leverage_change_intent')
        self.assertEqual(events[-1]['payload']['live_binding']['account_scope'],self.live.identity)
        self.assertEqual(events[-1]['payload']['live_binding']['policy_signature'],'policy-hash')

    def test_timeout_is_read_reconciled_without_second_write(self):
        request=self.request(timeout=True)
        self.assertEqual(self.apply(request),10)
        self.assertEqual(sum(c.args[0]=='POST' for c in request.call_args_list),1)
        self.assertNotIn('do-not-store-private-error',str(evidence.export_events(self.live.identity)))

    def test_unverified_readback_retains_one_write_only(self):
        request=self.request(actual=3,timeout=True)
        with self.assertRaisesRegex(risk.RiskRejected,'not verified'):self.apply(request)
        self.assertEqual(sum(c.args[0]=='POST' for c in request.call_args_list),1)

    def test_no_durable_same_scope_minute_decision_means_no_live_leverage_write(self):
        for decision in ('missing', evidence.append('other','decision',{'decision':{'horizon':'scalp'}}),
                         evidence.append(self.live.identity,'decision',{'instrument':INST,'decision':{'horizon':'swing'}})):
            with self.subTest(decision=decision):
                request=self.request()
                with self.assertRaises(risk.RiskRejected):self.apply(request,decision=decision)
                request.assert_not_called()

    def test_durable_decision_must_match_instrument_and_execution_configuration(self):
        for changes in ({'instrument':'ETH-USDT-SWAP'}, {'execution_profile_signature':'stale'}):
            record={'instrument':INST,'features':{'strategy_engine':'demo_scalp_v2'},
                    'decision':{'horizon':'scalp','engine_binding':{key:self.binding[key] for key in (*engine.BINDING_FIELDS,'record_id')}},
                    'execution_profile_signature':'execution-hash',**changes}
            decision=evidence.append(self.live.identity,'decision',record)
            request=self.request()
            with self.assertRaises(risk.RiskRejected):self.apply(request,decision=decision)
            request.assert_not_called()

    def test_revocation_or_changed_authorization_before_dispatch_never_writes(self):
        for after in ({**self.binding,'authorized':False},{**self.binding,'policy_signature':'new-policy'}):
            with self.subTest(after=after):
                self.reader.side_effect=[self.binding,after]
                request=self.request()
                with self.assertRaises(risk.RiskRejected):self.apply(request)
                self.assertFalse(any(c.args[0]=='POST' for c in request.call_args_list))

    def test_noop_does_not_change_existing_live_leverage_or_need_authorization(self):
        self.reader.return_value={**self.binding,'authorized':False}
        request=self.request()
        self.assertEqual(leverage.apply(self.live,INST,'long',3,3,'missing',request),3)
        request.assert_not_called()
