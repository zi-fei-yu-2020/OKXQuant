import base64
import hashlib
import hmac
import io
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from scripts import algo_reader as reader
from scripts.okx_runtime import OKXEnvironment
import ai_factor_trader as trader
from okxquant_backend import okx_trade_service


def order(inst='SOL-USDT-SWAP', identity='88', size='4'):
    return {'algoId': identity, 'instId': inst, 'ordType': 'oco', 'state': 'live',
            'posSide': 'long', 'side': 'sell', 'reduceOnly': 'true', 'sz': size,
            'tpTriggerPx': '106', 'slTriggerPx': '101'}


class Clock:
    def __init__(self): self.value = 1000.
    def monotonic(self): return self.value
    def sleep(self, duration): self.value += max(0, duration)


def process_read(directory, priority, key, result_path):
    reader.STATE_DIR = Path(directory)
    env = OKXEnvironment('demo', key, 'fake-secret', 'fake-pass')
    def wire(*args):
        with (Path(directory) / 'wire.jsonl').open('a') as f:
            f.write(json.dumps({'priority': priority, 'at': time.monotonic()}) + '\n')
        time.sleep(.03)
        return [order()]
    with patch.object(reader, '_signed_page', side_effect=wire):
        try:
            reader.read_algo_orders(env, priority=priority, force=True, timeout=3)
            result = 'ok'
        except reader.AlgoReadError as exc: result = exc.category
    Path(result_path).write_text(result)


class AlgoReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for module in (reader, trader.algo_reader):
            p = patch.object(module, 'STATE_DIR', self.directory); p.start(); self.addCleanup(p.stop)
        self.env = OKXEnvironment('demo', 'FAKE-KEY', 'FAKE-SECRET', 'FAKE-PASS')

    def fake_clock(self):
        clock = Clock()
        for name, fn in [('monotonic', clock.monotonic), ('sleep', clock.sleep)]:
            p=patch.object(reader.time, name, side_effect=fn); p.start(); self.addCleanup(p.stop)
        return clock

    def response(self, value): return io.BytesIO(json.dumps(value).encode())

    def error(self, status, retry=None):
        return urllib.error.HTTPError('https://www.okx.com'+reader.ENDPOINT, status, 'private error',
                                      {'Retry-After': retry} if retry else {}, io.BytesIO(b'PRIVATE-BODY-SECRET'))

    def test_combined_account_query_is_signed_get_without_instrument_fanout(self):
        with patch('urllib.request.urlopen', return_value=self.response({'code':'0','data':[order()]})) as send:
            result=reader.read_algo_orders(self.env)
        self.assertEqual(result, [order()]); send.assert_called_once()
        req=send.call_args.args[0]; self.assertEqual(req.get_method(), 'GET'); self.assertIsNone(req.data)
        self.assertIn('ordType=conditional%2Coco', req.full_url); self.assertNotIn('instId=', req.full_url)
        h={k.lower():v for k,v in req.headers.items()}
        self.assertEqual(h['x-simulated-trading'], '1')
        path=req.full_url.removeprefix(self.env.base_url)
        expected=base64.b64encode(hmac.new(self.env.secret_key.encode(), (h['ok-access-timestamp']+'GET'+path).encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(h['ok-access-sign'], expected)

    def test_one_fresh_account_read_can_serve_five_risk_checks_and_monitor(self):
        rows=[order(f'{name}-USDT-SWAP', str(i)) for i,name in enumerate(['BTC','ETH','SOL','DOGE','SUI'])]
        with patch.object(reader, '_signed_page', return_value=rows) as send:
            for name in ['BTC','ETH','SOL','DOGE','SUI']:
                data=reader.read_algo_orders(self.env)
                self.assertEqual(len(reader.orders_for_instrument(data, name+'-USDT-SWAP')), 1)
            reader.read_algo_orders(self.env, priority='monitor')
        self.assertEqual(send.call_count, 1)

    def test_http_429_honors_retry_after_and_never_exposes_private_body(self):
        clock=self.fake_clock(); start=clock.value
        with patch('urllib.request.urlopen', side_effect=[self.error(429, '2'), self.response({'code':'0','data':[order()]})]) as send:
            with self.assertLogs(reader.LOG, level='WARNING') as logs:
                self.assertEqual(reader.read_algo_orders(self.env), [order()])
        self.assertEqual(send.call_count, 2); self.assertGreaterEqual(clock.value-start, 2)
        self.assertNotIn('PRIVATE-BODY-SECRET','\n'.join(logs.output))

    def test_transient_429_then_coverage_does_not_enter_repair_or_exit(self):
        self.fake_clock()
        position = {'instId': 'SOL-USDT-SWAP', 'posSide': 'long', 'pos': '4',
                    'markPx': '104', 'avgPx': '103'}
        with (
            patch.object(trader, 'algo_reader', reader),
            patch.object(trader.market, '_selected', return_value=self.env),
            patch('urllib.request.urlopen', side_effect=[self.error(429), self.response({'code':'0','data':[order()]})]) as send,
            patch.object(trader, 'query_positions', return_value=(True, [position], '')) as positions,
            patch.object(trader, 'run_cmd_result') as write,
            patch.object(trader, 'close_position_confirmed') as close,
        ):
            ok, detail = trader.ensure_cloud_position_protection('SOL-USDT-SWAP', 'long', 4, 106, 101)
        self.assertTrue(ok, detail)
        self.assertIn('coverage verified', detail)
        self.assertEqual(send.call_count, 2)
        positions.assert_called_once_with()
        write.assert_not_called()
        close.assert_not_called()

    def test_orphan_markers_are_pruned_but_active_marker_is_retained(self):
        orphan = self.directory / 'risk-orphan.lock'
        orphan.write_text(json.dumps({'boot': reader._boot(), 'deadline': time.monotonic()+20}))
        self.assertFalse(reader._active('risk-'))
        self.assertFalse(orphan.exists())
        with reader._marker('risk-', time.monotonic()+20):
            self.assertTrue(reader._active('risk-'))
        self.assertFalse(reader._active('risk-'))

    def test_rate_limit_exhaustion_is_unknown_not_empty(self):
        self.fake_clock()
        with patch('urllib.request.urlopen', side_effect=[self.error(429) for _ in range(3)]) as send:
            with self.assertRaises(reader.AlgoReadError) as caught: reader.read_algo_orders(self.env)
        self.assertEqual(send.call_count, 3); self.assertEqual(caught.exception.category, 'rate_limited')
        self.assertEqual(caught.exception.attempts, 3); self.assertEqual(caught.exception.code, 429)
        self.assertEqual(list(self.directory.glob('*.snapshot.json')), [])
        self.assertEqual(list(self.directory.glob('risk-*.lock')), [])

    def test_retry_after_exceeding_budget_is_shared_and_not_bypassed_by_another_key(self):
        self.fake_clock()
        with patch('urllib.request.urlopen', side_effect=self.error(429,'60')) as send:
            with self.assertRaises(reader.AlgoReadError): reader.read_algo_orders(self.env, timeout=4)
            with self.assertRaises(reader.AlgoReadError): reader.read_algo_orders(OKXEnvironment('demo','other','s','p'), timeout=4)
        send.assert_called_once()

    def test_business_rate_limit_is_retried_but_auth_and_certificate_errors_are_not(self):
        self.fake_clock()
        with patch('urllib.request.urlopen', side_effect=[self.response({'code':'50011','msg':'private','data':[]}),self.response({'code':'0','data':[]})]) as send:
            self.assertEqual(reader.read_algo_orders(self.env), [])
            self.assertEqual(send.call_count, 2)
        for status in [400,401,403]:
            with patch('urllib.request.urlopen', side_effect=self.error(status)) as send:
                with self.assertRaises(reader.AlgoReadError): reader.read_algo_orders(self.env, force=True)
            send.assert_called_once()
        import ssl
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError(ssl.SSLCertVerificationError('bad certificate'))) as send:
            with self.assertRaises(reader.AlgoReadError): reader.read_algo_orders(self.env, force=True)
        send.assert_called_once()

    def test_risk_cache_is_shorter_than_monitor_cache_and_force_bypasses_both(self):
        clock=self.fake_clock()
        with patch.object(reader, '_signed_page', return_value=[order()]) as send:
            reader.read_algo_orders(self.env)
            clock.value+=1
            reader.read_algo_orders(self.env, priority='monitor'); self.assertEqual(send.call_count,1)
            reader.read_algo_orders(self.env); self.assertEqual(send.call_count,2)
            reader.read_algo_orders(self.env, force=True); self.assertEqual(send.call_count,3)

    def test_risk_first_read_does_not_trust_even_fresh_monitor_snapshot(self):
        self.fake_clock()
        with patch.object(reader, '_signed_page', return_value=[order()]) as send:
            reader.read_algo_orders(self.env, priority='monitor')
            reader.read_algo_orders(self.env, priority='risk')
            self.assertEqual(send.call_count, 2)
            reader.read_algo_orders(self.env, priority='risk')
            self.assertEqual(send.call_count, 2)

    def test_cache_key_isolated_by_credentials_mode_and_has_no_raw_credentials(self):
        self.fake_clock()
        with patch.object(reader, '_signed_page', return_value=[order()]) as send:
            for env in [self.env,OKXEnvironment('live','FAKE-KEY','FAKE-SECRET','FAKE-PASS'),OKXEnvironment('demo','FAKE-KEY','ROTATED','FAKE-PASS')]:
                reader.read_algo_orders(env)
        self.assertEqual(send.call_count,3)
        contents=''.join(p.read_text() for p in self.directory.glob('*.json'))
        for secret in ['FAKE-KEY','FAKE-SECRET','FAKE-PASS','ROTATED']: self.assertNotIn(secret, contents)

    def test_complete_pagination_and_failure_never_returns_partial_coverage(self):
        self.fake_clock()
        first=[order(identity=str(i)) for i in range(100)]
        with patch.object(reader, '_signed_page', side_effect=[first,[order(identity='100')]]) as send:
            self.assertEqual(len(reader.read_algo_orders(self.env)),101)
        self.assertEqual(send.call_args.args[1]['after'],'99')
        with patch.object(reader, '_signed_page', side_effect=[first,reader._WireError('api_error',401)]):
            with self.assertRaises(reader.AlgoReadError): reader.read_algo_orders(self.env,force=True)
        # The previous cache must still expire normally, never be refreshed by failure.

    def test_repeated_cursor_is_unknown_and_malformed_identity_never_cached(self):
        self.fake_clock(); first=[order(identity=str(i)) for i in range(100)]
        with patch.object(reader,'_signed_page',side_effect=[first,first]):
            with self.assertRaises(reader.AlgoReadError): reader.read_algo_orders(self.env)
        with patch.object(reader,'_signed_page',return_value=[{'sz':'4'}]):
            with self.assertRaises(reader.AlgoReadError): reader.read_algo_orders(self.env)
        self.assertEqual(list(self.directory.glob('*.snapshot.json')),[])

    def test_active_write_and_completed_write_both_invalidate_cache(self):
        self.fake_clock()
        with patch.object(reader,'_signed_page',return_value=[order()]) as send:
            reader.read_algo_orders(self.env)
            with reader.algo_mutation(self.env):
                with self.assertRaises(reader.AlgoReadError): reader.read_algo_orders(self.env,timeout=.1)
            reader.read_algo_orders(self.env)
        self.assertEqual(send.call_count,2)

    def test_write_during_read_discards_response_and_fetches_again(self):
        self.fake_clock(); calls=[]
        def wire(*args):
            calls.append(1)
            if len(calls)==1:
                with reader.algo_mutation(self.env): pass
                return [order(identity='old')]
            return [order(identity='new')]
        with patch.object(reader,'_signed_page',side_effect=wire):
            rows=reader.read_algo_orders(self.env)
        self.assertEqual(rows[0]['algoId'],'new'); self.assertEqual(len(calls),2)

    def test_write_not_delayed_by_read_gate_and_bookkeeping_error_cannot_suppress_close(self):
        with reader._turn(time.monotonic()+2,'risk'):
            with reader.algo_mutation(self.env): pass
        with patch.object(reader,'_dir',side_effect=OSError('read-only filesystem')):
            calls=[]
            with reader.algo_mutation(self.env): calls.append('one-write')
        self.assertEqual(calls,['one-write'])

    def test_real_trade_write_429_is_not_retried_and_cache_is_invalidated(self):
        self.fake_clock()
        with patch.object(reader,'_signed_page',return_value=[order()]): reader.read_algo_orders(self.env)
        before=reader._epoch(reader._scope(self.env))
        with patch('urllib.request.urlopen',side_effect=self.error(429)) as send:
            with self.assertRaises(RuntimeError):
                okx_trade_service._request('POST','/api/v5/trade/close-position',{'instId':'SOL-USDT-SWAP'},self.env)
        send.assert_called_once(); self.assertNotEqual(before,reader._epoch(reader._scope(self.env)))
        self.assertIsNone(reader._cached(self.env,reader._scope(self.env),2))

    def test_non_write_cli_commands_do_not_invalidate_or_use_write_barrier(self):
        with patch.object(reader,'algo_mutation') as mutation:
            for command in ['okx --demo swap orders --json','okx market candles BTC-USDT-SWAP --json','okx --demo swap algo orders --ordType oco --json']:
                with reader.command_barrier(command,self.env): pass
            mutation.assert_not_called()
            for command in ['okx --demo swap algo amend --algoId 1','okx swap cancel BTC-USDT-SWAP --ordId 1','okx --demo swap close --instId BTC-USDT-SWAP']:
                with reader.command_barrier(command,self.env): pass
            self.assertEqual(mutation.call_count,3)

    def test_oauth_explicit_types_have_no_three_type_fanout_or_shared_cache(self):
        self.fake_clock(); env=OKXEnvironment('demo','','','')
        with patch.object(reader,'_oauth_page',return_value=[]) as send:
            reader.read_algo_orders(env); reader.read_algo_orders(env)
        self.assertEqual(send.call_count,4)
        self.assertEqual({c.args[1]['ordType'] for c in send.call_args_list},{'conditional','oco'})
        self.assertEqual(list(self.directory.glob('*.snapshot.json')),[])

    def test_post_repair_always_reads_fresh_and_never_repeats_uncertain_write(self):
        position = {'instId': 'SOL-USDT-SWAP', 'posSide': 'long', 'pos': '4',
                    'markPx': '104', 'avgPx': '103'}
        for accepted in [True, False]:
            with (
                self.subTest(accepted=accepted),
                patch.object(trader.market, '_selected', return_value=self.env),
                patch.object(trader, 'okx_private_command', side_effect=lambda command: command),
                patch.object(trader.algo_reader, 'read_algo_orders', side_effect=[[], [order()]]) as read,
                patch.object(trader, 'query_positions', return_value=(True, [position], '')) as positions,
                patch.object(trader, 'run_cmd_result', return_value={'ok': accepted, 'data': {}, 'stderr': 'timeout', 'stdout': ''}) as write,
                patch.object(trader.time, 'sleep'),
            ):
                ok, detail = trader.ensure_cloud_position_protection('SOL-USDT-SWAP', 'long', 4, 106, 101)
                self.assertTrue(ok, detail)
                write.assert_called_once()
                self.assertIn('algo place ', write.call_args.args[0])
                self.assertEqual(read.call_count, 2)
                self.assertEqual(positions.call_count, 2)
                self.assertTrue(all(call.kwargs['force'] for call in read.call_args_list))
                self.assertLessEqual(read.call_args.kwargs['timeout'], 6)
                self.assertIn('repaired and verified' if accepted else 'uncertain write', detail)

    def test_unknown_initial_read_does_not_blindly_place_repair(self):
        with patch.object(trader.algo_reader,'read_algo_orders',side_effect=trader.algo_reader.AlgoReadError('rate_limited',3,429)), patch.object(trader,'run_cmd_result') as write:
            ok,detail=trader.ensure_cloud_position_protection('SOL-USDT-SWAP','long',4,106,101)
        self.assertFalse(ok); self.assertTrue(detail.startswith('UNKNOWN:')); write.assert_not_called()

    def test_confirmed_gap_after_repair_is_distinct_from_unavailable_post_read(self):
        self.fake_clock()
        position = {'instId': 'SOL-USDT-SWAP', 'posSide': 'long', 'pos': '4',
                    'markPx': '104', 'avgPx': '103'}
        for last in ([], trader.algo_reader.AlgoReadError('rate_limited', 3, 429)):
            known_gap = isinstance(last, list)
            snapshots = [[], last, last, last, last] if known_gap else [[], last]
            with (
                self.subTest(known_gap=known_gap),
                patch.object(trader.market, '_selected', return_value=self.env),
                patch.object(trader, 'okx_private_command', side_effect=lambda command: command),
                patch.object(trader.algo_reader, 'read_algo_orders', side_effect=snapshots) as read,
                patch.object(trader, 'query_positions', return_value=(True, [position], '')) as positions,
                patch.object(trader, 'run_cmd_result', return_value={'ok': True}) as write,
            ):
                ok, detail = trader.ensure_cloud_position_protection('SOL-USDT-SWAP', 'long', 4, 106, 101)
                self.assertFalse(ok)
                write.assert_called_once()
                self.assertIn('algo place ', write.call_args.args[0])
                self.assertTrue(detail.startswith('INSUFFICIENT:' if known_gap else 'UNKNOWN:'), detail)
                self.assertEqual(read.call_count, 5 if known_gap else 2)
                self.assertEqual(positions.call_count, 5 if known_gap else 1)
                self.assertTrue(all(call.kwargs['force'] for call in read.call_args_list))

    @unittest.skipUnless(sys.platform.startswith('linux'),'OS flock process verification')
    def test_cross_process_risk_reader_preempts_waiting_monitor(self):
        ctx=multiprocessing.get_context('fork')
        monitor=ctx.Process(target=process_read,args=(self.temp.name,'monitor','one',str(self.directory/'monitor.txt')))
        risk=ctx.Process(target=process_read,args=(self.temp.name,'risk','one',str(self.directory/'risk.txt')))
        try:
            with reader._turn(time.monotonic()+3,'risk'):
                monitor.start();time.sleep(.06);risk.start()
                end=time.monotonic()+1
                while not reader._active('risk-') and time.monotonic()<end:time.sleep(.01)
                self.assertTrue(reader._active('risk-'))
            for child in [risk,monitor]:child.join(5);self.assertEqual(child.exitcode,0)
            events=[json.loads(x) for x in (self.directory/'wire.jsonl').read_text().splitlines()]
            self.assertEqual(events[0]['priority'],'risk')
            self.assertEqual((self.directory/'monitor.txt').read_text(),'deferred_for_risk_check')
        finally:
            for child in [risk,monitor]:
                if child.is_alive():child.terminate();child.join(2)

    @unittest.skipUnless(sys.platform.startswith('linux'),'OS flock process verification')
    def test_different_processes_and_keys_share_one_network_pace(self):
        ctx=multiprocessing.get_context('fork')
        workers=[ctx.Process(target=process_read,args=(self.temp.name,'risk',str(i),str(self.directory/f'{i}.txt'))) for i in range(4)]
        try:
            for child in workers:child.start()
            for child in workers:child.join(6);self.assertEqual(child.exitcode,0)
            events=[json.loads(x) for x in (self.directory/'wire.jsonl').read_text().splitlines()]
            self.assertEqual(len(events),4)
            self.assertTrue(all(b['at']-a['at']>=reader.GAP_SECONDS-.015 for a,b in zip(events,events[1:])))
        finally:
            for child in workers:
                if child.is_alive():child.terminate();child.join(2)


if __name__=='__main__':unittest.main()
