import copy
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from okxquant_backend import chart_market

NOW = 1788916000000
START = NOW // 3600000 * 3600000


def row(ts=START, **changes):
    values = [str(ts), '100', '102', '99', '101', '20', '.02', '17.5', '0']
    for key, value in changes.items():
        values[int(key)] = value
    return values


class ChartMarketTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI(); app.include_router(chart_market.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        time_patch = patch.object(chart_market.time, 'time', return_value=NOW/1000)
        time_patch.start(); self.addCleanup(time_patch.stop)
        self.reader = patch.object(chart_market.public_market, 'get_json', return_value={'code':'0','data':[row(), row(START-3600000)]})
        self.get = self.reader.start(); self.addCleanup(self.reader.stop)

    def test_public_only_sorted_ohlcv_and_actual_quote_turnover(self):
        response = self.client.get('/api/v1/market/BTC-USDT-SWAP/candles?bar=1H&limit=150')
        self.assertEqual(response.status_code, 200)
        data = response.json(); self.assertEqual(data['market_environment'],'public'); self.assertTrue(data['read_only'])
        self.assertEqual([v['ts'] for v in data['candles']],[START-3600000,START])
        self.assertEqual(data['candles'][-1]['turnover'],17.5)
        self.assertEqual(data['volume_unit'],'contracts'); self.assertFalse(data['stale'])
        self.assertIn('no-store',response.headers['cache-control'])
        url = urlsplit(self.get.call_args.args[0])
        self.assertEqual((url.scheme,url.netloc,url.path),('https','www.okx.com','/api/v5/market/candles'))
        self.assertEqual(parse_qs(url.query),{'instId':['BTC-USDT-SWAP'],'bar':['1H'],'limit':['150']})
        self.assertEqual(self.get.call_args.kwargs,{'timeout':5,'simulated':False})

    def test_one_minute_request_has_correct_interval_and_public_transport(self):
        anchor = NOW // 60000 * 60000
        self.get.return_value = {'code': '0', 'data': [row(anchor), row(anchor-60000)]}
        response = self.client.get('/api/v1/market/BTC-USDT-SWAP/candles?bar=1m&limit=150')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['bar'], '1m')
        self.assertEqual(len(data['candles']), 2)
        self.assertFalse(data['has_gaps'])
        self.assertFalse(data['stale'])
        self.assertEqual(parse_qs(urlsplit(self.get.call_args.args[0]).query)['bar'], ['1m'])
        self.assertFalse(self.get.call_args.kwargs['simulated'])
        self.get.return_value = {'code': '0', 'data': [row(anchor-120000)]}
        self.assertTrue(self.client.get('/api/v1/market/BTC-USDT-SWAP/candles?bar=1m').json()['stale'])

    def test_dynamic_symbols_are_not_limited_to_the_old_six_coin_list(self):
        self.assertEqual(self.client.get('/api/v1/market/WLD-USDT-SWAP/candles').status_code,200)
        self.assertEqual(self.client.get('/api/v1/market/1000PEPE-USDT-SWAP/candles').status_code,200)

    def test_invalid_input_never_reaches_the_public_transport(self):
        for path in ('BTC-USDT/candles','btc-USDT-SWAP/candles','BTC-USDT-SWAP/candles?bar=1W',
                     'BTC-USDT-SWAP/candles?limit=301','BTC-USDT-SWAP/candles?limit=9',
                     'BTC-USDT-SWAP/candles?limit=nan'):
            self.get.reset_mock()
            response = self.client.get('/api/v1/market/'+path)
            self.assertEqual(response.status_code,422,path); self.get.assert_not_called()

    def test_upstream_failure_does_not_manufacture_bars_or_reuse_success_as_fresh(self):
        self.assertEqual(self.client.get('/api/v1/market/BTC-USDT-SWAP/candles').status_code,200)
        self.get.side_effect=RuntimeError('provider secret detail must not escape')
        response=self.client.get('/api/v1/market/BTC-USDT-SWAP/candles')
        self.assertEqual(response.status_code,502)
        self.assertNotIn('candles',response.json()); self.assertNotIn('secret',response.text)

    def test_empty_and_rejected_exchange_data_are_not_successful_charts(self):
        for value in ({'code':'0','data':[]},{'code':'1','data':[row()]},{'data':[row()]},[],None):
            self.get.return_value=value
            self.assertEqual(self.client.get('/api/v1/market/BTC-USDT-SWAP/candles').status_code,502)

    def test_invalid_geometry_unknown_volume_and_future_clock_are_rejected(self):
        bad = [row(**{'1':True}),row(**{'2':'98'}),row(**{'3':'103'}),row(**{'4':'NaN'}),
               row(**{'5':None}),row(**{'5':'-1'}),row(**{'7':'Infinity'}),row(**{'8':None}),
               row(**{'8':True}),row(NOW+60001),row()[:6]]
        for value in bad:
            with self.subTest(row=value):
                self.get.return_value={'code':'0','data':[value]}
                self.assertEqual(self.client.get('/api/v1/market/BTC-USDT-SWAP/candles').status_code,502)

    def test_zero_volume_and_confirmed_bars_are_real_observations(self):
        self.get.return_value={'code':'0','data':[row(**{'5':'0','7':'0','8':'1'})]}
        data=self.client.get('/api/v1/market/BTC-USDT-SWAP/candles').json()
        self.assertEqual(data['candles'][0]['vol'],0); self.assertTrue(data['candles'][0]['confirmed'])

    def test_duplicate_conflicts_are_rejected_without_mutating_source(self):
        raw=[row(),row()]; original=copy.deepcopy(raw)
        values,_=chart_market.normalize_candles(raw,NOW)
        self.assertEqual(len(values),1);self.assertEqual(raw,original)
        raw[1][4]='100.5'
        with self.assertRaises(ValueError):chart_market.normalize_candles(raw,NOW)

    def test_low_price_precision_is_preserved(self):
        raw=[row(**{'1':'.08981','2':'.09003','3':'.08911','4':'.08982'})]
        values,precision=chart_market.normalize_candles(raw,NOW)
        self.assertEqual(precision,5);self.assertEqual(values[0]['close'],.08982)

    def test_gaps_and_stale_candles_are_labelled_not_padded(self):
        self.get.return_value={'code':'0','data':[row(START-7200000),row(START-14400000)]}
        data=self.client.get('/api/v1/market/BTC-USDT-SWAP/candles').json()
        self.assertEqual(len(data['candles']),2);self.assertTrue(data['has_gaps']);self.assertTrue(data['stale'])
