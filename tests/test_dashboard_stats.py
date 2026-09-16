import unittest
from scripts.dashboard_stats import today_lifecycle_stats

class DashboardStatsTests(unittest.TestCase):
    def test_counts_lifecycle_rows_once_and_excludes_holdings(self):
        result=today_lifecycle_stats([
            {'status':'closed','close_time':'2026-09-16 09:00:00','net_pnl':2.5,'gross_pnl':3,'fee':-.5},
            {'status':'closed','close_time':'2026-09-16 09:01:00','net_pnl':-4,'gross_pnl':-3,'fee':-1},
            {'status':'holding','open_time':'2026-09-16 09:02:00','net_pnl':99},
            {'status':'closed_pending','close_time':'2026-09-16 09:03:00','net_pnl':-9},
            {'status':'closed','close_time':'2026-09-15 23:59:59','net_pnl':8},
        ],'2026-09-16')
        self.assertEqual(result['closed_trades'],2)
        self.assertEqual((result['win_trades'],result['loss_trades']), (1,1))
        self.assertEqual(result['win_rate'],50.0)
        self.assertEqual(result['net_realized'],-1.5)
        self.assertEqual(result['fees_paid'],-1.5)
        self.assertEqual(result['source'],'lifecycle_ledger')

    def test_reset_time_is_respected(self):
        result=today_lifecycle_stats([{'status':'closed','close_time':'2026-09-16 01:00:00','net_pnl':-1}], '2026-09-16','2026-09-16 02:00:00')
        self.assertEqual(result['closed_trades'],0)

if __name__=='__main__':unittest.main()
