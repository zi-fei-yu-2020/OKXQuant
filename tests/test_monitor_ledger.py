import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from okxquant_backend.macro_status import project
from scripts import ledger_monitor as monitor
from scripts.close_attribution import reason


class MacroTests(unittest.TestCase):
    def test_cache_actual_time(self):
        result = project({'SOL': {'macro_assessment': 'evidence', 'timestamp': 1000}}, [], now=1100)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['age_seconds'], 100)
        self.assertEqual(result['source'], 'decision_cache')

    def test_history_and_stale(self):
        result = project({}, [{'macro_assessment': 'old', 'time': 1000}], now=4000)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['text'], 'old')

    def test_new_failure_keeps_previous(self):
        result = project({'SOL': {'macro_assessment': 'old', 'timestamp': 1000}}, [],
                         validation={'status': 'unavailable', 'reason': '503'}, validation_at=1050, now=1100)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['text'], 'old')

    def test_pending_and_future(self):
        result = project({'SOL': {'macro_assessment': 'future', 'timestamp': 9999}}, [],
                         validation={'status': 'pending'}, validation_at=1050, now=1100)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['text'], '')


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.history = dict(instId='SOL-USDT-SWAP', direction='long', uTime='1788688602696', closeTotalPos='5')
        self.order = dict(instId='SOL-USDT-SWAP', posSide='long', side='sell', state='filled',
                          fillTime='1788688602695', accFillSz='5', ordId='3898562037694484480',
                          clOrdId='okxquantclose1788688602', algoId='', reduceOnly=True)

    def test_exact_sol_manual_receipt(self):
        result = reason(self.history, [self.order])
        self.assertEqual(result['exit_source'], 'manual_admin')
        self.assertEqual(result['attribution_status'], 'verified')

    def test_pnl_never_proves_source(self):
        for pnl in [-100, 0, 100]:
            self.assertEqual(reason({**self.history, 'pnl': pnl}, [])['exit_source'], 'unknown')

    def test_partial_not_all_manual(self):
        self.assertEqual(reason({**self.history, 'closeTotalPos': '10'}, [self.order])['attribution_status'], 'partial')

    def test_algo_and_unknown(self):
        self.assertEqual(reason(self.history, [{**self.order, 'clOrdId': '', 'algoId': '123'}])['exit_source'], 'exchange_algo')
        self.assertEqual(reason(self.history, [{**self.order, 'clOrdId': ''}])['exit_source'], 'unknown')

    def test_dedup_wrong_direction_and_old_order(self):
        self.assertEqual(reason(self.history, [self.order, self.order])['exit_source'], 'manual_admin')
        self.assertEqual(reason(self.history, [{**self.order, 'side': 'buy'}])['exit_source'], 'unknown')
        self.assertEqual(reason(self.history, [{**self.order, 'fillTime': '100'}])['exit_source'], 'unknown')


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch = patch.object(monitor, 'DATA', Path(self.tmp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.env = SimpleNamespace(identity='okx:demo:demo-test', mode='demo')
        self.target = dict(instId='SOL-USDT-SWAP', posId='123', posSide='long', cTime='1788676101000')
        self.row = dict(id='holding_SOL', instId='SOL-USDT-SWAP', pos_id='123', side='long',
                        open_time=monitor.bj(self.target['cTime']), status='holding', pnl=3, environment_id='okx:demo:demo-test')

    def test_confirmed_event_projects_without_estimated_pnl(self):
        monitor.note_confirmed_close(self.env, self.target, [], 'okxquantclose1788688602')
        result = monitor.project_rows([self.row], self.env.identity)[0]
        self.assertEqual(result['status'], 'closed_pending')
        self.assertEqual(result['exit_source'], 'manual_admin')
        self.assertIsNone(result['pnl'])
        self.assertIsNone(result['close_px'])
        self.assertEqual(self.row['status'], 'holding')

    def test_scope_and_lifecycle_mismatch(self):
        monitor.note_confirmed_close(self.env, self.target, [], 'okxquantclose1788688602')
        for changes in ({'environment_id': 'live-test'}, {'pos_id': 'other'}, {'open_time': 'other'}):
            result = monitor.project_rows([{**self.row, **changes}], self.env.identity)[0]
            self.assertEqual(result['status'], 'holding')

    def test_absent_requires_scoped_snapshot(self):
        self.assertEqual(monitor.project_rows([self.row], self.env.identity, positions=[])[0]['status'], 'closed_pending')
        legacy = {**self.row, 'environment_id': None}
        self.assertEqual(monitor.project_rows([legacy], self.env.identity, positions=[])[0]['status'], 'holding')

    def test_refresh_and_bounded_error_backoff(self):
        monitor.atomic('ledger_refresh_request.json', {'id': 'new', 'at': 100})
        self.assertFalse(monitor.should_run(100, 104))
        self.assertTrue(monitor.should_run(100, 105))
        monitor.atomic('ledger_sync_status.json', {'status': 'error', 'last_error_at': 106})
        self.assertFalse(monitor.should_run(105, 115))
        self.assertTrue(monitor.should_run(105, 165))

    def test_pending_fast_poll_expires(self):
        monitor.atomic('ledger_sync_status.json', {'pending_settlements': 1, 'pending_since': 100})
        self.assertTrue(monitor.should_run(100, 110))
        self.assertFalse(monitor.should_run(220, 230))
        self.assertTrue(monitor.should_run(220, 280))

    def test_worker_no_notify_and_failed_sync_preserves_ledger(self):
        from scripts import sync_full_ledger
        monitor.atomic('trading_ledger.json', [self.row])
        with patch('scripts.okx_runtime.selected_environment', return_value=self.env), patch.object(sync_full_ledger, 'build_lifecycle_ledger', return_value=[]) as sync:
            self.assertEqual(monitor.sync_once()['status'], 'ok')
            sync.assert_called_once_with(notify=False)
        with patch('scripts.okx_runtime.selected_environment', return_value=self.env), patch.object(sync_full_ledger, 'build_lifecycle_ledger', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): monitor.sync_once()
        self.assertEqual(monitor.load('trading_ledger.json', []), [self.row])
        self.assertEqual(monitor.load('ledger_sync_status.json', {})['status'], 'error')

    def test_native_snapshot_is_get_only_and_validated(self):
        from scripts.sync_full_ledger import read_snapshot
        env = SimpleNamespace(configured=True)
        with patch('okxquant_backend.okx_trade_service._request', return_value=[]) as read:
            self.assertEqual(read_snapshot(env, '/positions', 'unused', {}), [])
            read.assert_called_once_with('GET', '/positions', {}, env, timeout=8)
        with patch('okxquant_backend.okx_trade_service._request', return_value={'error': 503}):
            with self.assertRaises(RuntimeError): read_snapshot(env, '/positions', 'unused', {})

    def test_sol_lifecycle_accounting_and_notification_survive_worker(self):
        from scripts import sync_full_ledger as ledger
        from contextlib import ExitStack
        root = Path(self.tmp.name)
        history = dict(instId='SOL-USDT-SWAP', direction='long', posId='123', cTime='1788676101000',
                       uTime='1788688602696', closeTotalPos='5', openAvgPx='105.7', closeAvgPx='106.34',
                       pnl='3.2', fee='-0.37155', fundingFee='-0.05259', realizedPnl='2.77586', lever='3')
        order = dict(instId='SOL-USDT-SWAP', posSide='long', side='sell', state='filled',
                     fillTime='1788688602695', accFillSz='5', ordId='3898562037694484480', clOrdId='okxquantclose1788688602')
        with ExitStack() as stack:
            for name, value in [('DATA_DIR', str(root)), ('LEDGER_JSON_FILE', str(root/'trading_ledger.json')),
                                ('INITIAL_STATE_FILE', str(root/'initial.json')), ('POSITION_TRACKER_FILE', str(root/'tracker.json'))]:
                stack.enter_context(patch.object(ledger, name, value))
            stack.enter_context(patch.object(ledger, 'selected_environment', return_value=self.env))
            stack.enter_context(patch.object(ledger, 'TARGET_INSTRUMENTS', [{'name':'SOL','instId':'SOL-USDT-SWAP','ctVal':1}]))
            stack.enter_context(patch('scripts.strategy_evidence.best_effort'))
            read = stack.enter_context(patch.object(ledger, 'read_snapshot', side_effect=[[history], [], [order]]))
            notify = stack.enter_context(patch('qq_notifier.notify_trade_close'))
            rows = ledger.build_lifecycle_ledger(notify=False)
            self.assertEqual(rows[0]['net_pnl'], 2.77586)
            self.assertEqual(round(rows[0]['net_pnl'], 2), 2.78)
            self.assertEqual(rows[0]['exit_source'], 'manual_admin')
            self.assertEqual(rows[0]['close_notification_status'], 'pending')
            notify.assert_not_called()
            read.side_effect = [[history], [], [order]]
            ledger.build_lifecycle_ledger()
            notify.assert_called_once()
            read.side_effect = [[history], [], []]
            final = ledger.build_lifecycle_ledger()
            self.assertEqual(final[0]['exit_source'], 'manual_admin')
            notify.assert_called_once()
            before = (root/'trading_ledger.json').read_bytes()
            read.side_effect = RuntimeError('503')
            with self.assertRaises(RuntimeError): ledger.build_lifecycle_ledger(notify=False)
            self.assertEqual((root/'trading_ledger.json').read_bytes(), before)
