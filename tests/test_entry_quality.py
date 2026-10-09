import unittest
from copy import deepcopy
from unittest.mock import patch
from scripts import entry_quality as quality
from scripts.risk_policy import Policy

class EntryQualityTests(unittest.TestCase):
    def setUp(self):
        self.policy=vars(Policy())
        self.package={'bidPx':99.99,'askPx':100.,'macro_4h':'4H_MACRO_RANGE','rsi_15m':50,'adx_1h':30}
        self.plan={'action':'BUY_LONG','entry_price':100.,'stop_loss_price':98.,'take_profit_price':130.,
                   'setup':'pullback_reclaim','target_layers':{'near':{'price':110.,'extrapolated':False}}}
    def test_observed_room_admitted_without_geometry_mutation(self):
        before=deepcopy(self.plan);result=quality.evaluate(self.package,self.plan,self.policy)
        self.assertEqual(result['status'],'admitted');self.assertGreater(result['first_target_net_rr'],2)
        self.assertEqual(self.plan,before);self.assertFalse(result['order_authorized'])
    def test_far_target_cannot_mask_unfunded_near_structure(self):
        self.plan['target_layers']['near']['price']=101.
        result=quality.evaluate(self.package,self.plan,self.policy)
        self.assertIn('near_structure_net_rr_insufficient',result['reasons'])
    def test_costs_are_included_and_not_maker_only(self):
        result=quality.evaluate(self.package,self.plan,self.policy)
        self.assertEqual(result['cost_basis'],'taker_taker_with_slippage')
        self.assertGreater(result['round_trip_cost'],100*self.policy['maker_fee'])
    def test_live_quote_must_recheck_first_target_room(self):
        self.plan['target_layers']['near']['price']=105.
        self.assertEqual(quality.evaluate(self.package,self.plan,self.policy)['status'],'admitted')
        self.assertEqual(quality.evaluate(self.package,self.plan,self.policy,entry_price=103.)['status'],'shadow_only')
    def test_short_geometry_is_symmetric(self):
        self.plan.update(action='SELL_SHORT',stop_loss_price=102.,take_profit_price=70.)
        self.plan['target_layers']['near']['price']=90.
        self.assertEqual(quality.evaluate(self.package,self.plan,self.policy)['status'],'admitted')
        self.plan['target_layers']['near']['price']=99.
        self.assertEqual(quality.evaluate(self.package,self.plan,self.policy)['status'],'shadow_only')
    def test_projected_countertrend_momentum_without_observed_target_is_shadow(self):
        self.plan.update(setup='scalp_breakout_1m',target_layers={'near':None})
        self.package['macro_4h']='4H_MACRO_BEAR (sealed macro)'
        result=quality.evaluate(self.package,self.plan,self.policy)
        self.assertIn('countertrend_projection_without_observed_target',result['reasons'])
    def test_countertrend_with_real_room_not_blanket_banned(self):
        self.plan['setup']='scalp_breakout_1m';self.package['macro_4h']='4H_MACRO_BEAR'
        self.assertEqual(quality.evaluate(self.package,self.plan,self.policy)['status'],'admitted')
    def test_overextended_minute_momentum_is_not_a_new_order(self):
        self.plan['setup']='scalp_pullback_1m';self.package['rsi_15m']=80
        self.assertIn('minute_momentum_tail_overextended',quality.evaluate(self.package,self.plan,self.policy)['reasons'])
    def test_aligned_projected_momentum_does_not_require_a_fabricated_boundary(self):
        self.plan.update(setup='scalp_breakout_1m',target_layers={'near':None})
        self.package['macro_4h']='4H_MACRO_BULL'
        self.assertEqual(quality.evaluate(self.package,self.plan,self.policy)['status'],'admitted')
    def test_invalid_or_extrapolated_near_target_cannot_authorize(self):
        for value in (float('nan'),False,None):
            self.plan['target_layers']['near']['price']=value
            self.assertEqual(quality.evaluate(self.package,self.plan,self.policy)['status'],'shadow_only')
        self.plan['target_layers']['near']={'price':110,'extrapolated':True}
        self.assertEqual(quality.evaluate(self.package,self.plan,self.policy)['status'],'shadow_only')
    def test_existing_shadow_reason_preserved(self):
        self.plan.update(shadow_only=True,shadow_reason='range_forward_validation')
        self.plan['target_layers']['near']['price']=101
        quality.attach(self.package,self.plan,self.policy)
        self.assertTrue(self.plan['shadow_only']);self.assertEqual(self.plan['shadow_reason'],'range_forward_validation')

class EntryQualityEnforcementTests(unittest.TestCase):
    def test_default_near_structure_warnings_do_not_become_a_hidden_quota(self):
        result={'status':'shadow_only','reasons':['near_structure_net_rr_insufficient']}
        with patch.dict('os.environ',{'OKXQUANT_ENTRY_QUALITY_MODE':'tail_only'}):
            self.assertEqual(quality.enforced_reasons(result),[])
    def test_default_extreme_minute_tail_is_enforced(self):
        result={'reasons':['near_structure_net_rr_insufficient','minute_momentum_tail_overextended']}
        with patch.dict('os.environ',{'OKXQUANT_ENTRY_QUALITY_MODE':'tail_only'}):
            self.assertEqual(quality.enforced_reasons(result),['minute_momentum_tail_overextended'])
    def test_observe_never_promotes_a_warning_to_order_veto(self):
        with patch.dict('os.environ',{'OKXQUANT_ENTRY_QUALITY_MODE':'observe'}):
            self.assertEqual(quality.enforced_reasons({'reasons':['minute_momentum_tail_overextended']}),[])
    def test_strict_mode_is_explicit_and_bad_mode_fails_closed(self):
        with patch.dict('os.environ',{'OKXQUANT_ENTRY_QUALITY_MODE':'enforce'}):
            self.assertEqual(quality.enforced_reasons({'reasons':['near_structure_net_rr_insufficient']}),['near_structure_net_rr_insufficient'])
        with patch.dict('os.environ',{'OKXQUANT_ENTRY_QUALITY_MODE':'invalid'}):
            with self.assertRaises(ValueError):quality.enforcement_mode()

class EntryQualityRankingTests(unittest.TestCase):
    def test_first_target_room_changes_priority_without_removing_candidate(self):
        from scripts import scalp_ranking
        bars=[{'open':99.,'close':100.,'high':101.,'low':98.,'volume':100.} for _ in range(20)]
        package={'bidPx':99.99,'askPx':100.,'instId':'TEST-USDT-SWAP','macro_4h':'4H_MACRO_BULL'}
        plan={'id':'p','action':'BUY_LONG','setup':'scalp_breakout_1m','entry_price':100.,
              'stop_loss_price':98.,'take_profit_price':120.,'entry_atr':1.,'net_rr':8.,
              'entry_quality':{'first_target_net_rr':.2,'first_observed_target':101.,'round_trip_cost':.2}}
        with patch.object(scalp_ranking,'verified_bars',return_value=bars):
            metric=scalp_ranking.describe(package,plan,vars(Policy()))
            self.assertEqual(metric['status'],'evaluated')
            self.assertEqual(metric['score_rr_basis'],'first_observed_structure')
            self.assertAlmostEqual(metric['terms']['net_rr'],.2/3)
            self.assertAlmostEqual(metric['terms']['cost_efficiency'],.8)
            pairs,report=scalp_ranking.rank([(package,plan)],vars(Policy()))
        self.assertEqual(len(pairs),1);self.assertEqual(report['candidates_removed'],0)

class ObservationalQualityLabelTests(unittest.TestCase):
    def test_non_veto_warning_does_not_claim_shadow_only_authority(self):
        p={'bidPx':99.99,'askPx':100.,'rsi_15m':50}
        plan={'action':'BUY_LONG','entry_price':100.,'stop_loss_price':98.,'take_profit_price':120.,'setup':'range_reversion','target_layers':{'near':{'price':101.,'extrapolated':False}}}
        with patch.dict('os.environ',{'OKXQUANT_ENTRY_QUALITY_MODE':'tail_only'}):r=quality.attach(p,plan,vars(Policy()))
        self.assertEqual(r['status'],'observed_warning');self.assertFalse(r['veto']);self.assertFalse(plan.get('shadow_only',False))
