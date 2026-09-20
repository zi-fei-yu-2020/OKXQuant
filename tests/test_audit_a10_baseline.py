"""A10 daily briefing uses A07's account-scoped baseline, never its projection."""
from contextlib import ExitStack, redirect_stdout
import datetime
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import daily_summary_and_backup as briefing
from scripts import okx_runtime
from okxquant_backend import account_baseline


class DailyBaselineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.data = self.root/'data'; self.data.mkdir()
        self.baseline = self.data/'account_initial_state.json'
        self.ledger = self.data/'trading_ledger.json'
        self.scope = 'okx:demo:synthetic-a10'
        self.selected = self.stack.enter_context(patch.object(okx_runtime, 'selected_environment', return_value=SimpleNamespace(identity=self.scope)))
        self.stack.enter_context(patch.object(account_baseline, 'BASELINE_FILE', self.baseline))
        self.stack.enter_context(patch.object(briefing, 'WORKSPACE_DIR', str(self.root)))
        self.stack.enter_context(patch.object(briefing, 'DATA_DIR', str(self.data)))
        self.stack.enter_context(patch.object(briefing, 'LEDGER_JSON_FILE', str(self.ledger)))
        self.notify = self.stack.enter_context(patch.object(briefing, 'notify_daily_summary'))
        self.process = self.stack.enter_context(patch.object(briefing.subprocess, 'run'))
        self.stack.enter_context(patch.dict(os.environ, {'INITIAL_CAPITAL':'','INITIAL_CAPITAL_ACCOUNT_SCOPE':''}))
        self.today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d')

    def rows(self, scope=None):
        return [dict(status='closed', environment_id=scope or self.scope, close_time=self.today+' 11:00:00', pnl=100, inst='BEFORE'),
                dict(status='closed', environment_id=scope or self.scope, close_time=self.today+' 13:00:00', pnl=7, inst='AFTER'),
                dict(status='closed', environment_id='okx:live:other-fixture', close_time=self.today+' 13:00:00', pnl=900, inst='OTHER')]

    def run_briefing(self):
        with redirect_stdout(io.StringIO()):
            return briefing.generate_daily_briefing_and_backup()

    def test_v2_uses_explicit_selected_scope_not_top_level_reset(self):
        self.baseline.write_text(json.dumps({'schema_version':2, 'account_scope':'okx:live:other-fixture',
            'initial_capital':9000, 'reset_time':self.today+' 23:59:00',
            'baselines':{self.scope:{'account_scope':self.scope,'initial_capital':300,
                'baseline_configured':True,'reset_time':self.today+' 12:00:00'}}}))
        self.ledger.write_text(json.dumps(self.rows()))
        with patch.object(account_baseline, 'load_account_baseline', wraps=account_baseline.load_account_baseline) as load:
            text = self.run_briefing()
        load.assert_called_once_with(scope=self.scope)
        self.assertIn('300.00 USDT', text)
        self.assertIn('+7.00 USDT', text)
        self.assertNotIn('9000', text)
        self.assertNotIn('OTHER', text)
        self.notify.assert_called_once_with(text)
        self.process.assert_not_called()

    def test_ownerless_legacy_demo_remains_compatible(self):
        self.baseline.write_text(json.dumps({'initial_capital':350,'reset_time':self.today+' 12:00:00'}))
        self.ledger.write_text(json.dumps(self.rows()))
        text = self.run_briefing()
        self.assertIn('350.00 USDT', text)
        self.assertIn('+7.00 USDT', text)

    def test_live_does_not_claim_legacy_capital_or_legacy_reset(self):
        scope = 'okx:live:synthetic-a10'
        self.selected.return_value = SimpleNamespace(identity=scope)
        self.baseline.write_text(json.dumps({'initial_capital':350,'reset_time':self.today+' 12:00:00'}))
        self.ledger.write_text(json.dumps(self.rows(scope)))
        text = self.run_briefing()
        self.assertIn('+107.00 USDT', text)
        self.assertNotIn('350.00', text)
        self.assertNotIn('10000', text)

    def test_display_default_is_not_confirmed_capital(self):
        self.ledger.write_text('[]')
        with patch.object(account_baseline, 'load_account_baseline', return_value={
            'account_scope':self.scope,'initial_capital':10000,'baseline_configured':False,
            'reset_time':self.today+' 12:00:00'}):
            text = self.run_briefing()
        self.assertNotIn('10000', text)

    def test_account_switch_during_report_refuses_notification(self):
        self.ledger.write_text(json.dumps(self.rows()))
        self.selected.side_effect = [SimpleNamespace(identity=self.scope), SimpleNamespace(identity='okx:live:other')]
        with self.assertRaises(RuntimeError):
            self.run_briefing()
        self.notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
