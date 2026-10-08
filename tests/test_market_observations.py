import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
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

    def test_indexed_derivatives_match_reference_for_unsorted_rows_and_gaps(self):
        history=[]
        for inst in ('BTC-USDT-SWAP','ETH-USDT-SWAP'):
            for i in range(80):
                at=1_000_000+i*60_000
                fields={name:{'value':float(i)} for name in observations.DERIVATIVE_FIELDS}
                if i%9==0:fields['funding_rate']={'value':None}
                history.append({'instId':inst,'observed_at_ms':at,'fields':fields})
        history.reverse()
        current={'instId':'BTC-USDT-SWAP','observed_at_ms':6_000_000,
                 'fields':{name:{'value':100.} for name in observations.DERIVATIVE_FIELDS}}
        # Independent reference for the former scan/max behavior.
        expected={}
        rows=[row for row in history if row['instId']==current['instId'] and row['observed_at_ms']<current['observed_at_ms']]
        for name in observations.DERIVATIVE_FIELDS:
            for label,seconds in observations.DERIVATIVE_WINDOWS:
                target=current['observed_at_ms']-seconds*1000
                candidates=[row for row in rows if (row['fields'].get(name) or {}).get('value') is not None
                            and row['observed_at_ms']<=target]
                previous=max(candidates,key=lambda row:row['observed_at_ms']) if candidates else None
                value=((previous or {}).get('fields',{}).get(name) or {}).get('value')
                expected[f'{name}_delta_{label}']=None if value is None else 100.-value
        index=observations._history_index(history)
        self.assertEqual(observations.derive(current,history),expected)
        self.assertEqual(observations.derive(current,history,index),expected)

    def test_history_index_skips_invalid_rows_and_uses_first_duplicate_timestamp(self):
        history=[
            {'instId':'BTC-USDT-SWAP','observed_at_ms':700000,'fields':{'open_interest_usd':{'value':4}}},
            {'instId':'BTC-USDT-SWAP','observed_at_ms':700000,'fields':{'open_interest_usd':{'value':9}}},
            {'instId':'BTC-USDT-SWAP','observed_at_ms':'bad','fields':{'open_interest_usd':{'value':100}}},
        ]
        current={'instId':'BTC-USDT-SWAP','observed_at_ms':1_000_000,
                 'fields':{'open_interest_usd':{'value':10}}}
        result=observations.derive(current,history,observations._history_index(history))
        self.assertEqual(result['open_interest_usd_delta_5m'],6)
        self.assertEqual(result['open_interest_usd_delta_15m'],None)

    def _collect_with_history(self, row_count):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);history_path=root/'market_observations.jsonl'
        sample={'instId':'BTC-USDT-SWAP','observed_at_ms':1,'fields':{}}
        line=json.dumps(sample,separators=(',',':'))+'\n'
        history_path.write_text(line*row_count,encoding='utf-8')
        with patch.object(observations,'DATA',root), \
             patch.object(observations,'HISTORY',history_path), \
             patch.object(observations,'LATEST',root/'latest.json'), \
             patch('scripts.instrument_pool.load_instruments',return_value=[{'instId':'BTC-USDT-SWAP','type':'crypto'}]), \
             patch('scripts.public_market.get_json',side_effect=self.getter), \
             patch.object(observations,'_write_trimmed_history',wraps=observations._write_trimmed_history) as trim:
            payload=observations.collect()
            lines=history_path.read_text(encoding='utf-8').splitlines()
            return payload,trim,len(lines),json.loads(lines[-1])

    def test_history_limit_uses_slack_instead_of_rewriting_every_collection(self):
        payload,trim,line_count,_=self._collect_with_history(observations.HISTORY_LIMIT)
        trim.assert_not_called()
        self.assertEqual(line_count,observations.HISTORY_LIMIT+1)
        self.assertEqual(len(payload['items']),1)

    def test_history_compacts_in_batches_without_dropping_recent_observation(self):
        payload,trim,line_count,retained=self._collect_with_history(observations.HISTORY_LIMIT+observations.HISTORY_TRIM_SLACK)
        trim.assert_called_once()
        self.assertEqual(line_count,observations.HISTORY_LIMIT)
        self.assertEqual(retained['observed_at_ms'],payload['items'][0]['observed_at_ms'])
        self.assertEqual(len(payload['items']),1)

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
