import unittest
from scripts import execution_liquidity as liquidity
from scripts.risk_policy import Policy
from scripts.execution_costs import from_policy


class ExecutionLiquidityTests(unittest.TestCase):
    def book(self, *, ts=1_000_000, bid_size='20', ask_size='20', spread=.02):
        bid=100-spread/2;ask=100+spread/2
        return {'data':[{'ts':str(ts),
            'bids':[[str(bid-i*.01),bid_size,'0','1'] for i in range(20)],
            'asks':[[str(ask+i*.01),ask_size,'0','1'] for i in range(20)]}]}

    def test_snapshot_exposes_spread_depth_imbalance_and_freshness(self):
        snap=liquidity.snapshot(self.book(),ct_val=.01,now_ms=1_000_500)
        self.assertAlmostEqual(snap['spread'],.02,places=8)
        self.assertAlmostEqual(snap['spread_bps'],2,places=3)
        self.assertEqual(snap['age_ms'],500)
        self.assertGreater(snap['depth']['10']['bid_usdt'],0)
        self.assertGreater(snap['depth']['10']['ask_usdt'],0)
        self.assertAlmostEqual(snap['imbalance_10bps'],0,places=2)
        self.assertNotIn('_bids',liquidity.public_snapshot(snap))

    def test_admission_uses_actual_size_and_conservative_cost(self):
        policy=Policy(max_entry_spread_bps=5,minimum_depth_multiple=2,
                      orderbook_max_age_ms=1000,minimum_cost_edge_multiple=2)
        snap=liquidity.snapshot(self.book(),ct_val=.01,now_ms=1_000_500)
        costs=from_policy(100,103,policy,spread=snap['spread'])
        result=liquidity.admit(snap,side='long',order_size=2,entry=100,
                               take_profit=103,policy=policy,cost_model=costs)
        self.assertEqual(result['status'],'accepted')
        self.assertGreaterEqual(result['depth_multiple'],2)
        self.assertGreaterEqual(result['cost_edge_multiple'],2)

    def test_stale_wide_shallow_and_unfunded_edge_fail_closed(self):
        policy=Policy(max_entry_spread_bps=3,minimum_depth_multiple=10,
                      orderbook_max_age_ms=200,minimum_cost_edge_multiple=20)
        stale=liquidity.snapshot(self.book(),ct_val=.01,now_ms=1_001_000)
        costs=from_policy(100,100.2,policy,spread=stale['spread'])
        with self.assertRaisesRegex(liquidity.LiquidityRejected,'stale'):
            liquidity.admit(stale,side='long',order_size=1,entry=100,take_profit=100.2,policy=policy,cost_model=costs)
        wide=liquidity.snapshot(self.book(spread=.1),ct_val=.01,now_ms=1_000_100)
        with self.assertRaisesRegex(liquidity.LiquidityRejected,'spread'):
            liquidity.admit(wide,side='long',order_size=1,entry=100,take_profit=103,policy=policy,
                            cost_model=from_policy(100,103,policy,spread=wide['spread']))
        shallow=liquidity.snapshot(self.book(bid_size='.001',ask_size='.001'),ct_val=.01,now_ms=1_000_100)
        with self.assertRaisesRegex(liquidity.LiquidityRejected,'depth'):
            liquidity.admit(shallow,side='long',order_size=1,entry=100,take_profit=103,policy=policy,
                            cost_model=from_policy(100,103,policy,spread=shallow['spread']))
        fresh=liquidity.snapshot(self.book(),ct_val=.01,now_ms=1_000_100)
        with self.assertRaisesRegex(liquidity.LiquidityRejected,'cost'):
            liquidity.admit(fresh,side='long',order_size=1,entry=100,take_profit=100.2,policy=policy,
                            cost_model=from_policy(100,100.2,policy,spread=fresh['spread']))

    def test_incomplete_book_never_becomes_zero_liquidity(self):
        for payload in ({}, {'data':[]}, {'data':[{'ts':'1','bids':[],'asks':[]}]}):
            with self.subTest(payload=payload):
                with self.assertRaises(liquidity.LiquidityRejected):
                    liquidity.snapshot(payload,ct_val=.01,now_ms=1000)


if __name__=='__main__':
    unittest.main()
