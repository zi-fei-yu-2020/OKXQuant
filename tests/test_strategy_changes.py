import unittest
from datetime import datetime,timezone,timedelta
from scripts.risk_policy import Policy,ledger_daily_drawdown

class StrategyAndCircuitTests(unittest.TestCase):
    def test_daily_ledger_loss_triggers_opening_gate_at_threshold(self):
        rows=[{'status':'closed','close_time':'2026-09-16 10:00:00','net_pnl':-30},
              {'status':'closed','close_time':'2026-09-16 11:00:00','net_pnl':-1},
              {'status':'holding','open_time':'2026-09-16 12:00:00','net_pnl':-100},
              {'status':'closed_pending','close_time':'2026-09-16 12:00:00','net_pnl':-100}]
        result=ledger_daily_drawdown(Policy(daily_drawdown_pct=.03),now=datetime(2026,9,16,12,tzinfo=timezone(timedelta(hours=8))),rows=rows,initial_capital=1000,reset_time='2026-09-16 00:00:00')
        self.assertTrue(result['blocked']);self.assertEqual(result['net_pnl'],-31);self.assertAlmostEqual(result['drawdown'],.031)
    def test_previous_day_and_unsettled_rows_do_not_trigger_gate(self):
        rows=[{'status':'closed','close_time':'2026-09-15 23:59:59','net_pnl':-100},{'status':'holding','open_time':'2026-09-16 01:00:00','net_pnl':-100}]
        result=ledger_daily_drawdown(Policy(daily_drawdown_pct=.03),now=datetime(2026,9,16,2,tzinfo=timezone(timedelta(hours=8))),rows=rows,initial_capital=1000,reset_time='2026-09-16 00:00:00')
        self.assertFalse(result['blocked']);self.assertEqual(result['net_pnl'],0)

if __name__=='__main__':unittest.main()
