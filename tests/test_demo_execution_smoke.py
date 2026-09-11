"""Offline diagnostic safety tests: no config imports, .env, subprocess or API I/O.

The diagnostic module is imported under a non-main name. Its run() is exercised
only with replacement modules at EVERY external boundary, plus network/process
tripwires. Only the pure production sizing and OCO policies run unchanged.
"""
import copy
from contextlib import ExitStack, contextmanager
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import risk_policy as real_risk
from scripts.protection_policy import oco_coverage

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('offline_smoke_under_test', ROOT / 'scripts/demo_execution_smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)  # stdlib definitions only; never calls main().
INST, OTHER = 'BTC-USDT-SWAP', 'ETH-USDT-SWAP'
CONFIRM = 'DEMO EXECUTION TEST'


def environment(**changes):
    return SimpleNamespace(**dict(dict(mode='demo', simulated=True, configured=True,
        identity='offline-demo-account', base_url='https://www.okx.com'), **changes))


def position(inst=INST, size='0.19', **changes):
    return dict(dict(instId=inst, posSide='long', mgnMode='cross', posId='position-'+inst,
                     pos=size, markPx='100.05', avgPx='100.05', liqPx='0'), **changes)


def algo(inst=INST, size='0.19', **changes):
    return dict(dict(instId=inst, algoId='algo-'+inst, ordId='own-order', ordType='oco',
                     state='live', posSide='long', side='sell', reduceOnly='true', sz=size,
                     slTriggerPx='99.04', tpTriggerPx='104.06'), **changes)


class Offline:
    def __init__(self, **scenario):
        self.scenario = scenario
        self.env = environment()
        self.submitted = False
        self.closed = False
        self.order = None
        self.target = []
        self.other = position(OTHER, '0.5')
        self.other_before = copy.deepcopy(self.other)
        self.protection = []
        self.posts = []
        self.requests = []
        self.submit_mock = Mock(side_effect=self.submit)
        self.lock_depth = 0
        self.clock = 0
        self.path_open = Path.open

    def selected(self):
        if self.scenario.get('changed') and self.submitted:
            return environment(identity='different-account')
        return self.scenario.get('initial_env', self.env)

    @contextmanager
    def writer(self):
        if self.scenario.get('lock_failure'):
            raise TimeoutError('Offline lock busy')
        self.lock_depth += 1
        try:
            yield
        finally:
            self.lock_depth -= 1

    def tick(self):
        self.clock += 5
        return self.clock

    @property
    def cleanup_posts(self):
        return [(path, body) for path, body in self.posts if path != '/api/v5/trade/order']

    def submit(self, body):
        assert self.lock_depth == 1
        self.submitted = True
        size = body['sz']
        marker = body['attachAlgoOrds'][0]['attachAlgoClOrdId']
        state = self.scenario.get('order_state', 'filled')
        fill = '0.1' if state == 'partially_filled' else '0' if state in ('live', 'canceled') else size
        self.order = dict(instId=INST, clOrdId=body['clOrdId'], ordId='own-order',
            side='buy', posSide='long', tdMode='cross', ordType='limit', sz=size,
            accFillSz=fill, state=state, attachAlgoOrds=[{'attachAlgoId': 'attachment-not-generated-id', 'attachAlgoClOrdId': marker}])
        self.order.update(self.scenario.get('bad_order', {}))
        if fill != '0':
            self.target = [position(size=fill, **self.scenario.get('bad_position', {}))]
            if not self.scenario.get('no_oco'):
                self.protection = [algo(size=fill, ordId='', algoClOrdId=self.scenario.get('algo_marker', marker))]
        if self.scenario.get('external_algo'):
            self.protection.append(algo(algoId='external-algo', ordId='not-ours'))
        if self.scenario.get('other_changed'):
            self.other['pos'] = '0.6'
        if self.scenario.get('post_timeout'):
            raise TimeoutError('OFFLINE-FAKE-SECRET unknown REST submission')
        if self.scenario.get('interrupt'):
            raise KeyboardInterrupt()
        if self.scenario.get('post_error'):
            raise RuntimeError('OFFLINE-FAKE-SECRET REST response unreadable')
        return [{'ordId': 'own-order', 'clOrdId': body['clOrdId']}]


    def request(self, method, path, params, env, **kwargs):
        self.requests.append((method, path, copy.deepcopy(params)))
        assert env.mode == 'demo'
        assert env.identity == self.env.identity
        if method == 'POST':
            assert self.lock_depth == 1
            self.posts.append((path, copy.deepcopy(params)))
            assert params['instId'] == INST
            if path == '/api/v5/trade/order':
                return self.submit_mock(params)
            if path.endswith('cancel-order'):
                assert params['ordId'] == 'own-order'
                if self.scenario.get('cancel_timeout'):
                    raise TimeoutError('OFFLINE-FAKE-SECRET must not enter report')
                self.order['state'] = 'canceled'
                return [{}]
            if path.endswith('amend-algos'):
                assert params['algoId'] == 'algo-'+INST
                if self.scenario.get('amend_timeout'):
                    raise TimeoutError('OFFLINE-FAKE-SECRET must not enter report')
                self.protection[0]['slTriggerPx'] = params['newSlTriggerPx']
                if self.scenario.get('position_replaced'):
                    self.target[0]['posId'] = 'external-replacement'
                if self.scenario.get('already_flat'):
                    self.closed = True
                return [{}]
            raise AssertionError('Unexpected write: '+path)
        if path.endswith('/positions'):
            if self.scenario.get('already_flat') and self.closed:
                self.target = []
                self.protection = []
            if not self.submitted and self.scenario.get('bad_baseline'):
                return [position(pos=self.scenario['bad_baseline'])]
            rows = [self.other] + self.target
            if not self.submitted and (self.scenario.get('existing_position') or (self.scenario.get('late_position') and self.plan.called)):
                rows.append(position())
            return copy.deepcopy(rows)
        if path.endswith('/orders-pending'):
            if self.scenario.get('existing_pending') or (self.submitted and self.scenario.get('external_pending')):
                return [dict(instId=INST, ordId='external-order')]
            return []
        if path.endswith('/order'):
            if self.scenario.get('unknown_order'):
                raise RuntimeError('OFFLINE-FAKE-SECRET read unavailable')
            return [copy.deepcopy(self.order)] if self.order else []
        if path.endswith('/balance'):
            return [{'totalEq':'1000', 'details':[{'ccy':'USDT', 'availEq':self.scenario.get('available', '900'), 'availBal':'900'}]}]
        if path.endswith('/leverage-info'):
            return [{'posSide':'long','lever':self.scenario.get('leverage', '2')}]
        raise AssertionError('Unexpected read: '+path)

    def algorithms(self, *args, **kwargs):
        rows = [algo(OTHER, '0.5')]
        if not self.submitted and self.scenario.get('existing_algo'):
            rows.append(algo())
        return copy.deepcopy(rows + self.protection)

    def public(self, url, **kwargs):
        assert kwargs['simulated'] is True
        if 'instruments?' in url:
            return {'data':[dict(instId=inst, ctType='linear',settleCcy='USDT',state='live',
                ctVal='1',ctMult='1',lotSz='0.01',minSz=self.scenario.get('minimum', '0.01'),tickSz='0.01') for inst in (INST,OTHER)]}
        return {'data':[{'instId':INST,'askPx':'100', 'ts':self.scenario.get('quote_ts', '1000000')}]}

    def intent(self, env, pos):
        assert self.lock_depth == 1
        assert pos['instId'] == INST
        assert pos['pos'] == self.order['accFillSz']
        return 'offline-token', 'offline-confirmation'

    def close(self, token, phrase):
        assert self.lock_depth == 1
        assert (token, phrase) == ('offline-token', 'offline-confirmation')
        if self.scenario.get('close_timeout'):
            raise TimeoutError('OFFLINE-FAKE-SECRET close unknown')
        if self.scenario.get('close_unconfirmed'):
            return {'status':'pending'}
        self.target = []
        self.protection = []
        self.closed = True
        return {'status':'confirmed_closed', 'ledger_refresh':'requested'}

    def file_open(self, path, *args, **kwargs):
        path = Path(path)
        assert path.name != '.env', 'Tests must NEVER read .env'
        if (self.scenario.get('early_disk_failure') or (self.scenario.get('disk_failure') and self.submitted)) and path.suffix == '.json':
            raise OSError('Offline disk full')
        return self.path_open(path, *args, **kwargs)

    def run(self, **options):
        with ExitStack() as stack:
            temp = stack.enter_context(tempfile.TemporaryDirectory())
            def module(name, **attrs):
                result = ModuleType(name)
                result.__dict__.update(attrs)
                return result
            risk = module('scripts.risk_policy', **{k:v for k,v in vars(real_risk).items() if not k.startswith('__')})
            self.plan = Mock(wraps=real_risk.order_plan)
            risk.order_plan = self.plan
            risk.load_policy = Mock(return_value=real_risk.Policy())  # No risk_policy.json read.
            runtime = module('scripts.okx_runtime', selected_environment=self.selected,
                freeze_environment=Mock(return_value=self.scenario.get('frozen_env', self.env)),
                unfreeze_environment=Mock())
            trade = module('okxquant_backend.okx_trade_service', _request=Mock(side_effect=self.request),
                _create_intent=Mock(side_effect=self.intent), fast_close_confirmed=Mock(side_effect=self.close))
            self.trade, self.runtime = trade, runtime
            public = module('scripts.public_market', get_json=Mock(side_effect=self.public))
            modules = {'okxquant_backend': module('okxquant_backend', __path__=[]),
                'okxquant_backend.config':module('okxquant_backend.config', refresh_settings=Mock()),
                'okxquant_backend.okx_trade_service':trade, 'scripts':module('scripts', __path__=[]),
                'scripts.okx_runtime':runtime, 'scripts.risk_policy':risk,
                'scripts.trade_lock':module('scripts.trade_lock',writer=self.writer),
                'scripts.public_market':public,
                'scripts.algo_reader':module('scripts.algo_reader',read_algo_orders=Mock(side_effect=self.algorithms)),
                'scripts.protection_policy':module('scripts.protection_policy',oco_coverage=oco_coverage)}
            stack.enter_context(patch.dict(sys.modules, modules))
            stack.enter_context(patch.object(smoke, 'ROOT', Path(temp)))
            stack.enter_context(patch.object(smoke.time, 'time', return_value=1000))
            stack.enter_context(patch.object(smoke.time, 'monotonic', side_effect=self.tick))
            stack.enter_context(patch.object(smoke.time, 'sleep'))
            self.cli_mock = stack.enter_context(patch.object(subprocess, 'run', side_effect=AssertionError('CLI forbidden')))
            stack.enter_context(patch.object(subprocess, 'Popen', side_effect=AssertionError('Real process forbidden')))
            stack.enter_context(patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')))
            stack.enter_context(patch.object(Path, 'open', autospec=True, side_effect=self.file_open))
            report, output = smoke.run(options.pop('inst_id', INST), confirmation=options.pop('confirmation', CONFIRM), **options)
            self.report = report
            self.serialized = smoke.json.dumps(report)
            self.cli_mock.assert_not_called()
            assert self.lock_depth == 0
            assert 'OFFLINE-FAKE-SECRET' not in self.serialized
            return report


class AttachedAlgorithmOwnershipTests(unittest.TestCase):
    def test_generated_algo_id_can_differ_but_attached_client_id_must_match(self):
        order={'ordId':'parent','clOrdId':'owned','attachAlgoOrds':[{'attachAlgoId':'attachment', 'attachAlgoClOrdId':'ownedp'}]}
        row={'instId':INST,'algoId':'generated','algoClOrdId':'ownedp','ordId':''}
        self.assertEqual(smoke.own_algorithms([row],order,INST),[row])
        for bad in ('','foreign',None):
            with self.subTest(bad=bad),self.assertRaises(smoke.SafetyError):
                smoke.own_algorithms([{**row,'algoClOrdId':bad}],order,INST)

    def test_parent_id_and_attachment_id_cannot_substitute_for_marker(self):
        order={'ordId':'parent','clOrdId':'owned','attachAlgoOrds':[
            {'attachAlgoId':'attachment','attachAlgoClOrdId':'ownedp'}]}
        # Even matching legacy IDs cannot establish ownership without the marker.
        for row in ({'instId':INST,'algoId':'attachment','ordId':'parent'},
                    {'instId':INST,'algoId':'attachment','ordId':'parent','algoClOrdId':'foreign'}):
            with self.subTest(row=row), self.assertRaises(smoke.SafetyError):
                smoke.own_algorithms([row],order,INST)
        # A matching echo of a foreign marker is not the marker this parent submitted.
        with self.assertRaises(smoke.SafetyError):
            smoke.own_algorithms([{'instId':INST,'algoId':'generated','algoClOrdId':'foreign'}],
                {**order,'attachAlgoOrds':[{'attachAlgoClOrdId':'foreign'}]}, INST)
        with self.assertRaises(smoke.SafetyError):
            smoke.own_algorithms([{'instId':INST,'algoId':'generated','algoClOrdId':'ownedp'}],
                {**order,'attachAlgoOrds':[]}, INST)

    def test_coincident_geometry_and_timestamps_are_not_ownership(self):
        order={'ordId':'parent','clOrdId':'owned','attachAlgoOrds':[{'attachAlgoId':'attachment'}]}
        with self.assertRaises(smoke.SafetyError):
            smoke.own_algorithms([{'instId':INST,'algoId':'generated','ordId':'','cTime':'1000','sz':'.1'}],order,INST)


class SmokeSafetyTests(unittest.TestCase):
    def test_hard_gates_before_any_exchange_read_or_write(self):
        cases = [dict(initial_env=environment(mode='live')), dict(initial_env=environment(simulated=False)),
                 dict(initial_env=environment(configured=False)), dict(initial_env=environment(simulated=1)),
                 dict(initial_env=environment(base_url='https://untrusted.invalid')),
                 dict(frozen_env=environment(mode='live'))]
        for scenario in cases:
            with self.subTest(scenario=scenario):
                fake = Offline(**scenario)
                self.assertEqual(fake.run()['status'], 'failed')
                fake.trade._request.assert_not_called()
                fake.submit_mock.assert_not_called()
        for options in [dict(confirmation=''),dict(inst_id='OTHER'),*[dict(max_notional=x) for x in (0,101,float('nan'),float('inf'),True)]]:
            with self.subTest(options=options):
                fake = Offline()
                self.assertEqual(fake.run(**options)['status'], 'failed')
                fake.trade._request.assert_not_called()
                fake.submit_mock.assert_not_called()

    def test_existing_target_and_unknown_positions_are_untouchable(self):
        for scenario in [dict(existing_position=True),dict(existing_pending=True),dict(existing_algo=True),dict(late_position=True),
                         *[dict(bad_baseline=x) for x in ('NaN','Infinity','--1')]]:
            with self.subTest(scenario=scenario):
                fake = Offline(**scenario)
                self.assertEqual(fake.run()['status'], 'failed')
                fake.submit_mock.assert_not_called()
                fake.trade.fast_close_confirmed.assert_not_called()
                self.assertEqual(fake.cleanup_posts, [])

    def test_cost_risk_minimum_lot_stale_quote_and_zero_balance_block_entry(self):
        for scenario in [dict(minimum='1'),dict(available=0),dict(leverage='10'),dict(quote_ts='1')]:
            with self.subTest(scenario=scenario):
                fake = Offline(**scenario)
                self.assertEqual(fake.run()['status'], 'failed')
                fake.submit_mock.assert_not_called()
                self.assertFalse(fake.closed)

    def test_success_uses_policy_protected_rest_and_native_close_preserves_other_position(self):
        fake = Offline()
        report = fake.run()
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(report['strategy_signal'])
        fake.plan.assert_called_once()
        self.assertEqual(fake.plan.call_args.kwargs['budget_usdt'], 2)
        fake.submit_mock.assert_called_once()
        body = fake.submit_mock.call_args.args[0]
        self.assertEqual(set(body), {'instId','tdMode','side','posSide','clOrdId','ordType','px','sz','attachAlgoOrds'})
        self.assertEqual(body['instId'], INST)
        self.assertEqual(body['tdMode'], 'cross')
        self.assertEqual(body['side'], 'buy')
        self.assertEqual(body['posSide'], 'long')
        self.assertEqual(body['ordType'], 'limit')
        self.assertEqual(body['px'], '100.05')
        self.assertEqual(body['sz'], '0.19')
        self.assertEqual(body['attachAlgoOrds'], [{'attachAlgoClOrdId':body['clOrdId']+'p',
            'tpTriggerPx':'104.06','tpOrdPx':'-1','slTriggerPx':'99.04','slOrdPx':'-1'}])
        self.assertLessEqual(len(body['clOrdId']+'p'), 32)
        self.assertEqual(report['execution_transport'], 'native_rest')
        self.assertFalse(report['cli_validation'])
        self.assertFalse(report['production_entry_gateway'])
        self.assertEqual(fake.posts[0], ('/api/v5/trade/order', body))
        started=next(step for step in report['steps'] if step['step']=='submission_started')
        self.assertEqual(started['attachment_client_id'],body['clOrdId']+'p')
        fake.trade.fast_close_confirmed.assert_called_once()
        fake.runtime.unfreeze_environment.assert_called_once()
        self.assertEqual(fake.other, fake.other_before)
        self.assertIn('existing_positions_preserved', [s['step'] for s in report['steps']])

    def test_rest_unknown_transport_is_never_resent(self):
        for scenario in [dict(post_timeout=True),dict(post_error=True)]:
            with self.subTest(scenario=scenario):
                fake = Offline(**scenario)
                self.assertEqual(fake.run()['status'], 'passed')  # Reads prove actual fill and close.
                fake.submit_mock.assert_called_once()
                fake.trade.fast_close_confirmed.assert_called_once()
        fake = Offline(unknown_order=True)
        self.assertEqual(fake.run()['status'], 'cleanup_required')
        fake.submit_mock.assert_called_once()
        fake.trade.fast_close_confirmed.assert_not_called()
        self.assertEqual(fake.cleanup_posts, [])
        self.assertIn('reconciliation_read_failed', [s['step'] for s in fake.report['steps']])

    def test_unknown_post_with_unknown_order_performs_only_get_reconciliation(self):
        fake = Offline(post_timeout=True,unknown_order=True)
        report = fake.run()
        self.assertEqual(report['status'], 'cleanup_required')
        fake.submit_mock.assert_called_once()
        self.assertEqual(len(fake.posts), 1)
        submitted=next(i for i,request in enumerate(fake.requests) if request[:2]==('POST','/api/v5/trade/order'))
        after=fake.requests[submitted+1:]
        self.assertTrue(after)
        self.assertTrue(all(method=='GET' for method,_,_ in after))
        client_id=fake.posts[0][1]['clOrdId']
        reconciliations=[body for method,path,body in after if path=='/api/v5/trade/order']
        self.assertTrue(reconciliations)
        self.assertTrue(all(body=={'instId':INST,'clOrdId':client_id} for body in reconciliations))
        fake.trade._create_intent.assert_not_called()
        fake.trade.fast_close_confirmed.assert_not_called()
        self.assertIn('submission_unknown',[step['step'] for step in report['steps']])

    def test_marker_mismatch_blocks_amendment_and_cleanup(self):
        for scenario in (dict(algo_marker='foreign'),dict(algo_marker=''),
                         dict(bad_order={'attachAlgoOrds':[{'attachAlgoClOrdId':'foreign'}]})):
            with self.subTest(scenario=scenario):
                fake = Offline(**scenario)
                self.assertEqual(fake.run()['status'], 'cleanup_required')
                fake.submit_mock.assert_called_once()
                self.assertEqual(fake.cleanup_posts, [])
                fake.trade.fast_close_confirmed.assert_not_called()

    def test_failed_protection_and_interrupt_still_cleanup_exact_own_fill(self):
        for scenario in [dict(no_oco=True),dict(interrupt=True),dict(amend_timeout=True)]:
            with self.subTest(scenario=scenario):
                fake = Offline(**scenario)
                self.assertEqual(fake.run()['status'], 'failed')
                fake.trade.fast_close_confirmed.assert_called_once()
                self.assertEqual(fake.other, fake.other_before)
                self.assertLessEqual(len(fake.cleanup_posts), 1)

    def test_partial_fill_cancels_only_own_order_then_closes_only_partial_size(self):
        fake = Offline(order_state='partially_filled')
        self.assertEqual(fake.run()['status'], 'failed')
        self.assertEqual(fake.cleanup_posts, [('/api/v5/trade/cancel-order',{'instId':INST,'ordId':'own-order'})])
        self.assertEqual(fake.trade._create_intent.call_args.args[1]['pos'], '0.1')
        fake.trade.fast_close_confirmed.assert_called_once()
        self.assertEqual(fake.other, fake.other_before)

    def test_cleanup_refuses_wrong_order_id_symbol_side_size_and_nonfinite_fill(self):
        for changes in [dict(clOrdId='external'),dict(instId=OTHER),dict(side='sell'),dict(posSide='short'),
                        dict(sz='999'),dict(accFillSz='NaN'),dict(accFillSz='Infinity'),dict(state='unknown')]:
            with self.subTest(changes=changes):
                fake = Offline(bad_order=changes)
                self.assertEqual(fake.run()['status'], 'cleanup_required')
                fake.trade.fast_close_confirmed.assert_not_called()
                self.assertEqual(fake.cleanup_posts, [])
        for changes in [dict(pos='NaN'),dict(pos='0.2'),dict(posSide='short'),dict(mgnMode='isolated'),dict(posId='')]:
            with self.subTest(changes=changes):
                fake = Offline(bad_position=changes)
                self.assertEqual(fake.run()['status'], 'cleanup_required')
                fake.trade.fast_close_confirmed.assert_not_called()
                self.assertEqual(fake.cleanup_posts, [])

    def test_external_pending_algorithms_or_environment_change_block_native_cleanup(self):
        for scenario in [dict(external_pending=True),dict(external_algo=True),dict(changed=True)]:
            with self.subTest(scenario=scenario):
                fake = Offline(**scenario)
                self.assertEqual(fake.run()['status'], 'cleanup_required')
                fake.trade.fast_close_confirmed.assert_not_called()
                self.assertFalse(any(path.endswith('cancel-order') for path,_ in fake.posts))
                self.assertEqual(fake.other, fake.other_before)

    def test_unknown_cancel_and_close_are_recorded_and_never_retried(self):
        for scenario in [dict(order_state='partially_filled',cancel_timeout=True),dict(close_timeout=True),dict(close_unconfirmed=True)]:
            with self.subTest(scenario=scenario):
                fake = Offline(**scenario)
                report = fake.run()
                self.assertEqual(report['status'], 'cleanup_required')
                self.assertIn('cleanup_failure',[s['step'] for s in report['steps']])
                self.assertLessEqual(fake.trade.fast_close_confirmed.call_count, 1)
                self.assertLessEqual(sum(path.endswith('cancel-order') for path,_ in fake.posts), 1)
                fake.runtime.unfreeze_environment.assert_called_once()

    def test_unwritable_initial_report_blocks_submission(self):
        fake = Offline(early_disk_failure=True)
        report = fake.run()
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['report_errors'])
        fake.submit_mock.assert_not_called()
        fake.trade.fast_close_confirmed.assert_not_called()
        fake.runtime.unfreeze_environment.assert_called_once()

    def test_report_disk_failure_does_not_skip_cleanup_or_release(self):
        fake = Offline(disk_failure=True)
        report = fake.run()
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['report_errors'])
        fake.trade.fast_close_confirmed.assert_called_once()
        fake.runtime.unfreeze_environment.assert_called_once()

    def test_lock_failure_is_logged_and_frozen_environment_released_without_io(self):
        fake = Offline(lock_failure=True)
        self.assertEqual(fake.run()['status'], 'failed')
        fake.trade._request.assert_not_called()
        fake.submit_mock.assert_not_called()
        fake.runtime.unfreeze_environment.assert_called_once()

    def test_invalid_current_mark_never_reaches_amendment(self):
        fake = Offline(bad_position={'markPx':'NaN'})
        self.assertEqual(fake.run()['status'], 'failed')
        self.assertFalse(any(path.endswith('amend-algos') for path,_ in fake.posts))
        fake.trade.fast_close_confirmed.assert_called_once()

    def test_replaced_same_size_position_is_not_closed(self):
        fake = Offline(position_replaced=True)
        self.assertEqual(fake.run()['status'], 'cleanup_required')
        fake.trade.fast_close_confirmed.assert_not_called()
        self.assertEqual(fake.target[0]['posId'], 'external-replacement')

    def test_already_flat_cannot_claim_native_close_was_tested(self):
        fake = Offline(already_flat=True)
        report = fake.run()
        self.assertEqual(report['status'], 'failed')
        fake.trade.fast_close_confirmed.assert_not_called()
        self.assertIn('already_flat_native_close_not_exercised', [s['step'] for s in report['steps']])

    def test_external_position_changes_are_reported_never_repaired(self):
        fake = Offline(other_changed=True)
        report = fake.run()
        self.assertEqual(report['status'], 'failed')
        self.assertIn('baseline_verification_failed',[s['step'] for s in report['steps']])
        self.assertEqual(fake.other['pos'], '0.6')
        self.assertTrue(all(params['instId']==INST for _,params in fake.posts))


if __name__ == '__main__':
    unittest.main()
