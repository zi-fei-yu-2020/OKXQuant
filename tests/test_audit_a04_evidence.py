"""Read-only fill pagination, restart and projection accounting regressions."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts import evidence_sync, strategy_evidence as evidence, horizon_stats, db_manager

class FillSyncTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(evidence, 'DB_PATH', self.root/'e.db'))
        self.stack.enter_context(patch.object(evidence_sync, 'ROOT', self.root))
        self.env = SimpleNamespace(identity='audit')

    def page(self, count, offset=0):
        return [dict(billId=str(offset+i+1), ordId='order', instId='BTC-USDT-SWAP',
                     ts=str((2000-offset-i)*1000)) for i in range(count)]

    def state(self):
        with evidence.connection() as db:
            row = db.execute('SELECT payload FROM sync_state WHERE scope=?', ('audit',)).fetchone()
        return json.loads(row[0]) if row else None

    def test_restart_resumes_cursor_and_repeated_page_is_idempotent(self):
        page = self.page(100)
        with patch.object(evidence_sync, '_request', return_value=page):
            self.assertFalse(evidence_sync.collect_fills(self.env, max_pages=1)['complete'])
        cursor = self.state()['cursor']
        with patch.object(evidence_sync, '_request', return_value=page[-1:]+self.page(1, 100)) as read:
            self.assertTrue(evidence_sync.collect_fills(self.env)['complete'])
        self.assertEqual(read.call_args.args[2]['after'], cursor)
        self.assertEqual(len(evidence.export_events('audit', 'fill')), 101)
        self.assertNotIn('cursor', self.state())

    def test_archive_failure_never_advances_cursor_or_discards_prior_page(self):
        with patch.object(evidence_sync, '_request', return_value=self.page(100)):
            evidence_sync.collect_fills(self.env, max_pages=1)
        before = self.state()
        with patch.object(evidence_sync, '_request', return_value=self.page(1, 100)), patch.object(evidence, 'append_batch', side_effect=sqlite3.OperationalError('offline failure')):
            with self.assertRaises(sqlite3.OperationalError):
                evidence_sync.collect_fills(self.env)
        self.assertEqual(self.state(), before)
        self.assertEqual(len(evidence.export_events('audit', 'fill')), 100)

class StatisticsTests(unittest.TestCase):
    def test_missing_fees_and_gross_are_unknown_not_free_trades(self):
        stats = horizon_stats.rebuild([
            {'status': 'closed', 'net_pnl': 2, 'fee': -.1, 'gross_pnl': 2.1},
            {'status': 'closed', 'net_pnl': 1, 'fee': None, 'gross_pnl': None},
        ])['unknown']
        self.assertEqual(stats['net_pnl'], 3)
        self.assertIsNone(stats['fees'])
        self.assertIsNone(stats['gross_pnl'])
        self.assertEqual(stats['fees_observed'], -.1)
        self.assertEqual(stats['fees_unknown'], 1)

    def test_scoped_statistics_exclude_other_accounts_without_deleting_rows(self):
        rows = [{'status': 'closed', 'net_pnl': n, 'environment_id': scope}
                for n, scope in ((2, 'mine'), (99, 'other'), (100, None))]
        stats = horizon_stats.rebuild(rows, scope='mine')
        self.assertEqual(stats['unknown']['closed'], 1)
        self.assertEqual(stats['unknown']['net_pnl'], 2)
        self.assertEqual(len(rows), 3)

class MirrorAliasTests(unittest.TestCase):
    def test_failed_insert_rolls_back_reconciled_alias_removal(self):
        with tempfile.TemporaryDirectory() as td, patch.object(db_manager, 'DATA_DIR', td), patch.object(db_manager, 'DB_PATH', str(Path(td)/'mirror.db')):
            db_manager._write_rows([db_manager._mirror_row({'id': 'old', 'pnl': 3})])
            with sqlite3.connect(Path(td)/'mirror.db') as db:
                db.executescript("CREATE TRIGGER fail_new BEFORE INSERT ON trades WHEN NEW.bill_id='new' BEGIN SELECT RAISE(ABORT, 'offline failure'); END;")
            with self.assertRaises(sqlite3.IntegrityError):
                db_manager._write_rows([db_manager._mirror_row({'id': 'new', 'pnl': 4})], superseded=['old'])
            with sqlite3.connect(Path(td)/'mirror.db') as db:
                self.assertEqual(db.execute('SELECT bill_id,pnl FROM trades').fetchall(), [('old', 3)])
