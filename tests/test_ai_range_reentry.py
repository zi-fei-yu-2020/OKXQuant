import copy,unittest
from unittest.mock import patch
from scripts import entry_candidates as plans,trading_prompt
from scripts.risk_policy import Policy
from test_entry_candidates import package,selection

class RangeReentryTests(unittest.TestCase):
    def scenario(self):
        p=package();p['macro_4h']='4H_MACRO_RANGE'
        p['entry_candles']['15M']['rows'][-1]['low']=98.
        return p
    def result(self,p):
        with patch.object(plans,'hourly_trend',return_value='range'):
            return plans.catalog(p,vars(Policy()))
    def test_near_boundary_without_touch_is_not_a_range_reversal(self):
        p=self.scenario();p['entry_candles']['15M']['rows'][-1]['low']=98.2
        result=self.result(p)
        self.assertFalse(any(x['setup']=='range_reversion' for x in result['plans']))
        self.assertTrue(any(x['reason']=='range_boundary_not_tested_and_reentered' for x in result['checks']))
    def test_actual_test_and_reentry_has_checkable_boundary_and_structural_invalidation(self):
        p=self.scenario();before=copy.deepcopy(p);result=self.result(p)
        plan=next(x for x in result['plans'] if x['setup']=='range_reversion')
        self.assertEqual(plan['range_reentry_level'],98.)
        self.assertLess(plan['stop_loss_price'],98.)
        self.assertEqual(plan['take_profit_price'],110.)
        facts=trading_prompt.facts_for(p)
        self.assertEqual(facts['/entry_candles/1H/prior_12/low']['value'],98.)
        with patch.object(plans,'hourly_trend',return_value='range'):
            result=trading_prompt.candidate(p,selection(plan),facts)
        self.assertTrue(result['contract_valid'],result);self.assertEqual(p,before)
    def test_failed_rr_cannot_expand_observed_target_to_admit_the_trade(self):
        p=self.scenario();p['entry_candles']['15M']['rows'][-1]['low']=93.
        result=self.result(p)
        self.assertFalse(any(x['setup']=='range_reversion' for x in result['plans']))
        rejected=[x for x in result['checks'] if x.get('setup')=='range_reversion' and x['reason']=='net_rr_below_policy']
        self.assertTrue(rejected);self.assertEqual(rejected[0]['geometry']['take_profit_price'],110.)
    def test_quote_cannot_leave_the_reclaimed_range_before_dispatch(self):
        p=self.scenario();plan=next(x for x in self.result(p)['plans'] if x['setup']=='range_reversion')
        with patch.object(plans,'hourly_trend',return_value='range'),self.assertRaises(ValueError):
            plans.validate_live_quote(p,plan['id'],97.9)
