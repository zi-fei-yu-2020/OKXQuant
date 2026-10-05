import unittest
from scripts import execution_quality as quality


class ExecutionQualityTests(unittest.TestCase):
    def test_receipt_calculates_actual_slippage_latency_fill_type_and_partial_ratio(self):
        submission={'submitted_at':1000,'client_id':'c1','order_type':'post_only','plan':{
            'entry':100,'size':4,'side':'long','decision_id':'d1','candidate_id':'p1',
            'entry_market_snapshot':{'spread_bps':2},'liquidity_admission':{'status':'accepted'},
            'cost_model':{'taker_taker_total':.2}}}
        fill={'instId':'BTC-USDT-SWAP','ordId':'o1','tradeId':'t1','billId':'b1','fillPx':'100.05',
              'fillSz':'1','ts':'1001200','execType':'T','fee':'-.05','feeCcy':'USDT'}
        row=quality.receipt('demo',fill,submission)
        self.assertEqual(row['fill_type'],'taker')
        self.assertEqual(row['ack_to_fill_latency_ms'],1200)
        self.assertEqual(row['partial_fill_ratio'],.25)
        self.assertAlmostEqual(row['adverse_slippage_bps'],5,places=6)
        self.assertEqual(row['entry_market_snapshot']['spread_bps'],2)

    def test_short_favorable_fill_has_negative_adverse_slippage(self):
        submission={'submitted_at':1000,'client_id':'c1','order_type':'limit','plan':{'entry':100,'size':1,'side':'short'}}
        fill={'instId':'BTC-USDT-SWAP','ordId':'o1','tradeId':'t1','fillPx':'100.1','fillSz':'1','ts':'1001000','execType':'M'}
        row=quality.receipt('demo',fill,submission)
        self.assertLess(row['adverse_slippage_bps'],0)
        self.assertEqual(row['fill_type'],'maker')


if __name__=='__main__':unittest.main()
