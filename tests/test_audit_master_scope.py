from datetime import datetime,timezone,timedelta
import unittest
from scripts.risk_policy import Policy,ledger_daily_drawdown

class ScopedDailyLossAudit(unittest.TestCase):
    def test_foreign_demo_losses_do_not_block_live(self):
        rows=[{'environment_id':'demo-a','status':'closed','close_time':'2026-09-19 10:00:00','net_pnl':-500},
              {'environment_id':'live-a','status':'closed','close_time':'2026-09-19 10:00:00','net_pnl':-1},
              {'status':'closed','close_time':'2026-09-19 10:00:00','net_pnl':-500}]
        r=ledger_daily_drawdown(Policy(),now=datetime(2026,9,19,tzinfo=timezone(timedelta(hours=8))),rows=rows,initial_capital=300,reset_time='2026-09-19 00:00:00',scope='live-a')
        self.assertFalse(r['blocked']);self.assertEqual(r['net_pnl'],-1)

    def test_conflicting_account_markers_never_count_as_current(self):
        r=ledger_daily_drawdown(Policy(),now=datetime(2026,9,19),rows=[{'environment_id':'live-a','account_source_id':'demo-a','status':'closed','close_time':'2026-09-19 10:00:00','net_pnl':-100}],initial_capital=300,reset_time='2026-09-19 00:00:00',scope='live-a')
        self.assertEqual(r['net_pnl'],0)

    def test_current_account_loss_still_triggers_existing_threshold(self):
        r=ledger_daily_drawdown(Policy(daily_drawdown_pct=.08),now=datetime(2026,9,19),rows=[{'environment_id':'live-a','status':'closed','close_time':'2026-09-19 10:00:00','net_pnl':-25}],initial_capital=300,reset_time='2026-09-19 00:00:00',scope='live-a')
        self.assertTrue(r['blocked']);self.assertEqual(r['threshold'],.08)
