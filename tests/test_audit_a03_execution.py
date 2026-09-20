"""A03 offline regressions: one dispatch, durable recovery, shared writer gate."""
import fcntl
import hashlib
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import entry_gateway as gateway, entry_reconciliation as reconciliation
from scripts import strategy_evidence as evidence, trade_lock, initial_protection, algo_reader
from scripts.entry_diagnostics import submission_diagnostics
from scripts.risk_policy import RiskRejected
from okxquant_backend.okx_trade_service import OKXAPIError
import ai_factor_trader as trader


class JournalCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(evidence, 'DB_PATH', Path(self.tmp.name)/'e.db'))
        self.stack.enter_context(patch.object(trade_lock, 'PATH', Path(self.tmp.name)/'writer.lock'))
        self.env = SimpleNamespace(identity='audit-demo', mode='demo', simulated=False, configured=True)
        self.inst = 'BTC-USDT-SWAP'
        self.plan = {'instId':self.inst, 'side':'long', 'size':1, 'entry':100, 'stop':90, 'take_profit':120}
        self.cid = evidence.begin_intent(self.env.identity, 'decision-1', self.inst, self.plan)
        with evidence.connection() as db:
            db.execute('UPDATE intents SET at=? WHERE id=?', (time.time()-300, self.cid))

    def state(self, cid=None):
        with evidence.connection() as db:
            return db.execute('SELECT state FROM intents WHERE id=?', (cid or self.cid,)).fetchone()[0]

    def order(self, **kw):
        return {'instId':self.inst, 'clOrdId':self.cid, 'ordId':'123', 'state':'filled', **kw}


class RecoveryTests(JournalCase):
    def reconcile(self, request):
        with patch.object(gateway, '_request', side_effect=request):
            gateway.reconcile_intents(self.env)

    def test_live_intent_is_revisited_until_terminal(self):
        self.reconcile(lambda *a, **k: [self.order(state='partially_filled')])
        self.assertEqual(self.state(), 'pending')
        reader = Mock(return_value=[self.order(state='filled')])
        self.reconcile(reader)
        self.assertEqual(self.state(), 'filled')
        reader.assert_called_once()

    def test_pending_acceptance_cannot_be_erased_by_empty_history(self):
        evidence.finish_intent(self.cid, 'pending', self.order(state='live'))
        with self.assertRaises(RiskRejected):
            self.reconcile(lambda *a, **k: [])
        self.assertEqual(self.state(), 'pending')

    def test_exact_fill_order_id_recovers_51603_without_write(self):
        calls = []
        def request(method, path, params, env, **kw):
            calls.append((method, path, params))
            self.assertEqual(method, 'GET')
            if path.endswith('/order'):
                if 'clOrdId' in params:
                    raise OKXAPIError('51603', 'missing by client ID')
                self.assertEqual(params['ordId'], '123')
                return [self.order()]
            if path.endswith('/fills-history'):
                return [self.order(billId='1')]
            return []
        self.reconcile(request)
        self.assertEqual(self.state(), 'filled')
        self.assertEqual(len([c for c in calls if c[1].endswith('/order')]), 2)

    def test_failed_first_intent_does_not_starve_later_recovery(self):
        second = evidence.begin_intent(self.env.identity, 'decision-2', self.inst, self.plan)
        def request(method, path, params, env, **kw):
            if params.get('clOrdId') == self.cid:
                raise TimeoutError('private-text-must-not-be-journaled')
            return [self.order(clOrdId=second)]
        with self.assertRaises(RiskRejected):
            self.reconcile(request)
        self.assertEqual(self.state(), 'unknown')
        self.assertEqual(self.state(second), 'filled')
        self.assertNotIn('private-text-must-not-be-journaled', str(evidence.export_events(self.env.identity)))

    def test_acknowledged_exchange_id_recovers_missing_client_id_lookup(self):
        evidence.finish_intent(self.cid, 'acknowledged', {'order_id':'123', 'size':1})
        calls=[]
        def request(method, path, params, env, **kw):
            calls.append(params)
            self.assertEqual(method, 'GET')
            self.assertTrue(path.endswith('/order'))
            if 'clOrdId' in params: raise OKXAPIError('51603', 'missing')
            self.assertEqual(params['ordId'], '123')
            return [self.order()]
        self.reconcile(request)
        self.assertEqual(self.state(), 'filled')
        self.assertEqual(len(calls), 2)

    def test_acknowledged_id_does_not_erase_transient_client_lookup_failure(self):
        evidence.finish_intent(self.cid, 'acknowledged', {'order_id':'123'})
        reader=Mock(side_effect=TimeoutError('unknown'))
        with self.assertRaises(RiskRejected): self.reconcile(reader)
        reader.assert_called_once()
        self.assertEqual(self.state(), 'acknowledged')

    def test_conflicting_journal_order_ids_do_not_choose_one(self):
        evidence.finish_intent(self.cid, 'acknowledged', {'order_id':'123'})
        evidence.finish_intent(self.cid, 'pending', self.order(ordId='456', state='live'))
        reader=Mock(return_value=[self.order()])
        with self.assertRaises(RiskRejected): self.reconcile(reader)
        self.assertEqual(self.state(), 'pending')

    def test_over_twenty_intents_make_bounded_recovery_progress(self):
        for i in range(20):
            evidence.begin_intent(self.env.identity, 'backlog-'+str(i), self.inst, self.plan)
        with evidence.connection() as db:
            db.execute('UPDATE intents SET at=?', (time.time()-300,))
        calls=[]
        def request(method, path, params, env, **kw):
            self.assertEqual(method, 'GET')
            calls.append(path)
            if path.endswith('/order'): raise OKXAPIError('51603', 'missing')
            return []
        with self.assertRaises(RiskRejected): self.reconcile(request)
        with evidence.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM intents WHERE state='not_found'").fetchone()[0], 20)
        self.assertEqual(calls.count('/api/v5/trade/order'), 20)
        self.reconcile(request)
        self.assertEqual(calls.count('/api/v5/trade/order'), 21)
        self.assertEqual(reconciliation.recoverable_intents(self.env.identity), [])

    def test_fill_lookup_mismatch_retains_intent(self):
        def request(method, path, params, env, **kw):
            self.assertEqual(method, 'GET')
            if path.endswith('/order'):
                if 'clOrdId' in params: raise OKXAPIError('51603', 'missing')
                return [self.order(clOrdId='someone-else')]
            return [self.order()] if path.endswith('/fills-history') else []
        with self.assertRaises(RiskRejected): self.reconcile(request)
        self.assertEqual(self.state(), 'unknown')


class SubmissionTests(JournalCase):
    def submit(self, result=None, *, error=None, finish_error=None):
        with ExitStack() as stack:
            stack.enter_context(patch.object(trader.market, '_selected', return_value=self.env))
            stack.enter_context(patch.object(trader.support, 'opening_status', return_value={'can_open':True}))
            stack.enter_context(patch.object(trader.entry_gateway, 'prepare', return_value=(self.plan, self.cid)))
            stack.enter_context(patch.object(trader, 'save_horizon_intent'))
            stack.enter_context(patch.object(trader, 'okx_private_command', side_effect=lambda x:x))
            wire=stack.enter_context(patch.object(trader, 'run_cmd_result', return_value=result, side_effect=error))
            if finish_error:
                stack.enter_context(patch.object(evidence, 'finish_intent', side_effect=finish_error))
            outcome=trader.submit_protected_limit_order(self.inst, 'buy', 'long', 1, 100, 120, 90)
        wire.assert_called_once()
        return outcome, wire.call_args.args[0]

    def test_business_rejection_with_order_id_is_not_acknowledged(self):
        ok, _ = self.submit({'ok':True, 'data':{'code':'0', 'data':[{'ordId':'123', 'sCode':'51000'}]}})[0]
        self.assertFalse(ok)
        self.assertEqual(self.state(), 'unknown')

    def test_wrong_client_or_instrument_is_not_acknowledged(self):
        for extra in ({'clOrdId':'another-client'}, {'instId':'ETH-USDT-SWAP'}):
            with self.subTest(extra=extra):
                outcome, _=self.submit({'ok':True, 'data':[{'ordId':'123', **extra}]})
                self.assertFalse(outcome[0])
                self.assertEqual(self.state(), 'unknown')

    def test_envelope_failure_or_multiple_rows_is_not_acknowledged(self):
        for payload in ({'code':'1','data':[{'ordId':'123','sCode':'0'}]},
                        [{'ordId':'123'}, {'ordId':'456'}]):
            with self.subTest(payload=payload):
                self.assertFalse(self.submit({'ok':True, 'data':payload})[0][0])
                self.assertEqual(self.state(), 'unknown')

    def test_success_preserves_exact_protected_payload(self):
        outcome, command=self.submit({'ok':True, 'data':{'code':'0','data':[self.order(sCode='0')]}})
        self.assertEqual(outcome, (True, '123'))
        self.assertEqual(self.state(), 'acknowledged')
        for part in ('--clOrdId '+self.cid, '--sz 1', '--tpTriggerPx 120', '--slTriggerPx 90', '--ordType limit'):
            self.assertIn(part, command)

    def test_transport_exception_stays_uncertain_without_replay(self):
        outcome, _=self.submit(error=TimeoutError('private-transport-text'))
        self.assertFalse(outcome[0])
        self.assertEqual(self.state(), 'unknown')
        self.assertNotIn('private-transport-text', str(evidence.export_events(self.env.identity)))

    def test_ack_journal_failure_does_not_escape_or_resend(self):
        outcome, _=self.submit({'ok':True, 'data':[self.order()]}, finish_error=OSError('disk unavailable'))
        self.assertFalse(outcome[0])
        self.assertEqual(self.state(), 'unknown')
        events=evidence.export_events(self.env.identity, 'entry_submission')
        self.assertEqual(events[-1]['payload']['response'][0]['ordId'], '123')

    def test_list_business_error_code_survives_diagnostics(self):
        result=submission_diagnostics({'ok':True, 'data':[{'sCode':'51000','ordId':'123'}]}, self.env)
        self.assertEqual(result['exchange_code'], '51000')


class LockTests(unittest.TestCase):
    def test_scoped_writer_contends_with_deployed_default_lock(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(trade_lock, 'PATH', Path(tmp)/'writer.lock'):
            legacy=trade_lock.PATH.with_name(trade_lock.PATH.name+'.'+hashlib.sha256(b'||').hexdigest()[:20])
            with trade_lock.writer(account='same-account', inst_id='BTC-USDT-SWAP', side='long'):
                with legacy.open('a+') as other:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_nested_scope_is_reentrant_and_inference_reacquires_same_gate(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(trade_lock, 'PATH', Path(tmp)/'writer.lock'):
            with trade_lock.writer():
                handle=trade_lock._local.handle
                with trade_lock.writer(account='a', inst_id='ETH-USDT-SWAP'):
                    self.assertIs(trade_lock._local.handle, handle)
                with trade_lock.inference_window():
                    with trade_lock.writer(account='a'):
                        pass
                self.assertIs(trade_lock._local.handle, handle)
                self.assertEqual(trade_lock._local.depth, 1)
            self.assertEqual(trade_lock._local.depth, 0)


class InitialProtectionTests(unittest.TestCase):
    def test_nonfinite_or_missing_position_size_is_not_flat(self):
        env=SimpleNamespace(identity='audit-demo', configured=False)
        position={'instId':'BTC-USDT-SWAP','posSide':'long','pos':'1','posId':'p','cTime':'1'}
        for value in ('NaN', 'Infinity', None):
            with self.subTest(value=value), patch.object(evidence, 'best_effort'):
                current={**position, 'pos':value}
                result=initial_protection.verify(env, position['instId'], 'long', 1, position,
                    Mock(return_value=(True,[current],'')), read_orders=Mock(return_value=[]))
                self.assertEqual(result['status'], 'unverified')


class AlgoSnapshotTests(unittest.TestCase):
    def pages(self, changed):
        first=[{'algoId':str(i), 'instId':'BTC-USDT-SWAP', 'state':'live', 'sz':'1'} for i in range(100)]
        return [first, [{**first[0], **changed}]]

    def fetch(self, pages):
        with patch.object(algo_reader, '_reserve'), patch.object(algo_reader, '_read_finished'), \
             patch.object(algo_reader, '_signed_page', side_effect=pages):
            return algo_reader._fetch_all(SimpleNamespace(configured=True), time.monotonic()+10, 'risk')[0]

    def test_conflicting_duplicate_algo_is_not_silently_green(self):
        with self.assertRaises(algo_reader._WireError) as error:
            self.fetch(self.pages({'state':'effective', 'actualSz':'1'}))
        self.assertEqual(error.exception.category, 'snapshot_changed_during_read')
        self.assertTrue(error.exception.retryable)

    def test_identical_overlapping_page_remains_deduplicated(self):
        self.assertEqual(len(self.fetch(self.pages({}))), 100)


if __name__ == '__main__': unittest.main()
