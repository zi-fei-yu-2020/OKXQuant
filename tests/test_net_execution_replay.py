import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace
from scripts import execution_replay as replay,holding_costs,execution_costs,scalp_management as management
from scripts import strategy_evidence as evidence,profit_exit_execution,close_execution
from scripts import joint_trade_replay
from scripts import profit_protection
from scripts import public_market
from scripts.position_lifecycle import identity
from scripts.risk_policy import Policy
from scripts.okx_runtime import OKXEnvironment

INST='TEST-USDT-SWAP'


def position(side='long',size=2):
    return {'instId':INST,'posSide':side,'posId':'p','cTime':'100000','pos':str(size),'avgPx':'100','markPx':'103','upl':'6'}


class JointReplayTests(unittest.TestCase):
    def setUp(self):
        self.geometry={'signal_entry':100,'submitted_entry':100.1,'stop':99,'target':103,'side':'long'}
        self.history={'direction':'long','openAvgPx':'100.4','cTime':'100000','pnl':'1','realizedPnl':'-.1'}

    def test_deterioration_and_net_cost_loss_are_independent(self):
        result=replay.analyze(self.geometry,self.history,fees_verified=True)
        self.assertEqual(result['classification'],'execution_deterioration')
        self.assertAlmostEqual(result['actual_risk'],1.4)
        self.assertAlmostEqual(result['actual_reward'],2.6)
        self.assertTrue(result['cost_only_loss']);self.assertFalse(result['order_authorized'])

    def test_settled_cost_geometry_uses_actual_contract_units_and_is_labeled_retrospective(self):
        result=replay.analyze({**self.geometry,'contract_base_units':.1},
            {**self.history,'fee':'-.2','closeTotalPos':'2'},fees_verified=True)
        self.assertAlmostEqual(result['settled_fee_distance'],1.)
        self.assertAlmostEqual(result['net_target_distance_at_settled_fee'],1.6)
        self.assertEqual(result['cost_semantics'],'retrospective_total_fees_not_signal_time_forecast')

    def test_favorable_short_fill_is_not_execution_deterioration(self):
        frozen={**self.geometry,'side':'short','stop':101,'target':97}
        result=replay.analyze(frozen,{**self.history,'direction':'short','openAvgPx':'100.2'})
        self.assertLess(result['entry_deterioration_r'],0)
        self.assertNotEqual(result['classification'],'execution_deterioration')

    def test_missing_geometry_and_crossed_target_are_explicit(self):
        self.assertEqual(replay.analyze(None,self.history)['status'],'unavailable')
        self.assertEqual(replay.analyze(self.geometry,{**self.history,'openAvgPx':104})['classification'],'geometry_invalid_at_fill')

    def test_failure_requires_observed_evidence_not_a_loss(self):
        history={**self.history,'openAvgPx':100,'pnl':-5}
        self.assertNotEqual(replay.analyze(self.geometry,history)['classification'],'observed_structure_failure')
        result=replay.analyze(self.geometry,history,[{'management_state':{'exit':True,'peak_gain':.5}}])
        self.assertEqual(result['classification'],'observed_structure_failure')
        self.assertEqual(result['sampled_peak_gain'],.5)

    def test_frozen_stop_breach_is_explicit_without_claiming_a_model_failure(self):
        result=replay.analyze(self.geometry,{**self.history,'openAvgPx':100,'closeAvgPx':98.9})
        self.assertEqual(result['classification'],'frozen_stop_breach_at_exit')
        self.assertTrue(result['frozen_stop_breached_at_exit'])

    def test_same_bar_double_touch_and_entry_partial_bar_are_not_guessed(self):
        bars=[{'close_ms':120000,'low':98,'high':104}, {'close_ms':180000,'low':98,'high':104}]
        result=replay.touch_path(bars,entry_ms=100000,stop=99,target=103,side='long')
        self.assertEqual(result['status'],'ambiguous');self.assertEqual(result['close_ms'],180000)
        self.assertEqual(replay.touch_path(bars[1:],entry_ms=0,stop=99,target=103,side='long')['status'],'incomplete_path')

    def test_unscoped_receipts_are_reported_as_unknown_not_dropped(self):
        result=joint_trade_replay.report([{'status':'closed','id':'one'}])
        self.assertEqual(result['trades'],1);self.assertEqual(result['classifications'],{'insufficient_evidence':1})

    def test_read_only_archive_restores_frozen_geometry_without_recomputing_candidates(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(evidence,'DB_PATH',Path(directory)/'evidence.db'):
            decision={'decision':{'candidate_id':'c','entry_plans':{'plans':[{'id':'c','setup':'scalp_breakout_1m','entry_price':100}]}}}
            did=evidence.append('demo','decision',decision)
            evidence.append('demo','entry_submission',{'plan':{'decision_id':did,'candidate_id':'c','side':'long','entry':100.1,'stop':99,'take_profit':103},'response':[{'ordId':'12'}]})
            row={'id':'one','status':'closed','environment_id':'demo','instId':INST,'pos_id':'p','position_created_at':'100000','opening_order_ids':['12'],'exit_snapshot':self.history}
            result=joint_trade_replay.report([row],evidence_db=evidence.DB_PATH)
            self.assertEqual(result['rows'][0]['replay']['classification'],'execution_deterioration')
            self.assertEqual(result['rows'][0]['replay']['signal_entry'],100)
            self.assertFalse(result['profitability_backtest'])


class HoldingCostsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        patcher=patch.object(evidence,'DB_PATH',Path(self.temp.name)/'evidence.db');patcher.start();self.addCleanup(patcher.stop)
        self.pos=position();self.tracker={'positionIdentity':identity(self.pos,'demo'),'decision_id':'d'}
        evidence.append('demo','entry_submission',{'plan':{'instId':INST,'side':'long','decision_id':'d'},'response':[{'ordId':'12'}]})
        self.fill={'instId':INST,'posSide':'long','side':'buy','ordId':'12','tradeId':'a','fillSz':'2','fillPx':'100','fee':'-.04','feeCcy':'USDT','fillTime':'100000'}
        evidence.append('demo','fill',self.fill)

    def test_duplicate_fills_are_deduplicated_and_account_bound(self):
        evidence.append('demo','fill',self.fill)
        receipt=holding_costs.reconcile(self.pos,self.tracker,'demo',.1)
        self.assertEqual(receipt['status'],'verified');self.assertEqual(receipt['base_units'],.2)
        self.assertEqual(receipt['opening_fee'],-.04)
        self.assertEqual(holding_costs.reconcile(self.pos,self.tracker,'other',.1)['status'],'unavailable')

    def test_partial_exit_scale_missing_currency_and_conflicts_fall_back(self):
        self.assertEqual(holding_costs.reconcile(position(size=1),self.tracker,'demo',.1)['status'],'unavailable')
        evidence.append('demo','fill',{**self.fill,'fee':'-.05'})
        self.assertEqual(holding_costs.reconcile(self.pos,self.tracker,'demo',.1)['status'],'unavailable')

    def test_bad_digest_is_not_accepted(self):
        with evidence.connection() as db:db.execute("UPDATE events SET digest='bad' WHERE kind='fill'")
        self.assertEqual(holding_costs.reconcile(self.pos,self.tracker,'demo',.1)['status'],'unavailable')

    def test_foreign_fee_currency_and_incomplete_quantity_never_claim_verified(self):
        with evidence.connection() as db:db.execute("DELETE FROM events WHERE kind='fill'")
        evidence.append('demo','fill',{**self.fill,'feeCcy':'BTC'})
        self.assertEqual(holding_costs.reconcile(self.pos,self.tracker,'demo',.1)['status'],'unavailable')
        with evidence.connection() as db:db.execute("DELETE FROM events WHERE kind='fill'")
        evidence.append('demo','fill',{**self.fill,'fillSz':'1'})
        self.assertEqual(holding_costs.reconcile(self.pos,self.tracker,'demo',.1)['status'],'unavailable')

    def test_cost_estimate_is_replaced_not_double_counted(self):
        receipt=holding_costs.reconcile(self.pos,self.tracker,'demo',.1)
        cost=execution_costs.holding_budget(100,102,Policy(),receipt)
        self.assertEqual(cost['opening_cost_basis'],'verified_opening_fills')
        self.assertAlmostEqual(cost['opening_cost'],.2)
        self.assertAlmostEqual(cost['total_cost_distance'],.2+.051+.1)
        fallback=execution_costs.holding_budget(100,102,Policy(),None)
        self.assertAlmostEqual(fallback['total_cost_distance'],.05+.051+.1)

    def test_unavailable_cache_is_throttled_and_lifecycle_changes_invalidate_it(self):
        with patch.object(holding_costs,'reconcile',return_value={'status':'unavailable','identity':identity(self.pos,'demo'),'contracts':2}) as read:
            holding_costs.refresh(self.pos,self.tracker,'demo',.1,1000)
            holding_costs.refresh(self.pos,self.tracker,'demo',.1,1001)
            self.assertEqual(read.call_count,1)
            holding_costs.refresh({**self.pos,'cTime':'200000'},self.tracker,'demo',.1,1002)
            self.assertEqual(read.call_count,2)


class NetManagementTests(unittest.TestCase):
    def evaluate(self,setup,peak=1.5,side='long',saved=None):
        sign=1 if side=='long' else -1
        candidate={'setup':setup,'action':'BUY_LONG' if sign==1 else 'SELL_SHORT','id':'c','entry_price':100,'trigger_level':100,'entry_atr':.2,'take_profit_price':100+sign*3}
        ctx=management.context(candidate,{'instId':INST},scope='demo',decision_id='d',stop=100-sign)
        tracker=saved or {'entry_context':ctx,'positionIdentity':identity(position(side),'demo'),'decision_id':'d','highWaterMark':100+peak,'lowWaterMark':100-peak}
        return management.evaluate(tracker,{},entry=100,current=100+sign*peak,side=side,now=100,policy=Policy(),tick=.01),tracker

    def test_setup_specific_net_floor_is_above_cost_coverage(self):
        breakout,_=self.evaluate('scalp_breakout_1m');range_exit,_=self.evaluate('scalp_range_reversion_1m')
        self.assertTrue(breakout['protection']['active'])
        self.assertGreater(breakout['protection']['retained_gain'],breakout['cost_distance'])
        self.assertGreater(range_exit['protection']['retained_gain'],breakout['protection']['retained_gain'])
        self.assertAlmostEqual(breakout['protection']['retained_gain']-breakout['cost_distance'],max(breakout['minimum_net_profit_distance'],breakout['peak_net_gain']*.35))

    def test_tightened_stop_survives_restart_and_cost_changes_without_widening(self):
        result,tracker=self.evaluate('scalp_breakout_1m')
        tracker['scalpManagement']=result
        saved=json.loads(json.dumps(tracker));saved['highWaterMark']=100.5
        again,_=self.evaluate('scalp_breakout_1m',peak=1.,saved=saved)
        self.assertGreaterEqual(again['protection']['stop'],result['protection']['stop'])

    def test_long_short_profiles_are_symmetric(self):
        long,_=self.evaluate('scalp_range_reversion_1m');short,_=self.evaluate('scalp_range_reversion_1m',side='short')
        self.assertAlmostEqual(long['protection']['retained_gain'],short['protection']['retained_gain'],places=2)

    def test_standard_and_small_capital_use_identical_per_unit_cost_geometry(self):
        for units in (.01,10):
            _,tracker=self.evaluate('scalp_breakout_1m')
            tracker['openingCostReceipt']={'status':'verified','identity':tracker['positionIdentity'],'decision_id':'d','base_units':units,'opening_fee':-units*.02}
            result,_=self.evaluate('scalp_breakout_1m',saved=tracker)
            self.assertAlmostEqual(result['cost_model']['opening_cost'],.02)
            self.assertGreater(result['protection']['retained_gain'],result['cost_distance'])

    def test_swing_cost_budget_also_retains_net_surplus_not_just_fees(self):
        budget=execution_costs.holding_budget(100,102,Policy())
        budget['minimum_net_profit_distance']=.2
        result=profit_protection.floor_plan('long',100,102,102,99,.2,cost_budget=budget)
        self.assertTrue(result['active'])
        self.assertGreaterEqual(result['retained_net_gain'],.2)
        self.assertFalse(profit_protection.allow_ai_tightening('long',100,102,100.25,.2,cost_budget=budget))
        self.assertTrue(profit_protection.allow_ai_tightening('long',100,102,100.8,.2,cost_budget=budget))

    def test_range_target_approach_is_a_normal_profitable_exit_not_a_stop(self):
        _,tracker=self.evaluate('scalp_range_reversion_1m',peak=2.8)
        bars=[{'close_ms':180000,'open':102.2,'close':102.4},
              {'close_ms':240000,'open':102.4,'close':102.6},
              {'close_ms':300000,'open':102.6,'close':102.8}]
        result=management.evaluate(tracker,{'closed_1m':bars},entry=100,current=102.8,side='long',now=305,policy=Policy(),tick=.01)
        self.assertFalse(result['exit']);self.assertTrue(result['protection']['kinetic_exit'])
        self.assertEqual(result['normal_profit_reason'],'range_target_approach')
        self.assertFalse(result['protection']['crossed'])

    def test_breakout_waits_for_confirmed_exhaustion_and_remains_above_net_floor(self):
        _,tracker=self.evaluate('scalp_breakout_1m',peak=3.)
        tracker['entry_context']['target']=104
        bars=[{'close_ms':180000,'open':102.5,'close':102.9},
              {'close_ms':240000,'open':102.9,'close':102.75},
              {'close_ms':300000,'open':102.75,'close':102.5}]
        result=management.evaluate(tracker,{'closed_1m':bars},entry=100,current=102.5,side='long',now=305,policy=Policy(),tick=.01)
        self.assertTrue(result['protection']['kinetic_exit'])
        self.assertEqual(result['normal_profit_reason'],'confirmed_continuation_exhaustion')
        bars[-1]['open']=102.4
        result=management.evaluate(tracker,{'closed_1m':bars},entry=100,current=102.5,side='long',now=305,policy=Policy(),tick=.01)
        self.assertFalse(result['protection']['kinetic_exit'])

    def test_real_cost_receipt_must_match_lifecycle_and_decision(self):
        _,tracker=self.evaluate('scalp_breakout_1m')
        receipt={'status':'verified','identity':tracker['positionIdentity'],'decision_id':'d','base_units':2,'opening_fee':-.04}
        tracker['openingCostReceipt']=receipt
        result,_=self.evaluate('scalp_breakout_1m',saved=tracker)
        self.assertEqual(result['cost_model']['opening_cost_basis'],'verified_opening_fills')
        tracker['openingCostReceipt']={**receipt,'decision_id':'other'}
        result,_=self.evaluate('scalp_breakout_1m',saved=tracker)
        self.assertEqual(result['cost_model']['opening_cost_basis'],'conservative_estimate')

    def test_corrupted_frozen_profile_does_not_throw_or_authorize_an_exit(self):
        _,tracker=self.evaluate('scalp_breakout_1m')
        tracker['entry_context']['management_profile']['retain']='invalid'
        result,_=self.evaluate('scalp_breakout_1m',saved=tracker)
        self.assertFalse(result['enabled']);self.assertFalse(result['exit'])


class PassiveProfitExitTests(unittest.TestCase):
    def setUp(self):
        self.env=SimpleNamespace(identity='demo');self.pos=position()
        self.budget={'identity':identity(self.pos,'demo'),'opening_cost_basis':'verified_opening_fills',
                     'tick':.01,'total_cost_distance':.2,'minimum_net_profit_distance':.2,
                     'opening_receipt':{'status':'verified','identity':identity(self.pos,'demo'),'contracts':2,'opening_vwap':100}}
        self.quote={'instId':INST,'bidPx':'102','askPx':'102.01','ts':'1000000'}
        patcher=patch.object(evidence,'append');patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(evidence,'best_effort');patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(profit_exit_execution.time,'time',return_value=1000);patcher.start();self.addCleanup(patcher.stop)

    def attempt(self,request,reason='trailing_exit'):
        return profit_exit_execution.attempt(self.env,self.pos,reason,self.budget,request=request,sleep=Mock(),
            quote_reader=lambda:request('PUBLIC_QUOTE','/mock/public-market',{},self.env))

    def test_hard_stops_and_safety_exits_never_wait_for_maker(self):
        for reason in ['hard_stop','risk_reduction_exit','profit_lock','oco_unverified','strategy_failure_exit']:
            request=Mock();self.assertEqual(self.attempt(request,reason)['status'],'market_allowed');request.assert_not_called()

    def test_filled_maker_is_terminal_and_reduce_only(self):
        request=Mock(side_effect=[[self.quote],[{'ordId':'123','sCode':'0'}],[{'instId':INST,'ordId':'123','state':'filled'}]])
        self.assertEqual(self.attempt(request)['status'],'terminal')
        payload=request.call_args_list[1].args[2]
        self.assertTrue(payload['reduceOnly']);self.assertEqual(payload['ordType'],'post_only')

    def test_default_quote_transport_never_uses_private_rest_allowlist(self):
        request=Mock(side_effect=[[{'ordId':'123'}],[{'instId':INST,'ordId':'123','state':'filled'}]])
        with patch.object(public_market,'get_json',return_value={'code':'0','data':[self.quote]}) as public:
            result=profit_exit_execution.attempt(self.env,self.pos,'trailing_exit',self.budget,request=request,sleep=Mock())
        self.assertEqual(result['status'],'terminal');public.assert_called_once()
        self.assertEqual(request.call_args_list[0].args[:2],('POST','/api/v5/trade/order'))
        self.assertFalse(any(call.args[1]=='/api/v5/market/ticker' for call in request.call_args_list))

    def test_sdk_business_rejection_is_distinct_from_transport_unknown(self):
        from okxquant_backend.okx_trade_service import OKXAPIError
        request=Mock(side_effect=[[self.quote],OKXAPIError('51000','post-only rejected')])
        result=self.attempt(request)
        self.assertEqual(result['status'],'market_allowed');self.assertTrue(result['refresh_required'])

    def test_unknown_ack_cancel_or_nonterminal_status_never_allows_fallback(self):
        scripts=[[[self.quote],TimeoutError()],[[self.quote],[{'ordId':'123'}],[],],
                 [[self.quote],[{'ordId':'123'}],[{'instId':INST,'ordId':'123','state':'live'}],TimeoutError()],
                 [[self.quote],[{'ordId':'123'}],[{'instId':INST,'ordId':'123','state':'live'}],[{'sCode':'0'}],[{'instId':INST,'ordId':'123','state':'live'}]]]
        for responses in scripts:
            self.assertEqual(self.attempt(Mock(side_effect=responses))['status'],'unknown')

    def test_partial_fill_requires_terminal_cancel_before_residual_fallback(self):
        request=Mock(side_effect=[[self.quote],[{'ordId':'123'}],[{'instId':INST,'ordId':'123','state':'partially_filled','accFillSz':'1'}],[{'sCode':'0'}],[{'instId':INST,'ordId':'123','state':'canceled','accFillSz':'1'}]])
        self.assertEqual(self.attempt(request)['status'],'terminal');self.assertEqual(request.call_count,5)

    def test_cost_floor_stale_quote_and_missing_receipt_skip_passive(self):
        request=Mock(return_value=[{**self.quote,'bidPx':'100.1'}]);self.assertEqual(self.attempt(request)['status'],'market_allowed')
        self.assertEqual(request.call_count,1)
        request=Mock(return_value=[{**self.quote,'ts':'900000'}]);self.assertEqual(self.attempt(request)['status'],'market_allowed')

    def test_journal_failure_never_sends_passive_and_invalid_quotes_are_skipped(self):
        request=Mock(return_value=[self.quote])
        with patch.object(evidence,'append',side_effect=OSError()):
            self.assertEqual(self.attempt(request)['status'],'market_allowed')
        self.assertFalse(any(call.args[0]=='POST' for call in request.call_args_list))
        for quote in ({**self.quote,'askPx':'nan'},{**self.quote,'bidPx':'103'}):
            request=Mock(return_value=[quote]);self.assertEqual(self.attempt(request)['status'],'market_allowed')

    def test_position_growth_and_missing_receipt_skip_passive_before_any_exchange_call(self):
        request=Mock()
        self.pos['pos']='3'
        self.assertEqual(self.attempt(request)['status'],'market_allowed');request.assert_not_called()
        self.pos['pos']='2';self.budget.pop('opening_receipt')
        self.assertEqual(self.attempt(request)['status'],'market_allowed');request.assert_not_called()


class PassiveCloseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.env=OKXEnvironment('demo','fake','fake','fake');self.pos=position()
        for target in ['scripts.close_evidence.record_close','scripts.strategy_evidence.best_effort','scripts.close_execution.time.sleep']:
            patcher=patch(target);patcher.start();self.addCleanup(patcher.stop)

    def test_market_fallback_uses_fresh_residual_only(self):
        request=Mock(side_effect=[[],[],[self.pos],[position(size=1)],[{'ordId':'456','sCode':'0'}],[]])
        with patch.object(profit_exit_execution,'attempt',return_value={'status':'terminal','order_ids':['123']}):
            closed,_=close_execution.close(self.env,INST,'long',2,self.pos,'trailing_exit',request=request,profit_budget={})
        self.assertTrue(closed)
        writes=[call for call in request.call_args_list if call.args[0]=='POST']
        self.assertEqual(len(writes),1);self.assertEqual(writes[0].args[2]['sz'],'1')

    def test_unknown_passive_does_not_dispatch_market(self):
        request=Mock(side_effect=[[],[],[self.pos]])
        with patch.object(profit_exit_execution,'attempt',return_value={'status':'unknown','order_ids':['123']}):
            closed,_=close_execution.close(self.env,INST,'long',2,self.pos,'trailing_exit',request=request,profit_budget={})
        self.assertFalse(closed);self.assertFalse(any(call.args[0]=='POST' for call in request.call_args_list))

    def test_new_lifecycle_after_passive_never_gets_a_stale_market_close(self):
        request=Mock(side_effect=[[],[],[self.pos],[{**self.pos,'cTime':'200000'}]])
        with patch.object(profit_exit_execution,'attempt',return_value={'status':'terminal','order_ids':['123']}):
            closed,_=close_execution.close(self.env,INST,'long',2,self.pos,'trailing_exit',request=request,profit_budget={})
        self.assertFalse(closed);self.assertFalse(any(call.args[0]=='POST' for call in request.call_args_list))

    def test_position_growth_after_passive_does_not_close_new_exposure(self):
        request=Mock(side_effect=[[],[],[self.pos],[position(size=3)]])
        with patch.object(profit_exit_execution,'attempt',return_value={'status':'terminal','order_ids':['123']}):
            closed,_=close_execution.close(self.env,INST,'long',2,self.pos,'trailing_exit',request=request,profit_budget={})
        self.assertFalse(closed);self.assertFalse(any(call.args[0]=='POST' for call in request.call_args_list))
