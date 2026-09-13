import unittest
from scripts.horizon_stats import rebuild

class HorizonStatsTests(unittest.TestCase):
    def test_groups_by_horizon_and_strategy_with_fees(self):
        result=rebuild([
            {"status":"closed","horizon":"scalp","strategy_type":"breakout","net_pnl":2,"fee":-0.2,"duration_seconds":60},
            {"status":"closed","horizon":"swing","strategy_type":"pullback_reclaim","net_pnl":-1,"fee":-0.1,"duration_seconds":3600},
            {"status":"holding","horizon":"scalp","net_pnl":99},
        ])
        self.assertEqual(result['scalp']['closed'],1)
        self.assertEqual(result['swing']['losses'],1)
        self.assertEqual(result['by_strategy']['scalp:breakout']['net_pnl'],2)
        self.assertEqual(result['swing']['avg_hold_seconds'],3600)

if __name__=='__main__': unittest.main()
