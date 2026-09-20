"""A02 causal signal regressions. Run only through scripts/run_tests.py in WSL."""
import copy
from datetime import datetime, timezone
import importlib
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

from scripts import calculus_engine as calculus
from scripts import entry_candidates as candidates
from scripts import entry_opportunities as opportunities
from scripts import entry_research
from scripts import scalp_ranking as ranking
from scripts import scalp_research as research
from scripts import scenario_entry as scenario
from scripts import signal_data, trade_quality
from scripts.risk_policy import Policy

# factor_library's standalone imports resolve from scripts, as in its scheduler.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
factors = importlib.import_module('factor_library')

BASE = 1_788_825_600_000 // 14_400_000 * 14_400_000


def fixture(minute=False, side='long'):
    p = {'instId': 'A02-USDT-SWAP', 'data_quality': 'valid', 'data_as_of': BASE/1000+.05,
         'price': 100., 'bidPx': 99.99, 'askPx': 100.01, 'macro_4h': '4H_MACRO_BULL',
         'atr_1h': 3., 'environment_support': {'can_open': True}, 'entry_candles': {}}
    for tf, width in candidates.WIDTHS.items():
        rows = []
        for i in range(24):
            o, h, l, c, v = 101., 102., 99., 100., 100.
            if tf == '1H': o, h, l, c = 103., 110., 98., 102+i*.1
            elif minute:
                c = 100. if tf == '1M' else 98+i*.04
                o, h, l = c, c+1, c-1
                if tf == '1M' and i == 23: o, h, l, c, v = 100, 102, 99.9, 101.5, 160
            elif tf == '15M':
                if i == 22: o, h, l, c = 101., 101.2, 99., 99.4
                if i == 23: o, h, l, c = 99.4, 100.2, 99., 100.
            if side == 'short': o, h, l, c = 200-o, 200-l, 200-h, 200-c
            rows.append([BASE-(24-i)*width, o, h, l, c, v, 0, 0, '1'])
        p['entry_candles'][tf] = candidates.seal_candles(rows, tf, BASE+50)
    if side == 'short': p['macro_4h'] = '4H_MACRO_BEAR'
    if minute:
        p['strategy_engine'] = 'demo_scalp_v2'
        p['price'] = 101.5 if side == 'long' else 98.5
        p.update(bidPx=p['price']-.01, askPx=p['price']+.01)
    return p


class CandleContractTests(unittest.TestCase):
    def raw(self, width=60000):
        return [[BASE-(24-i)*width, 100, 101, 99, 100, 10, 0, 0, '1'] for i in range(24)]

    def test_future_and_unconfirmed_candles_are_not_signal_observations(self):
        rows=self.raw(); current=[BASE,100,1000,1,999,100,0,0,'0']
        future=[BASE+60000,100,1000,1,999,100,0,0,'1']
        result=signal_data.closed_candles(rows+[current,future], '1m', as_of_ms=BASE+500)
        self.assertEqual(len(result),24)
        self.assertEqual(result[0][0]+60000,BASE)
        self.assertEqual(rows[-1][4],100)

    def test_confirmed_fractional_timestamps_cannot_be_truncated_into_closed_bar(self):
        for bad in (BASE-60000+.5, True, float('nan'), float('inf'), None):
            with self.subTest(bad=bad):
                rows=self.raw(); rows[-1][0]=bad
                with self.assertRaises(signal_data.SignalDataError):
                    signal_data.closed_candles(rows,'1m',as_of_ms=BASE)
                with self.assertRaises(ValueError): candidates.seal_candles(rows,'1M',BASE)

    def test_one_bar_publication_tolerance_is_not_tightened(self):
        rows=self.raw()[:-1]
        self.assertEqual(len(signal_data.closed_candles(rows,'1m',as_of_ms=BASE)),23)

    def test_zero_interval_and_boolean_prices_are_not_valid_numbers(self):
        with self.assertRaises(signal_data.SignalDataError): signal_data.bar_seconds('0m')
        rows=self.raw(); rows[-1][1:5]=[True,1,1,1]
        with self.assertRaises(signal_data.SignalDataError):
            signal_data.closed_candles(rows,'1m',as_of_ms=BASE)

    def test_all_sealed_timeframes_are_closed_contiguous_and_copied(self):
        for tf,width in candidates.WIDTHS.items():
            rows=self.raw(width); original=copy.deepcopy(rows)
            sealed=candidates.seal_candles(list(reversed(rows)),tf,BASE+50)
            self.assertEqual(sealed['rows'][-1]['close_ms'],BASE)
            self.assertEqual(rows,original)
            self.assertEqual(len(sealed['rows']),24)

    def test_nonfinite_provenance_clock_cannot_bypass_snapshot_match(self):
        for bad in (float('nan'),float('inf'),True,None):
            p=fixture();p['entry_candles']['15M']['as_of_ms']=bad
            with self.assertRaises(ValueError):candidates.verified_bars(p,'15M')
            self.assertFalse(candidates.catalog(p)['plans'])

    def test_scenario_close_timestamp_contract_rejects_future_and_malformed_rows(self):
        rows=[dict(ts_ms=BASE-(19-i)*300000,open=100,high=101,low=99,close=100,volume=1,confirm=True) for i in range(20)]
        scenario.validate_bars(rows,300000,BASE)
        bad=copy.deepcopy(rows);bad[-1]['ts_ms']+=300000
        with self.assertRaises(ValueError):scenario.validate_bars(bad,300000,BASE)
        bad=copy.deepcopy(rows);bad[-1]=None
        with self.assertRaisesRegex(ValueError,'invalid_candle_row'):scenario.validate_bars(bad,300000,BASE)


class CandidateAndRankTests(unittest.TestCase):
    def test_existing_long_short_minute_and_swing_setups_still_produce_plans(self):
        from scripts.execution_costs import from_policy
        for minute in (False,True):
            for side in ('long','short'):
                with self.subTest(minute=minute,side=side):
                    p=fixture(minute,side);before=copy.deepcopy(p);out=candidates.catalog(p)
                    self.assertTrue(out['plans'],out)
                    self.assertEqual(out,candidates.catalog(p));self.assertEqual(p,before)
                    for q in out['plans']:
                        sign=1 if side=='long' else -1
                        self.assertGreater(sign*(q['entry_price']-q['stop_loss_price']),0)
                        self.assertGreater(sign*(q['take_profit_price']-q['entry_price']),0)
                        self.assertFalse(q['order_authorized'])
                        policy=vars(Policy())
                        if minute:
                            from scripts.demo_scalp_policy import parameters
                            policy=parameters(policy)
                        cost=from_policy(q['entry_price'],max(q['stop_loss_price'],q['take_profit_price']),policy,
                                         p['askPx']-p['bidPx'] if minute else 0)['maker_taker_total']
                        rr=(abs(q['take_profit_price']-q['entry_price'])-cost)/(abs(q['entry_price']-q['stop_loss_price'])+cost)
                        self.assertAlmostEqual(q['net_rr'],rr)
                        self.assertEqual(q['target_observation']['extrapolated'],minute)

    def test_ids_stable_for_same_snapshot_but_not_transferable_to_new_quote_or_symbol(self):
        for minute in (False,True):
            p=fixture(minute);plan=candidates.catalog(p)['plans'][0]
            self.assertEqual(plan['id'],candidates.catalog(copy.deepcopy(p))['plans'][0]['id'])
            for field,value in (('instId','OTHER-USDT-SWAP'),('askPx',p['askPx']+.001)):
                newer=copy.deepcopy(p);newer[field]=value
                with self.assertRaises(ValueError):
                    candidates.expand_selection(newer,{'candidate_id':plan['id'],'action':plan['action']})
            candidates.validate_live_quote(p,plan['id'],plan['entry_price'])

    def test_scalp_id_uses_numeric_snapshot_time_not_python_number_representation(self):
        p=fixture(True)
        p['data_as_of']=BASE//1000
        for frame in p['entry_candles'].values():frame['as_of_ms']=BASE
        integer=candidates.catalog(p)['plans'][0]
        p['data_as_of']=float(p['data_as_of'])
        real=candidates.catalog(p)['plans'][0]
        self.assertEqual(integer['id'],real['id'])

    def test_terminal_opportunity_cause_survives_missing_observations(self):
        for state in opportunities.TERMINAL:
            old={'id':'old','state':state,'reason':'frozen_reason','created_at':1,'expires_at':1000}
            self.assertEqual(opportunities.advance([old],[],2),[old])
            self.assertEqual(opportunities.advance([old],[{**old,'state':'ready','reason':'new'}],2),[old])

    def test_pivot_evidence_records_when_its_right_hand_bar_confirmed(self):
        hours=[{'close_ms':(i+1)*3600000,'high':v,'low':90} for i,v in enumerate((101,110,102))]
        self.assertIsNone(opportunities.observed_target(hours,'long',100,7200000))
        target=opportunities.observed_target(hours,'long',100,10800000)
        self.assertEqual(target['close_ms'],7200000)
        self.assertEqual(target['confirmed_close_ms'],10800000)

    def test_bad_direction_geometry_falls_back_without_removing_candidates(self):
        p=fixture(True);q=candidates.catalog(p)['plans'][0]
        for changes in ({'take_profit_price':q['entry_price']-1}, {'stop_loss_price':q['entry_price']+1},
                        {'action':'WAIT'}, {'entry_atr':float('nan')}, {'entry_atr':True}):
            with self.subTest(changes=changes):
                bad={**q,**changes,'id':'bad'};pairs=[(p,q),(p,bad)]
                ordered,audit=ranking.rank(pairs,vars(Policy()))
                self.assertEqual(audit['status'],'reference_rank_fallback')
                self.assertEqual(audit['candidates_removed'],0)
                self.assertEqual(len(ordered),len(pairs))
                self.assertEqual(audit['new_order'],audit['legacy_order'])

    def test_valid_ranking_is_scale_invariant_and_not_a_win_probability(self):
        p=fixture(True);q=candidates.catalog(p)['plans'][0]
        reference=ranking.describe(p,q,vars(Policy()))
        self.assertEqual(reference['status'],'evaluated')
        for scale in (1e-14,1e-7,1e5):
            pp,qq=copy.deepcopy(p),copy.deepcopy(q)
            for field in ('bidPx','askPx','price'):pp[field]*=scale
            for frame in pp['entry_candles'].values():
                for row in frame['rows']:
                    for field in ('open','high','low','close'):row[field]*=scale
            for field in ('entry_price','stop_loss_price','take_profit_price','entry_atr'):qq[field]*=scale
            metric=ranking.describe(pp,qq,vars(Policy()))
            self.assertEqual(metric['status'],'evaluated')
            self.assertAlmostEqual(metric['score'],reference['score'],places=6)
        self.assertEqual(reference['semantics'],'heuristic_not_win_probability')

    def test_observed_targets_do_not_use_later_hour_or_unconfirmed_future_pivot(self):
        hours=[{'close_ms':(i+1)*3600000,'high':110+(i%3),'low':90-(i%3)} for i in range(14)]
        before=opportunities.observed_target(hours,'long',100,12*3600000)
        hours[-1]['high']=100000
        self.assertEqual(before,opportunities.observed_target(hours,'long',100,12*3600000))
        self.assertLessEqual(before['close_ms'],12*3600000)
        self.assertFalse(before['extrapolated'])


class CalculusRegressionTests(unittest.TestCase):
    def test_invalid_close_cannot_compress_time_or_realign_volume(self):
        for bad in (None,float('nan'),float('inf'),0,-1,True,'not-a-price'):
            prices=[100+i*.1 for i in range(16)];prices[5]=bad
            for fn in (calculus.calculate_calculus,calculus.calculate_definite_integrals):
                with self.subTest(bad=bad,function=fn.__name__):
                    out=fn(prices,vols=[100+i for i in range(16)])
                    self.assertFalse(out['valid'])

    def test_unaligned_or_nonfinite_volume_is_not_treated_as_valid_integral(self):
        prices=[100+i for i in range(16)]
        for volumes in ([100]*15,[100]*15+[float('nan')],[100]*15+[-1]):
            self.assertFalse(calculus.calculate_calculus(prices,vols=volumes)['valid'])
            self.assertFalse(calculus.calculate_definite_integrals(prices,vols=volumes)['valid'])
        self.assertTrue(calculus.calculate_calculus(prices,vols=[0]*16)['valid'])

    def test_raw_exchange_rows_cannot_be_misread_as_stripped_ohlcv(self):
        raw=[[BASE-i*60000,100,101,99,100,10,0,0,'1'] for i in range(16)]
        result=calculus.calculate_multi_timeframe({'1M':raw})
        self.assertFalse(result['valid'])
        self.assertEqual(result['timeframes']['1M']['reason'],'expected_stripped_ohlcv')

    def test_malformed_row_is_not_silently_skipped(self):
        rows=[[100,101,99,100+i*.1,1] for i in range(16)];rows[5]=[100]
        self.assertFalse(calculus.calculate_multi_timeframe({'15M':rows})['valid'])

    def test_weak_acceleration_never_flips_majority_direction_label(self):
        base={'valid':True,'quality':1.,'velocity':.5,'acceleration':0.,'impulse':.5,'jerk':0.,'direction':1,
              'definite_integrals':{},'probability_theory':{}}
        for direction in (1,-1):
            for acceleration in (-.03,0.,.03):
                feature={**base,'direction':direction,'impulse':direction*.5,'velocity':direction*.5,'acceleration':acceleration}
                with patch.object(calculus,'calculate_calculus',return_value=feature):
                    out=calculus.calculate_multi_timeframe({'15M':[],'1H':[],'4H':[]})
                self.assertTrue(out['regime'].startswith('BULL_' if direction==1 else 'BEAR_'),out)

    def test_probability_semantics_survive_aggregation_and_short_history(self):
        rows=[[100+i*.1,101+i*.1,99+i*.1,100+i*.1,10] for i in range(24)]
        out=calculus.calculate_multi_timeframe({'15M':list(reversed(rows))})
        for p in (out['probability_theory'],calculus.calculate_probability_theory([.001])):
            self.assertFalse(p['probability_calibrated'])
            self.assertIn('not_empirical_win_rate',p['probability_semantics'])
        self.assertFalse(calculus.calculate_probability_theory([.001]*5+[float('nan')])['valid'])

    def test_valid_calculus_is_price_scale_invariant(self):
        prices=[100+i*.1+i*i*.01 for i in range(24)]
        a=calculus.calculate_calculus(prices)
        b=calculus.calculate_calculus([p*1e-7 for p in prices])
        for field in ('velocity','acceleration','impulse','jerk'):
            self.assertAlmostEqual(a[field],b[field],places=4)
        self.assertEqual(a['direction'],b['direction'])


class FactorPrecisionTests(unittest.TestCase):
    def compute(self,prices):
        raw=[[i*900000,p,p*1.01,p*.99,p,100,0,0,'1'] for i,p in enumerate(prices)]
        def market_reply(url,*args,**kwargs):
            if '/ticker?' in url:return {'code':'0','data':[{'last':prices[-1],'bidPx':prices[-1],'askPx':prices[-1],'open24h':prices[0]}]}
            return {'code':'0','data':[]}
        with patch.object(factors.market,'get_json',side_effect=market_reply), \
             patch.object(factors.market,'signal_json',return_value={'code':'0','data':list(reversed(raw))}), \
             patch.object(factors.market,'signal_indicators',return_value={}):
            return factors.compute_instrument_factors({'instId':'A02-USDT-SWAP','name':'A02'}, {})

    def test_flat_rising_and_falling_rsi_boundary_values(self):
        for prices,expected in (([100.]*24,50.),([100.+i for i in range(24)],100.),([124.-i for i in range(24)],0.)):
            self.assertEqual(self.compute(prices)['trend_momentum']['rsi_14'],expected)

    def test_low_price_atr_keeps_price_units_instead_of_rounding_to_zero(self):
        high=self.compute([100.]*24);low=self.compute([1e-6]*24)
        for key in ('atr_14','atr_1h'):
            self.assertGreater(low['volatility_channel'][key],0)
            self.assertAlmostEqual(high['volatility_channel'][key]*1e-8,low['volatility_channel'][key],places=16)

    def test_factor_probability_scores_are_explicitly_uncalibrated(self):
        p=self.compute([100.]*24)['probability_theory']
        self.assertFalse(p['probability_calibrated'])
        self.assertIn('not_empirical_win_rate',p['probability_semantics'])


class ResearchCausalityTests(unittest.TestCase):
    def case(self,at=600.):
        return research.make_case('demo',{'instId':'A02'},{'id':'id','setup':'scalp_breakout_1m','action':'BUY_LONG',
            'entry_price':100,'stop_loss_price':99,'take_profit_price':102},{},at)

    def test_submillisecond_after_boundary_excludes_partial_minute(self):
        for at in (600.00001,600.0001,600.0009,600.5):
            case=self.case(at)
            self.assertEqual(case['observation_start_ms'],660000)
            out=research.advance(case,[{'close_ms':660000,'high':200,'low':1}],660000)
            self.assertEqual(out['observed_bars'],0)
        self.assertEqual(self.case(600.)['observation_start_ms'],600000)

    def test_unsorted_bars_cannot_reverse_first_touch_or_drop_history(self):
        c=self.case();bars=[{'close_ms':c['observation_start_ms']+(i+1)*60000,'high':101,'low':99.5} for i in range(60)]
        bars[0]['low']=98.;bars[1]['high']=103.
        expected=research.advance(c,bars,c['end_ms'])
        actual=research.advance(c,list(reversed(bars)),c['end_ms'])
        self.assertEqual(actual,expected)
        self.assertEqual(actual['first_touch'],'stop_first')
        self.assertEqual(actual['status'],'complete')
        self.assertEqual(c['observed_bars'],0)

    def test_future_bar_does_not_affect_current_research_excursions(self):
        c=self.case();first={'close_ms':660000,'high':101,'low':99.5};future={'close_ms':720000,'high':200,'low':1}
        a=research.advance(c,[first],660000);b=research.advance(c,[future,first],660000)
        self.assertEqual(a,b)
        self.assertIsNone(a['first_touch'])

    def test_entry_probe_remains_causal_under_changed_future_prices(self):
        rows=[]
        for i in range(1200):
            p=100+i*.02;at=(i+1)*300000
            rows.append(dict(symbol='A02',timestamp=datetime.fromtimestamp(at/1000,timezone.utc).isoformat(),
                             ts_ms=at,open=p-.01,high=p+.01,low=p-.02,close=p,volume=100,confirm=True))
        future=copy.deepcopy(rows)
        for row in future[1100:]:
            for key in ('open','high','low','close'):row[key]*=2
        for variant in entry_research.SPEC['variants']:
            a=entry_research.generate(rows,{'tickSz':'.01'},variant)
            b=entry_research.generate(future,{'tickSz':'.01'},variant)
            cutoff=rows[1099]['ts_ms']
            self.assertEqual([s for s in a['signals'] if s['features_as_of_ms']<=cutoff],
                             [s for s in b['signals'] if s['features_as_of_ms']<=cutoff])
            self.assertTrue(all(s['generated_at_ms']>s['setup_created_at_ms'] for s in a['signals']))


class TradeEvidenceTests(unittest.TestCase):
    def test_latest_observation_is_selected_independent_of_input_order(self):
        history={'instId':'A02','posId':'p','cTime':'100000','uTime':'1000000'}
        key=('A02','p','100000')
        samples=[{'at':101.,'position':{'markPx':100,'upl':-1}},
                 {'at':999.,'position':{'markPx':102,'upl':2}},
                 {'at':1001.,'position':{'markPx':999,'upl':999}}]
        row=trade_quality.annotate({},history,{key:samples},[])
        self.assertEqual(row['exit_market_snapshot']['observed_at'],999.)
        self.assertEqual(row['exit_market_snapshot']['markPx'],102)
        self.assertEqual(row['holding_observations']['samples'],2)
        self.assertEqual(row['holding_observations']['sampled_max_unrealized_pnl'],2)
        self.assertEqual(row['holding_observations']['basis'],'periodic_snapshots_not_tick_extremes')

    def test_observation_index_closes_read_only_connection(self):
        db=MagicMock();db.execute.return_value.fetchall.return_value=[]
        with patch.object(trade_quality.sqlite3,'connect',return_value=db):
            self.assertEqual(trade_quality.observation_index('demo'),{})
        db.close.assert_called_once()
