"""Daily-only account drawdown admission contract."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import risk_policy as risk, strategy_evidence as evidence

BEIJING=timezone(timedelta(hours=8))

def ts(value):
    return datetime.fromisoformat(value).replace(tzinfo=BEIJING).timestamp()

class DailyDrawdownGateTests(unittest.TestCase):
    def test_peak_drawdown_is_telemetry_and_daily_gate_resets_at_beijing_midnight(self):
        policy=risk.Policy(daily_drawdown_pct=.03,peak_drawdown_pct=.08)
        before=risk.update_equity_state(None,equity=1000,at=ts('2026-09-22T23:50:00'),cash_flow=0,complete=True,policy=policy)
        blocked=risk.update_equity_state(before,equity=900,at=ts('2026-09-22T23:59:00'),cash_flow=0,complete=True,policy=policy)
        self.assertTrue(blocked['blocked'])
        self.assertAlmostEqual(blocked['daily_drawdown'],.10)
        next_day=risk.update_equity_state(blocked,equity=900,at=ts('2026-09-23T00:01:00'),cash_flow=0,complete=True,policy=policy)
        self.assertFalse(next_day['blocked'])
        self.assertEqual(next_day['daily_drawdown'],0)
        self.assertAlmostEqual(next_day['peak_drawdown'],.10)

    def test_same_timestamp_rollover_retains_peak_but_resets_daily_anchor(self):
        policy=risk.Policy(daily_drawdown_pct=.03,peak_drawdown_pct=.08)
        yesterday={'at':ts('2026-09-22T23:55:00'),'equity':900,'day':'2026-09-22',
                   'day_anchor':1000,'peak':1000,'daily_drawdown':.1,'peak_drawdown':.1,
                   'blocked':True,'external_flow_origin':ts('2026-09-22T20:00:00'),'external_flow_total':0}
        current=risk.refresh_equity_state(yesterday,equity=900,at=yesterday['at'],day='2026-09-23',policy=policy)
        self.assertFalse(current['blocked'])
        self.assertEqual(current['day_anchor'],900)
        self.assertEqual(current['daily_drawdown'],0)
        self.assertAlmostEqual(current['peak_drawdown'],.1)

    def test_early_gate_ignores_legacy_peak_flag_but_respects_current_daily_loss(self):
        import ai_factor_trader as trader
        from scripts import okx_runtime
        with tempfile.TemporaryDirectory() as directory:
            db_path=Path(directory)/'evidence.db'
            state={'day':'2026-09-23','daily_drawdown':0.,'peak_drawdown':.0815,'blocked':True}
            with patch.object(evidence,'DB_PATH',db_path), \
                 patch.object(trader,'check_black_swan_sentinel',return_value=(False,'')), \
                 patch.object(trader,'CIRCUIT_BREAKER_FILE',str(Path(directory)/'missing.json')), \
                 patch.object(risk,'ledger_daily_drawdown',return_value={'blocked':False}), \
                 patch.object(risk,'load_policy',return_value=risk.Policy(daily_drawdown_pct=.03)), \
                 patch.object(risk,'beijing_day',return_value='2026-09-23'), \
                 patch.object(okx_runtime,'selected_environment',return_value=SimpleNamespace(identity='test-account')):
                with evidence.connection() as db:
                    db.execute('INSERT OR REPLACE INTO equity_state VALUES (?,?)',('test-account',evidence.canonical(state)))
                self.assertEqual(trader.is_circuit_breaker_active(),(False,''))
                state['daily_drawdown']=.03
                with evidence.connection() as db:
                    db.execute('INSERT OR REPLACE INTO equity_state VALUES (?,?)',('test-account',evidence.canonical(state)))
                active,reason=trader.is_circuit_breaker_active()
                self.assertTrue(active)
                self.assertIn('3.00%',reason)

    def test_unreadable_ledger_fails_closed_but_missing_anchor_does_not(self):
        import ai_factor_trader as trader
        from scripts import okx_runtime
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(evidence,'DB_PATH',Path(directory)/'evidence.db'), \
             patch.object(trader,'check_black_swan_sentinel',return_value=(False,'')), \
             patch.object(trader,'CIRCUIT_BREAKER_FILE',str(Path(directory)/'missing.json')), \
             patch.object(risk,'load_policy',return_value=risk.Policy()), \
             patch.object(okx_runtime,'selected_environment',return_value=SimpleNamespace(identity='test-account')):
            with patch.object(risk,'ledger_daily_drawdown',return_value={'blocked':False,'reason':'ledger_unavailable'}):
                active,reason=trader.is_circuit_breaker_active()
                self.assertTrue(active)
                self.assertIn('账本读取失败',reason)
            with patch.object(risk,'ledger_daily_drawdown',return_value={'blocked':False,'reason':'equity_day_anchor_unavailable'}):
                self.assertEqual(trader.is_circuit_breaker_active(),(False,''))

    def test_missing_ledger_is_zero_loss_but_corrupt_ledger_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trading_ledger.json'
            def evaluate():
                with patch.object(risk,'settled_ledger_path',return_value=path):
                    return risk.ledger_daily_drawdown(risk.Policy(),initial_capital=1000,
                        reset_time='1970-01-01 00:00:00',scope='a')
            fresh=evaluate()
            self.assertEqual(fresh['reason'],'lifecycle_ledger_daily_loss')
            self.assertEqual(fresh['net_pnl'],0)
            self.assertFalse(fresh['blocked'])
            path.write_text('{not json',encoding='utf-8')
            self.assertEqual(evaluate()['reason'],'ledger_unavailable')

if __name__=='__main__':
    unittest.main()
