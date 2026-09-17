import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from scripts import scalp_management as sm,minute_exit,entry_candidates,demo_scalp
from scripts.risk_policy import Policy
from scripts.strategy_modes import mode_for
import ai_factor_trader as trader
from test_demo_scalp import minute_package

INST='TEST-USDT-SWAP';CREATED=600000

def context(setup='scalp_breakout_1m',side='long',alignment='range'):
    return {'version':sm.VERSION,'setup':setup,'scope':'demo','instrument':INST,'side':side,
            'decision_id':'decision','candidate_id':'candidate','entry_atr':.2,
            'initial_stop':99 if side=='long' else 101,'trigger_level':100.,'alignment':alignment}
def tracker(**kwargs):
    return {'entry_context':context(**kwargs),'positionIdentity':{'instId':INST,'posId':'position','cTime':str(CREATED),'scope':'demo','side':kwargs.get('side','long')},
            'highWaterMark':100.1,'lowWaterMark':99.9,'entryTs':600}
def bars(closes):
    return [{'close_ms':CREATED+(i+1)*60000,'close':v} for i,v in enumerate(closes)]
def evaluate(t,closes=(99.5,99.4),price=99.4,side='long',now=None):
    return sm.evaluate(t,{'closed_1m':bars(closes)},entry=100,current=price,side=side,now=now or 600+len(closes)*60+5,policy=Policy(),tick=.01)

class ScalpManagementTests(unittest.TestCase):
    def test_two_confirmed_closes_not_adverse_tick_authorize_failure(self):
        t=tracker()
        self.assertFalse(evaluate(t,closes=(100.1,100.2,100.1),price=99.4)['exit'])
        r=evaluate(t);self.assertTrue(r['exit']);self.assertEqual(r['state'],'FAILED')
        self.assertEqual(r['last_close_ms'],720000)
    def test_partial_entry_candle_is_not_a_full_confirmation(self):
        t=tracker();t['positionIdentity']['cTime']='630000'
        self.assertFalse(evaluate(t)['exit'])
    def test_pullback_has_its_own_longer_observation_window(self):
        t=tracker(setup='scalp_pullback_1m')
        self.assertFalse(evaluate(t)['exit'])
        self.assertTrue(evaluate(t,closes=(99.5,99.4,99.3),price=99.3)['exit'])
    def test_regime_changes_management_not_direction_permissions(self):
        self.assertTrue(evaluate(tracker(alignment='countertrend'))['exit'])
        self.assertFalse(evaluate(tracker(alignment='aligned'))['exit'])
        self.assertTrue(evaluate(tracker(alignment='aligned'),closes=(99.5,99.4,99.3),price=99.3)['exit'])
    def test_short_and_long_use_symmetric_failure_conditions(self):
        r=evaluate(tracker(side='short'),closes=(100.5,100.6),price=100.6,side='short')
        self.assertTrue(r['exit'])
    def test_recovery_before_execution_does_not_close_on_old_broken_bars(self):
        self.assertFalse(evaluate(tracker(),price=100.1)['exit'])
    def test_missing_stale_or_gapped_bars_do_not_authorize_failure(self):
        t=tracker()
        self.assertFalse(evaluate(t,closes=(),now=900)['exit'])
        self.assertFalse(evaluate(t,now=901)['exit'])
        broken=bars((99.5,99.4));broken[-1]['close_ms']+=60000
        r=sm.evaluate(t,{'closed_1m':broken},entry=100,current=99.4,side='long',now=785,policy=Policy(),tick=.01)
        self.assertFalse(r['exit'])
    def test_unknown_and_legacy_setups_are_not_forced_into_minute_failure_exit(self):
        for setup in ('unknown','range_reversion','pullback_reclaim'):
            self.assertFalse(evaluate(tracker(setup=setup))['enabled'])
    def test_profit_floor_covers_taker_costs_and_does_not_widen_after_restart(self):
        t=tracker();t['highWaterMark']=101.5
        r=evaluate(t,closes=(101.2,101.3),price=101.3)
        self.assertTrue(r['protection']['active']);self.assertFalse(r['exit'])
        self.assertGreaterEqual(r['protection']['stop']-100,r['cost_distance'])
        t=json.loads(json.dumps({**t,'scalpManagement':r}));t['highWaterMark']=100.2
        again=evaluate(t,closes=(101.,100.8),price=100.8)
        self.assertTrue(again['protection']['active'])
        self.assertGreaterEqual(again['protection']['stop'],r['protection']['stop'])
    def test_cost_floor_does_not_activate_below_fee_and_slippage_budget(self):
        t=tracker();t['highWaterMark']=100.15
        r=evaluate(t,closes=(100.1,100.15),price=100.15)
        self.assertFalse(r['protection']['active'])
    def test_short_floor_and_price_crossing_are_observed(self):
        t=tracker(side='short');t['lowWaterMark']=98.5
        r=evaluate(t,closes=(98.6,98.7),price=99.5,side='short')
        self.assertTrue(r['protection']['active']);self.assertTrue(r['protection']['crossed'])
    def test_prior_progress_is_not_reclassified_as_failed_initial_confirmation(self):
        t=tracker();t['highWaterMark']=100.75
        self.assertFalse(evaluate(t)['exit'])
    def test_context_adoption_binds_account_lifecycle_and_survives_json_restart(self):
        saved={'entry_context':context(),'decision_id':'decision','ts':600}
        p={'instId':INST,'posSide':'long','posId':'position','cTime':str(CREATED)}
        t={};self.assertTrue(sm.adopt(t,saved,p,'demo'))
        self.assertEqual(json.loads(json.dumps(t))['entry_context'],context())
        for changes,scope in (({},'live'),({'cTime':'1000000'},'demo'),({'instId':'OTHER'},'demo'),({'posSide':'short'},'demo')):
            self.assertFalse(sm.adopt({},saved,{**p,**changes},scope))
    def test_entry_context_is_saved_atomically_and_available_before_consumption(self):
        mode=mode_for('scalp');mode['engine']='demo_scalp_v2'
        with tempfile.TemporaryDirectory() as tmp,patch.object(trader,'HORIZON_INTENTS_FILE',str(Path(tmp)/'intents.json')):
            trader.save_horizon_intent(INST,'long','scalp','decision',mode,'scalp_breakout_1m',context())
            saved=trader.load_horizon_intents()[INST+'_long']
            self.assertEqual(saved['entry_context'],context());self.assertEqual(saved['setup'],'scalp_breakout_1m')
            self.assertEqual(trader.consume_horizon_intent(INST,'long'),'scalp')
            self.assertEqual(trader.load_horizon_intents(),{})

    def test_setup_cooldown_is_retired_but_hard_stop_is_not(self):
        data={INST+'_long':{'ts':1000,'cooldown_seconds':7200,'reason':'setup结构失效','count':3}}
        with patch.object(trader,'load_stop_cooldowns',return_value=data),patch.object(trader.time,'time',return_value=1100):
            self.assertFalse(trader.is_in_stop_cooldown(INST,'long'))
            data[INST+'_long']['reason']='硬止损'
            self.assertTrue(trader.is_in_stop_cooldown(INST,'long'))
    def test_candidates_are_unchanged_by_materializing_exit_metadata(self):
        for side in ('long','short'):
            p=minute_package(side);before=entry_candidates.catalog(p)
            for plan in before['plans']:
                row=demo_scalp.materialize(p,plan,p['data_as_of'],Policy())
                self.assertEqual(row['decision']['candidate_id'],plan['id'])
                self.assertEqual(row['decision']['setup'],plan['setup'])
            self.assertEqual(entry_candidates.catalog(p),before)

class RealManagerTests(unittest.TestCase):
    def test_guard_created_tracker_adopts_frozen_setup_and_failure_has_no_cooldown(self):
        mode=mode_for('scalp');mode['engine']='demo_scalp_v2'
        p={'instId':INST,'side':'long','posSide':'long','posId':'position','cTime':str(CREATED),'pos':1,'avgPx':100,'upl':-.6}
        t=tracker();t.pop('entry_context');t.update(horizon='scalp',mode=mode,trailingStopPx=99,exchangeStopPx=99,initialRiskStopPx=99,takeProfitPx=105,currentSz=1)
        # Guard has adopted the cloud stop, not yet the saved entry context.
        tracked={INST+'_long':t};f={'instId':INST,'name':'TEST','market_data_valid':True,'price':99.4,'atr':1,'atr_15m':1,'precision':2,'tickSz':'.01','ctVal':1}
        saved={INST+'_long':{'entry_context':context(),'mode':mode,'horizon':'scalp','setup':'scalp_breakout_1m','decision_id':'decision','ts':600}}
        def enrich(f,*args):f['closed_1m']=bars((99.5,99.4));f['atr_1m']=.2
        with patch.object(trader.time,'time',return_value=725),patch.object(trader.market,'_selected',return_value=SimpleNamespace(identity='demo')),patch.object(trader,'load_horizon_intents',return_value=saved),patch('scripts.minute_exit.enrich_volatility',side_effect=enrich),patch.object(trader,'ensure_cloud_position_protection',return_value=(True,'verified')),patch.object(trader,'close_position_confirmed',return_value=(True,'confirmed')) as close,patch.object(trader,'add_stop_cooldown') as cooldown,patch.object(trader.strategy_evidence,'best_effort'):
            changed,_=trader.manage_position_tp_and_trailing(f,p,tracked,'fixture',[])
        self.assertTrue(changed);self.assertNotIn(INST+'_long',tracked)
        self.assertEqual(close.call_args.kwargs['exit_reason'],'strategy_failure_exit');cooldown.assert_not_called()
