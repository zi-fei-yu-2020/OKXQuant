import copy
import json
import unittest
from scripts import entry_candidates as plans, trading_prompt as contract


def package(side='long'):
    at=1_788_825_600_000  # exact 4H boundary; synthetic fixture, no market outcome claim
    at=at//14_400_000*14_400_000
    raw=[]
    for i in range(24):
        # Earlier data provides a channel; the final candle reclaims the prior close.
        o,h,l,c=101,102,99,100
        if i==22:o,h,l,c=101,101.2,99,99.4
        if i==23:o,h,l,c=99.4,100.2,99,100
        raw.append([at-(24-i)*900_000,o,h,l,c,100,0,0,'1'])
    hours=[[at-(24-i)*3_600_000,103,110,98,102+i*.1,100,0,0,'1'] for i in range(24)]
    p={'instId':'TEST-USDT-SWAP','name':'TEST','data_quality':'valid','data_as_of':at/1000+.05,
       'price':100,'bidPx':99.99,'askPx':100.01,'macro_4h':'4H_MACRO_BULL','adx_1h':25,'atr_1h':3,
       'environment_support':{'can_open':True},'entry_candles':{}}
    if side=='short':
        for series in (raw,hours):
            for r in series:r[1:5]=[200-r[1],200-r[3],200-r[2],200-r[4]]
        p['macro_4h']='4H_MACRO_BEAR'
    p['entry_candles']={tf:plans.seal_candles(series,tf,int(p['data_as_of']*1000)) for tf,series in [('15M',raw),('1H',hours)]}
    return p


def selection(plan):
    return {'action':plan['action'],'candidate_id':plan['id'],'summary_reason':'The closed candle reclaimed a defined structure',
            'counter_evidence_status':'none_observed','counter_evidence':[],
            'uncertainty':'Residual reversal and execution risk remain; no probability guarantee'}


class ProgramPlanTests(unittest.TestCase):
    def test_both_directions_generate_materializable_plans_without_score(self):
        for side in ('long','short'):
            p=package(side);before=copy.deepcopy(p);result=plans.catalog(p)
            self.assertTrue(result['plans'],result)
            plan=result['plans'][0]
            d=contract.candidate(p,selection(plan),contract.facts_for(p))
            self.assertTrue(d['contract_valid'],d)
            self.assertEqual(d['action'],plan['action']);self.assertEqual(d['confidence'],0)
            self.assertEqual(d['stop_loss_price'],plan['stop_loss_price'])
            self.assertEqual(p,before);self.assertFalse(plan['order_authorized'])
            self.assertGreaterEqual(plan['net_rr'],2)
            self.assertEqual(plans.catalog(p),result)

    def test_id_cannot_cross_symbol_snapshot_or_override_geometry(self):
        p=package();d=selection(plans.catalog(p)['plans'][0])
        for mutate in ('symbol','quote','geometry','direction'):
            other=copy.deepcopy(p);raw=copy.deepcopy(d)
            if mutate=='symbol':other['instId']='OTHER-USDT-SWAP'
            if mutate=='quote':other['askPx']+=.001
            if mutate=='geometry':raw['stop_loss_price']=10
            if mutate=='direction':raw['action']='SELL_SHORT'
            checked=contract.candidate(other,raw,contract.facts_for(other))
            self.assertFalse(checked['contract_valid'],mutate)

    def test_missing_stale_unconfirmed_or_gapped_candles_never_make_plans(self):
        for mutate in ('missing','stale','gap','bad_ohlc','future','unsupported'):
            p=package()
            if mutate=='missing':p.pop('entry_candles')
            elif mutate=='stale':p['data_as_of']+=900
            elif mutate=='gap':p['entry_candles']['15M']['rows'].pop(-3)
            elif mutate=='bad_ohlc':p['entry_candles']['15M']['rows'][-1]['high']=1
            elif mutate=='future':p['entry_candles']['15M']['rows'][-1]['close_ms']+=900_000
            elif mutate=='unsupported':p['environment_support']['can_open']=False
            self.assertEqual(plans.catalog(p)['plans'],[],mutate)
        with self.assertRaises(ValueError):plans.seal_candles([[0,1,2,.5,1,1,0,0,'0']]*24,'15M',1_000_000)

    def test_no_trigger_or_insufficient_rr_cannot_be_rescued_by_target_padding(self):
        p=package();p['entry_candles']['15M']['rows'][-1].update(open=100,close=99.5)
        p.update(price=99.5,bidPx=99.49,askPx=99.51)
        self.assertEqual(plans.catalog(p)['plans'],[])
        p=package()
        for i,r in enumerate(p['entry_candles']['1H']['rows']):r.update(open=100,close=99.95+i*.01,high=100.2)
        result=plans.catalog(p)
        self.assertEqual(result['plans'],[])
        rejection=next(c for c in result['checks'] if c['reason']=='net_rr_below_policy')
        self.assertEqual(rejection['geometry']['take_profit_price'],100.2)
        self.assertLess(rejection['net_rr'],2)

    def test_both_sides_targets_are_observed_not_extended_and_ids_are_versioned(self):
        for side in ('long','short'):
            p=package(side); plan=plans.catalog(p)['plans'][0]
            bars=plans.verified_bars(p,'1H')[-13:-1]
            observed=max(b['high'] for b in bars) if side=='long' else min(b['low'] for b in bars)
            self.assertEqual(plan['take_profit_price'],observed)
            self.assertEqual(plan['target_observation']['price'],observed)
            self.assertFalse(plan['target_observation']['extrapolated'])
            self.assertEqual(plan['version'],'closed-candle-plans-v4')
            # Stop basis is setup-aware: pullback_reclaim anchors to the retest structure
            # (no 1.5xATR floor), closed_range_breakout keeps the 1.5xATR volatility floor.
            self.assertIn(plan['stop_basis'],('retest_structure_plus_volatility_buffer','max_structural_3bar_extreme_and_1.5x_atr'))
            if side=='short':
                for i,r in enumerate(p['entry_candles']['1H']['rows']):r.update(open=100,close=100.05-i*.01,low=99.8)
                result=plans.catalog(p)
                self.assertEqual(result['plans'],[])
                self.assertTrue(any(c['reason']=='net_rr_below_policy' for c in result['checks']))

    def test_wait_requires_specific_review_but_is_never_forced_into_a_trade(self):
        from test_wait_audit import valid_wait
        p=package();raw=valid_wait();facts=contract.facts_for(p)
        self.assertFalse(contract.candidate(p,raw,facts)['contract_valid'])
        raw['candidate_reviews']=[{'candidate_id':plan['id'],'reason':'Current counter evidence needs consideration',
            'evidence':[{'ref':'/price','value':100,'interpretation':'Observed price does not prove success'}]} for plan in plans.catalog(p)['plans']]
        checked=contract.candidate(p,raw,facts)
        self.assertTrue(checked['contract_valid'],checked)
        self.assertEqual(checked['action'],'WAIT')
        raw['candidate_reviews'][0]['candidate_id']='invented'
        self.assertFalse(contract.candidate(p,raw,facts)['contract_valid'])

    def test_quote_can_retrace_but_cannot_lose_the_trigger_or_chase_farther(self):
        p=package();p.update(price=99.8,bidPx=99.79,askPx=99.81)
        self.assertTrue(plans.catalog(p)['plans'])
        p.update(price=99.2,bidPx=99.19,askPx=99.21)
        self.assertEqual(plans.catalog(p)['plans'],[])
        p.update(price=105,bidPx=104.99,askPx=105.01)
        self.assertEqual(plans.catalog(p)['plans'],[])

    def test_final_quote_must_still_hold_the_frozen_trigger_after_inference(self):
        for side in ('long','short'):
            p=package(side);plan=plans.catalog(p)['plans'][0]
            self.assertEqual(plans.validate_live_quote(p,plan['id'],100),plan)
            lost=99 if side=='long' else 101
            chased=105 if side=='long' else 95
            for quote in (lost,chased):
                with self.assertRaises(ValueError):plans.validate_live_quote(p,plan['id'],quote)

    def test_prompt_contains_exact_catalog_and_short_selection_schema(self):
        p=package();bundle=contract.compose({'id':'test'},{},[p])
        payload,_=json.JSONDecoder().raw_decode(bundle.user)
        self.assertEqual(payload['entry_candidates'][p['instId']],plans.catalog(p))
        branches=contract.output_schema()['properties']['decisions']['additionalProperties']['oneOf']
        self.assertTrue(any('candidate_id' in b['required'] for b in branches))
        self.assertIn('/entry_candles/15M/last/close',payload['facts'][p['instId']])
    def test_exhausted_trend_tail_does_not_create_short_plan(self):
        p=package('short')
        p.update(rsi_1h=22.0, rsi_15m=27.0, vwap_bias=-0.8)
        result=plans.catalog(p)
        self.assertFalse(result['plans'])
        self.assertTrue(any(x['reason']=='trend_tail_overextended' for x in result['checks']))

    def test_retest_stop_has_volatility_buffer(self):
        p=package('short')
        p.update(rsi_1h=42.0, rsi_15m=42.0, vwap_bias=-0.1, atr_1h=3.0)
        result=plans.catalog(p)
        self.assertTrue(result['plans'], result)
        plan=result['plans'][0]
        self.assertEqual(plan['stop_basis'],'retest_structure_plus_volatility_buffer')
        self.assertGreater(abs(plan['stop_loss_price']-plan['entry_price']), 0.25*3.0)

    def test_range_boundary_reversion_creates_observable_candidate(self):
        p=package('long')
        # Convert the higher timeframe into a range and place the final closed
        # candle at the lower boundary with a confirmed reclaim.
        for i,r in enumerate(p['entry_candles']['1H']['rows']):
            r.update(open=100, high=101, low=99, close=100)
        rows=p['entry_candles']['15M']['rows']
        for r in rows: r.update(open=99.5, high=99.8, low=99.2, close=99.5)
        rows[-2].update(open=98.9, high=99.2, low=98.5, close=98.9)
        rows[-1].update(open=98.9, high=99.6, low=98.8, close=99.5)
        p.update(price=99.5,bidPx=99.49,askPx=99.51,macro_4h='4H_MACRO_RANGE',rsi_1h=45,rsi_15m=48,vwap_bias=-0.1,atr_1h=1.0)
        result=plans.catalog(p, {**vars(__import__('scripts.risk_policy', fromlist=['Policy']).Policy()), 'minimum_net_rr': 0.8})
        self.assertTrue(any(x['setup']=='range_reversion' for x in result['plans']), result)


if __name__=='__main__':unittest.main()
