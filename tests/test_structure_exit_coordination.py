import copy,json,unittest
from unittest.mock import patch,Mock
from scripts import structure_targets as targets,exit_coordination as coordination,swing_management as swing
from scripts import entry_candidates,scalp_candidates,scalp_management,exit_policy,risk_policy,profit_exit_execution,strategy_evidence
from test_entry_candidates import package
from test_demo_scalp import minute_package
from test_net_execution_replay import position
from scripts.position_lifecycle import identity
from types import SimpleNamespace
from contextlib import ExitStack

def candles(width=60000,closes=(101.5,101.4),created=600000):
    return [{'close_ms':created+(i+1)*width,'open':v+.1,'high':v+.2,'low':v-.2,'close':v} for i,v in enumerate(closes)]

def plan(side='long',setup='pullback_reclaim'):
    sign=1 if side=='long' else -1
    return {'id':'candidate','action':'BUY_LONG' if sign==1 else 'SELL_SHORT','setup':setup,
        'horizon':'swing','entry_price':100.,'stop_loss_price':100-sign,'take_profit_price':100+sign*10,
        'entry_atr':.2,'entry_atr_1h':2.,'trigger_level':100,'stop_basis':'original',
        'invalidation':{'price':100-sign},'target_observation':{'extrapolated':False,'price':100+sign*10}}

class TargetAndGeometryTests(unittest.TestCase):
    def test_observed_near_and_extension_are_separate_and_trigger_is_excluded(self):
        p=plan();rows=candles(closes=(101.4,102.4,100.))
        rows[-1]['high']=100.4
        result=targets.layers(p,{'15M':rows},vars(risk_policy.Policy()))
        self.assertAlmostEqual(result['near']['price'],101.6)
        self.assertFalse(result['near']['extrapolated'])
        self.assertEqual(result['extension']['price'],110)
        self.assertFalse(result['entry_gate_changed'])
        self.assertNotIn('target_layers',p)

    def test_no_observed_space_does_not_manufacture_target(self):
        rows=candles(closes=(99.,99.1,100.))
        self.assertIsNone(targets.layers(plan(),{'1H':rows},vars(risk_policy.Policy()))['near'])

    def test_short_near_is_symmetric(self):
        p=plan('short');rows=candles(closes=(98.6,97.6,100.))
        result=targets.layers(p,{'15M':rows},vars(risk_policy.Policy()))
        self.assertAlmostEqual(result['near']['price'],98.4)

    def test_new_stop_bounded_and_target_unchanged_when_original_rr_supports_it(self):
        p=plan();rows=candles(closes=tuple(100. for _ in range(20)))
        for r in rows:r.update(high=102.,low=98.)
        targets.calibrate_stop(p,{'1H':rows},vars(risk_policy.Policy()))
        self.assertTrue(p['structure_stop']['applied'])
        self.assertEqual(p['stop_loss_price'],98.25)
        self.assertEqual(p['take_profit_price'],110)
        self.assertEqual(p['invalidation']['price'],p['stop_loss_price'])
        self.assertGreaterEqual(p['net_rr'],2.)

    def test_tight_reward_retains_candidate_and_original_stop(self):
        p=plan();p['take_profit_price']=102.6
        rows=candles(closes=tuple(100. for _ in range(20)))
        for r in rows:r.update(high=102.,low=98.)
        targets.calibrate_stop(p,{'1H':rows},vars(risk_policy.Policy()))
        self.assertFalse(p['structure_stop']['applied']);self.assertEqual(p['stop_loss_price'],99.)
        self.assertEqual(p['take_profit_price'],102.6)

    def test_catalog_keeps_all_existing_setup_direction_opportunities(self):
        for side in ('long','short'):
            for catalog,p in ((entry_candidates.catalog,package(side)),(scalp_candidates.catalog,minute_package(side))):
                before=copy.deepcopy(p);policy=vars(risk_policy.Policy())
                if catalog is scalp_candidates.catalog:policy={**policy,'minimum_net_rr':1.2}
                with patch.object(targets,'calibrate_stop'),patch.object(targets,'layers',return_value=None):
                    original=catalog(p,policy)['plans']
                actual=catalog(p,policy)['plans']
                self.assertEqual([(x['setup'],x['action']) for x in original],[(x['setup'],x['action']) for x in actual])
                self.assertEqual(before,p)
                for x in actual:self.assertFalse(x['order_authorized'])

    def test_larger_stop_resizes_risk_for_standard_and_small_equity(self):
        meta={'instId':'TEST-USDT-SWAP','state':'live','ctVal':'1','ctMult':'1','ctValCcy':'TEST','settleCcy':'USDT','ctType':'linear','lotSz':'.01','minSz':'.01','tickSz':'.01'}
        for equity in (300.,5000.):
            policy=risk_policy.Policy();args=dict(metadata=meta,side='long',entry=100.,take_profit=110.,requested_size=100.,budget_usdt=equity*.005,equity=equity,available=equity,leverage=3,policy=policy)
            original=risk_policy.order_plan(stop=99.,**args);new=risk_policy.order_plan(stop=98.25,**args)
            self.assertLessEqual(new['size'],original['size'])
            self.assertLessEqual(new['risk_usdt'],new['risk_budget_usdt'])

class ExitCoordinationTests(unittest.TestCase):
    def decide(self,**changes):
        args=dict(side='long',entry=100.,current=101.4,peak=101.8,atr=.2,tick=.01,costs=.1,minimum_net=.2,stop=101.1,
            targets={'near':{'price':101.5}},bars=candles())
        args.update(changes);return coordination.normal_profit(**args)

    def test_observed_near_target_exhaustion_can_exit_before_cloud_stop(self):
        r=self.decide();self.assertTrue(r['eligible']);self.assertEqual(r['reason'],'observed_near_target_exhaustion');self.assertTrue(r['cloud_protection_retained'])

    def test_trend_continuation_does_not_exit_only_for_touching_near_target(self):
        bars=candles();bars[-1].update(open=101.2,close=101.6)
        self.assertFalse(self.decide(bars=bars)['eligible'])

    def test_cloud_floor_approach_without_near_target_can_preempt(self):
        r=self.decide(targets=None,stop=101.3)
        self.assertTrue(r['eligible']);self.assertEqual(r['reason'],'cloud_floor_preemptive_profit')

    def test_crossed_floor_cost_shortfall_and_no_bars_never_authorize_passive_profit(self):
        for changes in ({'stop':101.5},{'current':100.2},{'bars':[]},{'current':float('nan')},{'entry':True}):
            self.assertFalse(self.decide(**changes)['eligible'])

    def test_partial_stale_gapped_or_future_bars_are_not_confirmation(self):
        rows=candles()
        self.assertEqual(len(coordination.complete_bars(rows,630000,725,60000)),1)
        self.assertFalse(coordination.complete_bars(rows,600000,900,60000))
        self.assertFalse(coordination.complete_bars(rows,600000,710,60000))
        rows[-1]['close_ms']+=60000
        self.assertFalse(coordination.complete_bars(rows,600000,785,60000))

    def test_short_exit_mirrors_long(self):
        bars=candles(closes=(98.5,98.6));bars[-1]['open']=98.5
        self.assertTrue(self.decide(side='short',current=98.6,peak=98.2,stop=98.9,targets={'near':{'price':98.5}},bars=bars)['eligible'])

class SwingContextTests(unittest.TestCase):
    def fixture(self,side='long'):
        p=plan(side);ctx=swing.context(p,{'instId':'TEST-USDT-SWAP'},scope='demo',decision_id='d',stop=p['stop_loss_price'])
        tracker={'entry_context':ctx,'decision_id':'d','positionIdentity':{'scope':'demo','instId':'TEST-USDT-SWAP','side':side,'cTime':'600000','posId':'p'},
                 'highWaterMark':100.1,'lowWaterMark':99.9,'trailingStopPx':p['stop_loss_price'],'exchangeStopPx':p['stop_loss_price']}
        return tracker

    def evaluate(self,t,**changes):
        args=dict(entry=100.,current=99.5,side='long',now=2405,policy=risk_policy.Policy(),tick=.01,thresholds=exit_policy.HORIZON_PRESETS['swing']);args.update(changes)
        factor={'atr_1h':2.,'closed_15m':candles(900000,(99.5,99.4))}
        return swing.evaluate(t,factor,**args)

    def test_binding_and_restart_survive_without_mutating_original_stop(self):
        t=self.fixture();copy_t=json.loads(json.dumps(t));r=self.evaluate(copy_t)
        self.assertTrue(r['enabled']);self.assertEqual(r['volatility_source'],'atr_1h')
        self.assertEqual(copy_t,t)
        for field in ('scope','instId','side'):
            other=copy.deepcopy(t);other['positionIdentity'][field]='other'
            self.assertFalse(self.evaluate(other)['enabled'])

    def test_swing_failure_requires_post_fill_15m_not_minute_noise(self):
        t=self.fixture();self.assertTrue(self.evaluate(t)['exit'])
        factor={'atr_1h':2.,'closed_1m':candles(closes=(99.4,99.3)),'closed_15m':[]}
        r=swing.evaluate(t,factor,entry=100,current=99.3,side='long',now=725,policy=risk_policy.Policy(),tick=.01,thresholds=exit_policy.HORIZON_PRESETS['swing'])
        self.assertFalse(r['exit'])

    def test_missing_hour_atr_does_not_invent_volatility(self):
        self.assertFalse(swing.evaluate(self.fixture(),{},entry=100,current=101,side='long',now=2405,policy=risk_policy.Policy(),tick=.01,thresholds=exit_policy.HORIZON_PRESETS['swing'])['enabled'])

    def test_adoption_rejects_other_account_direction_time_and_decision(self):
        t=self.fixture();saved={'entry_context':t['entry_context'],'decision_id':'d','ts':600}
        p={'instId':'TEST-USDT-SWAP','posSide':'long','cTime':'600000'}
        self.assertTrue(swing.adopt({},saved,p,'demo'))
        self.assertFalse(swing.adopt({},saved,p,'other'))
        self.assertFalse(swing.adopt({},saved,{**p,'cTime':'9999999'},'demo'))
        self.assertFalse(swing.adopt({},saved,{**p,'posSide':'short'},'demo'))
        self.assertFalse(swing.adopt({},dict(saved,decision_id='x'),p,'demo'))
        self.assertFalse(swing.adopt(t,saved,p,'demo'))

    def test_recovery_is_not_closed_from_previous_adverse_bars(self):
        self.assertFalse(self.evaluate(self.fixture(),current=100.1)['exit'])

class PassiveDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.env=SimpleNamespace(identity='demo');self.pos=position()
        self.budget={'identity':identity(self.pos,'demo'),'opening_cost_basis':'verified_opening_fills',
            'tick':.01,'total_cost_distance':.2,'minimum_net_profit_distance':.2,
            'opening_receipt':{'status':'verified','identity':identity(self.pos,'demo'),'contracts':2,'opening_vwap':100}}
        self.quote={'instId':self.pos['instId'],'bidPx':'102','askPx':'102.01','ts':'1000000'}
        for target in ('append','best_effort'):
            p=patch.object(strategy_evidence,target);p.start();self.addCleanup(p.stop)
        p=patch.object(profit_exit_execution.time,'time',return_value=1000);p.start();self.addCleanup(p.stop)

    def attempt(self,request):
        return profit_exit_execution.attempt(self.env,self.pos,'trailing_exit',self.budget,request=request,sleep=Mock(),quote_reader=lambda:request('PUBLIC_QUOTE','/mock/public-market',{},self.env))
    def test_quote_crossing_cloud_floor_skips_passive_but_allows_market(self):
        self.budget['protected_stop']=102.001
        request=Mock(return_value=[self.quote]);r=self.attempt(request)
        self.assertEqual(r['status'],'market_allowed');self.assertEqual(r['reason'],'cloud_floor_crossed_market_only')
        self.assertFalse(any(c.args[0]=='POST' for c in request.call_args_list))

    def test_skip_diagnostics_distinguish_receipt_and_cost_shortfall(self):
        self.budget['opening_cost_basis']='estimate'
        self.assertEqual(self.attempt(Mock())['reason'],'opening_cost_not_verified')
        self.budget['opening_cost_basis']='verified_opening_fills'
        self.quote['bidPx']='100.1'
        self.assertEqual(self.attempt(Mock(return_value=[self.quote]))['reason'],'executable_net_profit_insufficient')

class ManagerIntegrationTests(unittest.TestCase):
    def run_manager(self,horizon,closed=True):
        import ai_factor_trader as trader
        from scripts.strategy_modes import mode_for
        inst='TEST-USDT-SWAP';mode=mode_for(horizon)
        if horizon=='scalp':mode['engine']='demo_scalp_v2'
        p=plan();p.update(horizon=horizon,setup='scalp_breakout_1m' if horizon=='scalp' else 'pullback_reclaim')
        p['target_layers']={'near':{'price':101.5 if horizon=='scalp' else 103.8}}
        p.update(trigger_level=100,entry_atr=.2)
        ctx=(scalp_management.context if horizon=='scalp' else swing.context)(p,{'instId':inst},scope='demo',decision_id='d',stop=99.)
        t={'entry_context':ctx,'positionIdentity':{'scope':'demo','instId':inst,'side':'long','cTime':'600000','posId':'position'},
            'decision_id':'d','horizon':horizon,'mode':mode,'setup':p['setup'],'entryTs':600,
            'trailingStopPx':99.,'exchangeStopPx':99.,'initialRiskStopPx':99.,'takeProfitPx':110.,
            'highWaterMark':101.8 if horizon=='scalp' else 105.4,'lowWaterMark':100.,'currentSz':1.}
        pos={'instId':inst,'posSide':'long','side':'long','posId':'position','cTime':'600000','pos':1.,'avgPx':100.,'upl':1.4}
        f={'instId':inst,'name':'TEST','market_data_valid':True,'price':101.4 if horizon=='scalp' else 104.,
            'atr':.2,'atr_15m':.2,'precision':2,'tickSz':'.01','ctVal':1}
        def enrich(f,*args):
            if horizon=='scalp':f.update(atr_1m=.2,closed_1m=candles())
            else:f.update(atr_1h=2.,closed_15m=candles(900000,(104.1,104.)))
        tracked={inst+'_long':t}
        with ExitStack() as stack:
            stack.enter_context(patch.object(trader.time,'time',return_value=725 if horizon=='scalp' else 2405))
            stack.enter_context(patch.object(trader.market,'_selected',return_value=SimpleNamespace(identity='demo')))
            stack.enter_context(patch.object(trader,'load_horizon_intents',return_value={}))
            stack.enter_context(patch('scripts.minute_exit.'+('enrich_volatility' if horizon=='scalp' else 'enrich_swing'),side_effect=enrich))
            cloud=stack.enter_context(patch.object(trader,'ensure_cloud_position_protection',return_value=(True,'verified')))
            close=stack.enter_context(patch.object(trader,'close_position_confirmed',return_value=(closed,'fixture')))
            for target in ('record_trade','notify_trade_close','add_stop_cooldown'):stack.enter_context(patch.object(trader,target))
            stack.enter_context(patch.object(trader.strategy_evidence,'best_effort'))
            changed,_=trader.manage_position_tp_and_trailing(f,pos,tracked,'fixture',[])
        return changed,tracked,t,cloud,close

    def test_minute_normal_exit_retains_original_cloud_protection_and_supplies_cost_budget(self):
        changed,tracked,t,cloud,close=self.run_manager('scalp')
        self.assertTrue(changed);self.assertFalse(tracked)
        self.assertEqual(cloud.call_args.args[-1],99.)
        self.assertEqual(close.call_args.kwargs['exit_reason'],'trailing_exit')
        self.assertIn('protected_stop',close.call_args.kwargs['profit_budget'])
        self.assertEqual(t['scalpManagement']['coordination']['reason'],'observed_near_target_exhaustion')

    def test_swing_normal_exit_uses_hourly_atr_and_same_cloud_coordination(self):
        changed,tracked,t,cloud,close=self.run_manager('swing')
        self.assertTrue(changed);self.assertFalse(tracked)
        self.assertEqual(t['exitVolatility']['source'],'atr_1h')
        self.assertEqual(cloud.call_args.args[-1],99.)
        self.assertEqual(close.call_args.kwargs['exit_reason'],'trailing_exit')

    def test_unconfirmed_profit_close_keeps_tracker_and_protection(self):
        changed,tracked,t,cloud,close=self.run_manager('scalp',closed=False)
        self.assertFalse(changed);self.assertTrue(tracked)
        self.assertEqual(cloud.call_args.args[-1],99.)
        self.assertEqual(close.call_count,1)

if __name__=='__main__':unittest.main()
