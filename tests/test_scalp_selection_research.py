import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts import execution_costs as costs,scalp_ranking as ranking,scalp_research as research,entry_candidates
from scripts.risk_policy import Policy
from test_demo_scalp import minute_package

class CostModelTests(unittest.TestCase):
    def test_admission_reference_matches_legacy_arithmetic_exactly(self):
        policy=vars(Policy())
        for entry in (.0817,.7417,101.59,2449.35,70000.1):
            for ratio in (.97,1,1.03):
                for spread in (0,.0001,.01):
                    exit_price=entry*ratio
                    old=entry*policy['maker_fee']+exit_price*policy['taker_fee']+entry*policy['slippage']+spread
                    result=costs.from_policy(entry,exit_price,policy,spread)
                    self.assertEqual(result['maker_taker_total'],old)
                    self.assertGreaterEqual(result['taker_taker_total']+1e-12,old)
    def test_equal_price_exit_and_size_budget_arithmetic_is_preserved(self):
        p=Policy()
        for entry in (.08,.7417,101.59,2449.35,70000):
            self.assertEqual(costs.reference_at_entry(entry,p),entry*(p.maker_fee+p.taker_fee+p.slippage))
            self.assertEqual(costs.from_policy(entry,entry,p)['taker_taker_total'],entry*(2*p.taker_fee+p.slippage))
    def test_nonfinite_negative_and_boolean_cost_inputs_are_rejected(self):
        for bad in (True,float('nan'),float('inf'),-1):
            with self.assertRaises(ValueError):costs.from_policy(bad,1,Policy())
        with self.assertRaises(ValueError):costs.from_policy(1,1,Policy(),-.1)
    def test_actual_fee_comparison_does_not_apply_leverage_or_alter_receipts(self):
        snapshot={**costs.from_policy(.7417,.74781,Policy()),'contract_base_units':1,'planned_contracts':5444}
        history={'openAvgPx':'.7417','closeAvgPx':str(4016.95312/5444),'closeTotalPos':'5444'}
        before=copy.deepcopy((snapshot,history))
        result=costs.compare_actual(snapshot,history,-2.87544969)
        self.assertTrue(result['within_fee_scenarios']);self.assertFalse(result['accounting_changed'])
        self.assertAlmostEqual(result['official_fee_cost'],2.87544969)
        self.assertAlmostEqual(result['actual_minus_reference_fee'],.05941017)
        self.assertEqual((snapshot,history),before)
    def test_missing_historical_cost_snapshot_stays_unknown(self):
        self.assertEqual(costs.compare_actual(None,{},-1)['status'],'unavailable')
    def test_generated_candidate_prices_counts_and_ids_match_reference_formula(self):
        def reference(entry,exit_price,policy,spread=0):
            return {'maker_taker_total':entry*policy['maker_fee']+exit_price*policy['taker_fee']+entry*policy['slippage']+spread}
        from test_entry_candidates import package
        for factory in (minute_package,package):
            for side in ('long','short'):
                p=factory(side);actual=entry_candidates.catalog(p,vars(Policy()))
                with patch.object(costs,'from_policy',side_effect=reference):baseline=entry_candidates.catalog(p,vars(Policy()))
                self.assertEqual(actual,baseline)

class RankingTests(unittest.TestCase):
    def candidates(self):
        p=minute_package();base=entry_candidates.catalog(p)['plans'][0]
        near={**base,'id':'near','entry_price':101.5,'stop_loss_price':100.5,'take_profit_price':105.,'net_rr':2.5}
        far={**base,'id':'far','entry_price':101.5,'stop_loss_price':100.5,'take_profit_price':130.,'net_rr':20.}
        return [(p,far),(p,near)]
    def test_rank_can_prefer_reachable_distance_over_largest_projected_rr(self):
        pairs=self.candidates();before=copy.deepcopy(pairs)
        selected,audit=ranking.rank(pairs,vars(Policy()))
        self.assertEqual(audit['legacy_order'][0],'far');self.assertEqual(audit['new_order'][0],'near')
        self.assertEqual(len(selected),len(pairs));self.assertEqual(audit['candidates_removed'],0)
        self.assertEqual(set(audit['new_order']),set(audit['legacy_order']));self.assertIsNone(audit['minimum_score'])
        self.assertEqual(pairs,before)
    def test_range_regime_prioritizes_reversion_without_removing_breakouts(self):
        p=minute_package();p.update(macro_4h='4H_MACRO_RANGE',structure_1h='1H_SWING_CHOP')
        base=entry_candidates.catalog(p)['plans'][0]
        breakout={**base,'id':'breakout','setup':'scalp_breakout_1m','net_rr':2.2}
        reversion={**base,'id':'reversion','setup':'range_reversion','net_rr':2.2}
        selected,audit=ranking.rank([(p,breakout),(p,reversion)],vars(Policy()))
        self.assertEqual(audit['new_order'][0],'reversion')
        self.assertEqual(len(selected),2)
        self.assertEqual(audit['candidates_removed'],0)
        self.assertEqual(audit['metrics']['reversion']['market_regime'],'range_chop')

    def test_trend_regime_prioritizes_breakout_without_removing_pullbacks(self):
        p=minute_package();p.update(macro_4h='4H_MACRO_BULL',structure_1h='1H_SWING_BULL')
        base=entry_candidates.catalog(p)['plans'][0]
        breakout={**base,'id':'breakout','setup':'scalp_breakout_1m','net_rr':2.2}
        pullback={**base,'id':'pullback','setup':'scalp_pullback_1m','net_rr':2.2}
        selected,audit=ranking.rank([(p,pullback),(p,breakout)],vars(Policy()))
        self.assertEqual(audit['new_order'][0],'breakout')
        self.assertEqual(len(selected),2)
        self.assertEqual(audit['candidates_removed'],0)
        self.assertEqual(audit['metrics']['breakout']['market_regime'],'trend')

    def test_scores_are_bounded_and_not_labeled_win_probabilities(self):
        _,audit=ranking.rank(self.candidates(),vars(Policy()))
        for d in audit['metrics'].values():
            self.assertTrue(0<=d['score']<=100);self.assertFalse(d['order_authorized']);self.assertFalse(d['target_observed'])
            self.assertEqual(d['semantics'],'heuristic_not_win_probability')
    def test_optional_evidence_failure_falls_back_without_removing_candidates(self):
        pairs=self.candidates();pairs[0][0].pop('entry_candles')
        selected,audit=ranking.rank(pairs,vars(Policy()))
        self.assertEqual(len(selected),2);self.assertEqual(audit['status'],'reference_rank_fallback')
        self.assertEqual(audit['new_order'],audit['legacy_order'])
    def test_empty_pool_does_not_create_a_trade(self):
        selected,audit=ranking.rank([],vars(Policy()));self.assertEqual(selected,[]);self.assertEqual(audit['candidate_count'],0)

class ShadowOutcomeTests(unittest.TestCase):
    def case(self):
        return research.make_case('demo',{'instId':'TEST'},{'id':'p','setup':'scalp_breakout_1m','action':'BUY_LONG','entry_price':100,'stop_loss_price':99,'take_profit_price':102},{},600.5)
    def bars(self,start,count=60):
        return [{'close_ms':start+(i+1)*60000,'high':101.,'low':99.5} for i in range(count)]
    def test_entry_partial_minute_is_excluded(self):
        c=self.case();self.assertEqual(c['observation_start_ms'],660000)
        out=research.advance(c,[{'close_ms':660000,'high':200,'low':1}],660001)
        self.assertEqual(out['observed_bars'],0);self.assertIsNone(out['first_touch']);self.assertFalse(c['fill_assumed'])
    def test_same_bar_stop_and_target_are_ambiguous_not_a_win(self):
        c=self.case();bs=self.bars(c['observation_start_ms']);bs[0].update(high=103,low=98)
        out=research.advance(c,bs,c['end_ms'])
        self.assertEqual(out['status'],'complete');self.assertEqual(out['first_touch'],'ambiguous_same_bar')
        self.assertTrue(out['horizons']['15']['target_observed']);self.assertTrue(out['horizons']['15']['stop_observed'])
        self.assertNotIn('pnl',out)
    def test_stop_before_later_target_is_not_counted_as_target_first(self):
        c=self.case();bs=self.bars(c['observation_start_ms']);bs[0]['low']=98.9;bs[5]['high']=102.1
        out=research.advance(c,bs,c['end_ms'])
        self.assertEqual(out['first_touch'],'stop_first');self.assertLess(out['stop_touch_ms'],out['target_touch_ms'])
    def test_target_reachability_is_separate_by_observation_horizon(self):
        c=self.case();bs=self.bars(c['observation_start_ms']);bs[19]['high']=102.2
        out=research.advance(c,bs,c['end_ms'])
        self.assertFalse(out['horizons']['15']['target_observed']);self.assertTrue(out['horizons']['30']['target_observed'])
        self.assertTrue(out['horizons']['60']['target_observed'])
    def test_gaps_cannot_create_complete_first_touch_or_future_mfe_in_earlier_window(self):
        c=self.case();bs=self.bars(c['observation_start_ms'])[19:];bs[0]['high']=103
        out=research.advance(c,bs,c['end_ms'])
        self.assertEqual(out['status'],'incomplete');self.assertEqual(out['first_touch'],'unknown_data_gap')
        self.assertIsNone(out['horizons']['15']['mfe_observed_price']);self.assertIsNone(out['horizons']['15']['target_observed'])
    def test_unknown_data_expires_without_inventing_a_trade(self):
        c=self.case();out=research.advance(c,[],c['end_ms']+120000)
        self.assertEqual(out['status'],'incomplete');self.assertEqual(out['observed_bars'],0)
    def test_progress_is_idempotent_and_survives_restart(self):
        c=self.case();bs=self.bars(c['observation_start_ms']);first=research.advance(c,bs[:20],bs[19]['close_ms'])
        self.assertEqual(research.advance(first,bs[:20],bs[19]['close_ms']),first)
        out=research.advance(json.loads(json.dumps(first)),bs,c['end_ms'])
        self.assertEqual(out['observed_bars'],60);self.assertEqual(out['status'],'complete')
    def test_short_direction_uses_low_for_target_and_high_for_stop(self):
        c=self.case();c.update(action='SELL_SHORT',stop=101,target=98)
        bs=self.bars(c['observation_start_ms']);bs[0].update(high=100.5,low=97.9)
        out=research.advance(c,bs[:1],bs[0]['close_ms']);self.assertEqual(out['first_touch'],'target_first')

class ShadowStorageTests(unittest.TestCase):
    def test_scope_restart_and_repeated_frame_never_duplicate_cases(self):
        p=minute_package();plans=entry_candidates.catalog(p)['plans'];pairs=[(p,q) for q in plans]
        selected,audit=ranking.rank(pairs,vars(Policy()));now=p['data_as_of']+8
        result={'at':now,'ranking_observed_at':now,'ranking':audit,'status':'risk_blocked','selected':None}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'r.db'
            self.assertEqual(research.observe('demo', [p],selected,result,path=path,now=now)['status'],'recorded')
            self.assertEqual(research.observe('demo', [p],selected,result,path=path,now=now)['status'],'already_observed')
            status=research.public_status('demo',path=path)
            self.assertEqual(status['frames'],1);self.assertEqual(status['cases']['tracking'],len(plans))
            self.assertEqual(status['selection_comparison']['candidate_pool_only']['legacy']['tracking'],1)
            self.assertEqual(status['selection_comparison']['candidate_pool_only']['new']['tracking'],1)
            self.assertEqual(status['latest']['execution_status'],'risk_blocked');self.assertFalse(status['latest']['counterfactual_order_sent'])
            self.assertEqual(research.public_status('other',path=path)['frames'],0)
            research.observe('other',[p],selected,result,path=path,now=now)
            self.assertEqual(research.public_status('other',path=path)['frames'],1)
            self.assertEqual(research.public_status('demo',path=path)['frames'],1)
    def test_summary_of_missing_store_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'absent.db';self.assertEqual(research.public_status('demo',path=path)['status'],'not_started');self.assertFalse(path.exists())
    def test_shadow_module_has_no_trading_network_or_llm_dependencies(self):
        text=Path(research.__file__).read_text(encoding='utf8')
        for forbidden in ('import requests','import urllib','import ai_factor_trader','import entry_gateway','import okx_trade_service','subprocess','llm_transport'):
            self.assertNotIn(forbidden,text)

class ResearchApiTests(unittest.TestCase):
    def test_research_summary_requires_authentication_before_reading(self):
        from fastapi import HTTPException
        import okxquant_backend.app as api
        with patch.object(api,'require_admin_header',side_effect=HTTPException(401,'auth')),patch.object(research,'public_status') as read:
            with self.assertRaises(HTTPException):api.cache('scalp-research')
        read.assert_not_called()
    def test_research_summary_uses_current_account_scope(self):
        import okxquant_backend.app as api
        with patch.object(api,'require_admin_header'),patch('scripts.okx_runtime.selected_environment',return_value=SimpleNamespace(identity='demo-scoped')),patch.object(research,'public_status',return_value={'order_authorized':False}) as read:
            response=api.cache('scalp-research')
        read.assert_called_once_with('demo-scoped');self.assertFalse(json.loads(response.body)['order_authorized'])
