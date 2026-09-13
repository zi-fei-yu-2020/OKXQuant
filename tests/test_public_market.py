import io
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import public_market as market
import factor_library
import ai_factor_trader
from okx_runtime import OKXEnvironment

URL = 'https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP'


def envelope(data=None):
    return {'code': '0', 'data': data if data is not None else [{'last': '100'}]}


def indicator_response():
    values = {'ADX': {'adx': '24.3'}, 'KDJ': {'j': '55.0'},
              'BBWIDTH': {'bbWidth': '1.40'}, 'CMF': {'cmf': '0.12'}}
    return envelope([{'data': [{'timeframes': {'1H': {'indicators': {
        k: [{'ts': '1700000000000', 'values': v}] for k, v in values.items()
    }}}}]}])


def fork_fetch(same_key, index, calls, active, peak):
    def response(*args, **kwargs):
        with calls.get_lock(): calls.value += 1
        with active.get_lock():
            active.value += 1
            with peak.get_lock(): peak.value = max(peak.value, active.value)
        time.sleep(.15)
        with active.get_lock(): active.value -= 1
        return io.BytesIO(json.dumps(envelope()).encode())
    with patch('urllib.request.urlopen', side_effect=response), patch.object(market, '_rate_limit'):
        market.get_json(URL if same_key else URL.replace('BTC', f'ASSET{index}'))


class PublicMarketTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache_patch = patch.object(market, 'CACHE_DIR', Path(self.temporary.name))
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def test_public_calls_never_forward_credentials_or_demo_account_headers(self):
        with patch.dict('os.environ', {'OKX_API_KEY': 'secret', 'OKX_PASSPHRASE': 'secret', 'OKX_DEMO': '1'}), patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(envelope()).encode())) as send:
            market.get_json(URL)
        request = send.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertFalse(any('access' in k.lower() or 'simulated' in k.lower() or 'authorization' in k.lower() for k in request.headers))
        self.assertNotIn('secret', str(request.headers))

    def test_private_or_arbitrary_endpoints_are_rejected_before_network(self):
        bad = ['https://www.okx.com/api/v5/account/balance',
               'https://www.okx.com/api/v5/trade/order',
               URL + '&api_key=secret', URL + '&instId=ETH-USDT-SWAP',
               URL.replace('www.okx.com', 'evil.example'), URL.replace('https:', 'http:'),
               URL.replace('www.okx.com', 'secret@www.okx.com')]
        with patch('urllib.request.urlopen') as send:
            for url in bad:
                with self.assertRaises(ValueError): market.get_json(url)
            with self.assertRaises(ValueError):
                market._wire('/api/v5/trade/order', {}, {'instId': 'BTC-USDT-SWAP'}, time.monotonic() + 1)
        send.assert_not_called()

    def test_equivalent_query_order_and_mutation_do_not_poison_shared_cache(self):
        with patch.object(market, '_wire', return_value=envelope()) as send:
            data = market.get_json(URL)
            data['data'][0]['last'] = 'poison'
            # Read a separate JSON document, not an in-process shared object.
            result = market.get_json(URL)
        self.assertEqual(result['data'][0]['last'], '100')
        self.assertEqual(send.call_count, 1)

    def test_candle_prefix_reuse_preserves_requested_window_and_bar(self):
        rows = [[str(i), '1', '2', '0', str(i), '1'] for i in range(100, 0, -1)]
        with patch.object(market, '_wire', return_value=envelope(rows)) as send:
            long = market.candles('BTC-USDT-SWAP', '15m', 45)
            short = market.candles('BTC-USDT-SWAP', '15m', 24)
            other_bar = market.candles('BTC-USDT-SWAP', '1H', 16)
        self.assertEqual(len(long), 45)
        self.assertEqual(short, long[:24])
        self.assertEqual(len(other_bar), 16)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args[1]['limit'], '100')

    def test_candle_cache_expires_at_bar_rollover(self):
        with patch.object(market, '_wire', return_value=envelope([['x']])) as send, patch.object(market.time, 'time', return_value=1799.5):
            market.candles('BTC-USDT-SWAP', '15m', 1)
            market.candles('BTC-USDT-SWAP', '15m', 1)
            self.assertEqual(send.call_count, 1)
            with patch.object(market.time, 'time', return_value=1800.1):
                market.candles('BTC-USDT-SWAP', '15m', 1)
            self.assertEqual(send.call_count, 2)

    def test_ttl_expiry_and_clock_rollback_cannot_serve_old_quotes(self):
        with patch.object(market, '_wire', return_value=envelope()) as send, patch.object(market.time, 'time', return_value=100):
            market.get_json(URL)
            for stamp in (103, 90):
                with patch.object(market.time, 'time', return_value=stamp): market.get_json(URL)
        self.assertEqual(send.call_count, 3)

    def test_corrupt_cache_is_refetched(self):
        with patch.object(market, '_wire', return_value=envelope()) as send:
            market.get_json(URL)
            for cache in market.CACHE_DIR.glob('*.json'): cache.write_text('{')
            market.get_json(URL)
        self.assertEqual(send.call_count, 2)

    def test_stale_cache_is_not_returned_when_upstream_fails(self):
        with patch.object(market, '_wire', return_value=envelope()), patch.object(market.time, 'time', return_value=100):
            market.get_json(URL)
        with patch.object(market, '_wire', side_effect=market.MarketDataError('down')), patch.object(market.time, 'time', return_value=104):
            with self.assertRaises(market.MarketDataError): market.get_json(URL)

    def test_failed_and_empty_responses_are_not_cached(self):
        for payload in ({'code': '50011', 'data': []}, {'code': '0', 'data': []}, {'data': [{}]}):
            with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
                with self.assertRaises(market.MarketDataError): market.get_json(URL)
        self.assertEqual(list(market.CACHE_DIR.glob('*.json')), [])

    def test_batched_indicators_preserve_cli_defaults_and_response_values(self):
        fixture = indicator_response()
        with patch.object(market, '_wire', return_value=fixture) as send:
            first = market.indicator_values('BTC-USDT-SWAP')
            second = market.indicator_values('BTC-USDT-SWAP')
        self.assertEqual(first, fixture['data'][0]['data'][0]['timeframes']['1H']['indicators'])
        self.assertEqual(first, second)
        self.assertEqual(send.call_count, 1)
        path, params, body, _ = send.call_args.args
        self.assertEqual(path, '/api/v5/aigc/mcp/indicators')
        self.assertEqual(params, {})
        self.assertEqual(body['indicators'], {code: {'paramList': args, 'returnList': False} for code, args in market.INDICATOR_DEFAULTS.items()})

    def test_indicator_cache_and_public_mode_preserve_cli_demo_live_semantics(self):
        with patch.object(market, '_wire', return_value=indicator_response()) as send:
            with patch.object(market, '_selected', return_value=OKXEnvironment('demo', 'key', 'secret', 'pass')):
                market.indicator_values('BTC-USDT-SWAP')
                self.assertTrue(send.call_args.kwargs['simulated'])
                market.indicator_values('BTC-USDT-SWAP')
                self.assertEqual(send.call_count, 1)
            with patch.object(market, '_selected', return_value=OKXEnvironment('live', 'key', 'secret', 'pass')):
                market.indicator_values('BTC-USDT-SWAP')
                self.assertFalse(send.call_args.kwargs['simulated'])
                self.assertEqual(send.call_count, 2)
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(envelope()).encode())) as send:
            market._wire('/api/v5/market/ticker', {'instId': 'BTC-USDT-SWAP'}, None, time.monotonic() + 3, simulated=True)
        headers = {k.lower(): v for k, v in send.call_args.args[0].headers.items()}
        self.assertEqual(headers['x-simulated-trading'], '1')
        self.assertFalse(any('access' in key or 'authorization' in key for key in headers))

    def test_incomplete_indicator_batch_is_not_marked_success_or_cached(self):
        fixture = indicator_response()
        fixture['data'][0]['data'][0]['timeframes']['1H']['indicators'].pop('CMF')
        with patch.object(market, '_wire', return_value=fixture):
            with self.assertRaises(market.MarketDataError): market.indicator_values('BTC-USDT-SWAP')
        self.assertEqual(list(market.CACHE_DIR.glob('*.json')), [])

    def test_one_missing_indicator_preserves_others_without_caching_partial_batch(self):
        fixture = indicator_response()
        fixture['data'][0]['data'][0]['timeframes']['1H']['indicators']['CMF'] = []
        @market.observe_collection
        def collect():
            return {'indicators': market.available_indicators('BTC-USDT-SWAP')}
        with patch.object(market, '_wire', return_value=fixture):
            result = collect()
        self.assertEqual(result['indicators']['ADX'][0]['values']['adx'], '24.3')
        self.assertNotIn('CMF', result['indicators'])
        self.assertEqual(result['collection_quality']['status'], 'partial')
        self.assertEqual(list(market.CACHE_DIR.glob('*.json')), [])

    def test_read_failure_is_explicit_in_collection_quality(self):
        @market.observe_collection
        def collect():
            try: market.get_json(URL)
            except market.MarketDataError: pass
            return {'price': 0}
        with patch.object(market, '_wire', side_effect=market.MarketDataError('down')):
            result = collect()
        self.assertEqual(result['collection_quality']['status'], 'partial')
        self.assertEqual(result['collection_quality']['failed_reads'], 1)

    def test_private_smart_money_failure_is_not_cached_or_retried(self):
        with patch.object(market, '_selected', return_value=OKXEnvironment('demo', 'KEY', 'SECRET', 'PASS')), patch.object(market.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='private body')) as cli:
            with self.assertRaises(market.MarketDataError) as caught:
                market.smart_money_overview(['BTC'])
        self.assertEqual(cli.call_count, 1)
        self.assertNotIn('private body', str(caught.exception))
        self.assertEqual(list(market.CACHE_DIR.glob('*.json')), [])

    def test_outer_collection_deadline_prevents_new_network_calls(self):
        with patch('urllib.request.urlopen') as send:
            with self.assertRaises(market.MarketDataError):
                market.run_with_deadline(time.monotonic() - 1, market.get_json, URL)
        send.assert_not_called()

    def test_public_candle_failure_does_not_spawn_cli(self):
        with patch.object(ai_factor_trader.market, 'candles', side_effect=market.MarketDataError('down')), patch.object(ai_factor_trader.subprocess, 'run') as cli:
            self.assertEqual(ai_factor_trader.fetch_candles_direct('BTC-USDT-SWAP'), [])
        cli.assert_not_called()

    def test_rate_limiter_spaces_requests_and_has_a_deadline(self):
        now = time.monotonic()
        market._rate_limit('unit-rate', .08, now + 2)
        market._rate_limit('unit-rate', .08, now + 2)
        self.assertGreaterEqual(time.monotonic() - now, .075)
        with self.assertRaises(market.MarketDataError):
            market._rate_limit('unit-rate', 1, time.monotonic() + .01)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Real Linux process lock verification')
    def test_cross_process_cache_single_flight_and_global_http_cap(self):
        ctx = multiprocessing.get_context('fork')
        for same_key, count in [(True, 1), (False, 6)]:
            for cache in market.CACHE_DIR.glob('*.json'): cache.unlink()
            calls, active, peak = [ctx.Value('i', 0) for _ in range(3)]
            workers = [ctx.Process(target=fork_fetch, args=(same_key, index, calls, active, peak)) for index in range(6)]
            try:
                for worker in workers: worker.start()
                for worker in workers:
                    worker.join(10)
                    self.assertEqual(worker.exitcode, 0)
            finally:
                for worker in workers:
                    if worker.is_alive(): worker.terminate(); worker.join(2)
            self.assertEqual(calls.value, count)
            self.assertLessEqual(peak.value, market.HTTP_SLOTS)
            if not same_key: self.assertGreaterEqual(peak.value, 2)

    def test_separate_threads_share_the_same_file_lock(self):
        with market.file_lock('held', time.monotonic() + 1):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: self._try_held_lock())
                self.assertTrue(future.result(timeout=2))
        with market.file_lock('held', time.monotonic() + .1): pass

    def _try_held_lock(self):
        try:
            with market.file_lock('held', time.monotonic() + .05): pass
        except market.MarketDataError: return True
        return False

    def test_authenticated_smart_money_has_scoped_cache_and_no_shell(self):
        demo = OKXEnvironment('demo', 'KEY1', 'SECRET1', 'PASS1')
        result = SimpleNamespace(returncode=0, stdout=json.dumps({'data': []}))
        with patch.object(market, '_selected', return_value=demo), patch.object(market.subprocess, 'run', return_value=result) as cli:
            market.smart_money_overview(['ETH', 'BTC'])
            market.smart_money_overview(['BTC', 'ETH'])
            self.assertEqual(cli.call_count, 1)
            self.assertEqual(cli.call_args.args[0][:3], ['okx', '--demo', 'smartmoney'])
            self.assertFalse(cli.call_args.kwargs['shell'])
            self.assertEqual(cli.call_args.kwargs['env']['OKX_DEMO'], '1')
            with patch.object(market, '_selected', return_value=OKXEnvironment('demo', 'KEY1', 'ROTATED', 'PASS1')):
                market.smart_money_overview(['BTC', 'ETH'])
            self.assertEqual(cli.call_count, 2)
        serialized = '\n'.join(p.read_text() for p in market.CACHE_DIR.glob('*.json'))
        self.assertNotIn('KEY1', serialized)
        self.assertNotIn('SECRET1', serialized)

    def test_unknown_oauth_identity_never_reuses_smart_money_cache(self):
        with patch.object(market, '_selected', return_value=OKXEnvironment('demo', '', '', '')), patch.object(market.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"data": []}')) as cli:
            market.smart_money_overview(['BTC'])
            market.smart_money_overview(['BTC'])
        self.assertEqual(cli.call_count, 2)

    def test_factor_refresh_is_coalesced_and_preserves_original_timestamp(self):
        items = [{'instId': 'BTC-USDT-SWAP', 'ccy': 'BTC', 'name': 'BTC'}]
        def compute(item, pool):
            time.sleep(.05)
            return {'instId': item['instId'], 'price': 100, 'collection_quality': {'status': 'fresh', 'oldest_source_at': time.time()}}
        with patch.object(factor_library, 'FACTOR_LIB_CACHE_FILE', str(market.CACHE_DIR / 'snapshot.json')), patch.object(factor_library, 'load_instruments', return_value=items), patch.object(market, 'account_scope', return_value='account-1'), patch.object(market, 'smart_money_overview', return_value=[]) as sm, patch.object(factor_library, 'compute_instrument_factors', side_effect=compute) as compute_mock:
            with ThreadPoolExecutor(max_workers=3) as pool:
                snapshots = list(pool.map(lambda _: factor_library.update_factor_library(), range(3)))
            self.assertEqual(sm.call_count, 1)
            self.assertEqual(compute_mock.call_count, 1)
            self.assertEqual(snapshots[0], snapshots[1])
            self.assertEqual(snapshots[1], snapshots[2])
            with patch.object(market, 'account_scope', return_value='account-2'):
                factor_library.update_factor_library()
            self.assertEqual(sm.call_count, 2)
            with patch.object(factor_library, 'load_instruments', return_value=[{**items[0], 'instId': 'ETH-USDT-SWAP'}]):
                factor_library.update_factor_library()
            self.assertEqual(sm.call_count, 3)

    def test_partial_factor_snapshot_cannot_be_reused_as_fresh(self):
        item = {'instId': 'BTC-USDT-SWAP', 'ccy': 'BTC', 'name': 'BTC'}
        result = {'price': 0, 'collection_quality': {'status': 'partial', 'oldest_source_at': time.time()}}
        with patch.object(factor_library, 'FACTOR_LIB_CACHE_FILE', str(market.CACHE_DIR / 'snapshot.json')), patch.object(factor_library, 'load_instruments', return_value=[item]), patch.object(market, 'account_scope', return_value='account-1'), patch.object(market, 'smart_money_overview', return_value=[]), patch.object(factor_library, 'compute_instrument_factors', return_value=result) as compute:
            self.assertEqual(factor_library.update_factor_library()['data_status'], 'partial')
            factor_library.update_factor_library()
        self.assertEqual(compute.call_count, 2)


if __name__ == '__main__':
    unittest.main()
