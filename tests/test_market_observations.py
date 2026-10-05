import unittest
from scripts import market_observations as observations


class MarketObservationTests(unittest.TestCase):
    def getter(self,url):
        if '/ticker?' in url:return {'data':[{'ts':'1000000','last':'100','bidPx':'99.99','askPx':'100.01'}]}
        if 'mark-price?' in url:return {'data':[{'ts':'1000000','markPx':'100.1'}]}
        if 'index-tickers?' in url:return {'data':[{'ts':'1000000','idxPx':'100'}]}
        if 'funding-rate?' in url:return {'data':[{'ts':'1000000','fundingTime':'1200000','fundingRate':'0.0001'}]}
        if 'open-interest?' in url:return {'data':[{'ts':'1000000','oiUsd':'5000000'}]}
        if 'long-short' in url:return {'data':[['1000000','1.2']]}
        if 'taker-volume' in url:return {'data':[['1000000','30','20']]}
        raise AssertionError(url)

    def test_fields_keep_source_time_age_and_real_derivative_deltas(self):
        old={'instId':'BTC-USDT-SWAP','observed_at_ms':700000,'fields':{
            'open_interest_usd':{'value':4000000},'funding_rate':{'value':.00005},
            'long_short_ratio':{'value':1.1},'taker_net_volume':{'value':5}}}
        row=observations.collect_one({'instId':'BTC-USDT-SWAP'},getter=self.getter,now_ms=1000100,history=[old])
        self.assertEqual(row['quality']['status'],'fresh')
        self.assertEqual(row['fields']['open_interest_usd']['source'],'okx_public_open_interest')
        self.assertEqual(row['fields']['open_interest_usd']['age_ms'],100)
        self.assertAlmostEqual(row['fields']['basis_bps']['value'],10,places=6)
        self.assertEqual(row['fields']['basis_bps']['source'],'okx_mark_vs_index')
        self.assertEqual(row['fields']['funding_rate']['freshness_limit_ms'],300000)
        self.assertEqual(row['fields']['open_interest_usd']['freshness_limit_ms'],60000)
        self.assertEqual(row['derivatives_history']['open_interest_usd_delta_5m'],1000000)
        self.assertEqual(row['derivatives_history']['funding_rate_delta_5m'],.00005)

    def test_failed_read_is_unavailable_not_zero(self):
        def failed(url):
            if '/ticker?' in url:return self.getter(url)
            raise OSError('upstream')
        row=observations.collect_one({'instId':'BTC-USDT-SWAP'},getter=failed,now_ms=1000100,history=[])
        self.assertEqual(row['quality']['status'],'partial')
        self.assertIsNone(row['fields']['funding_rate']['value'])
        self.assertEqual(row['fields']['funding_rate']['status'],'unavailable')
        self.assertFalse(row['fields']['funding_rate']['fallback_used'])
        self.assertIsNone(row['derivatives_history']['open_interest_usd_delta_5m'])


if __name__=='__main__':unittest.main()
