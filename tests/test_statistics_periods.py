import unittest
from datetime import datetime, timezone, timedelta

from scripts.dashboard_stats import canonical_rows, normalize_timestamp, page_trades, scoped_rows, today_lifecycle_stats
from scripts.horizon_stats import rebuild, strategy_periods

SH = timezone(timedelta(hours=8))
SCOPE = "demo:account-1"


def row(identity, *, status="closed", opened="2026-10-07 23:50:00", closed="2026-10-08 00:05:00",
        pnl=1, horizon="scalp", scope=SCOPE, **extra):
    value={"id":identity,"environment_id":scope,"status":status,"open_time":opened,
           "close_time":closed,"net_pnl":pnl,"gross_pnl":pnl,"fee":0,"horizon":horizon}
    value.update(extra)
    return value


class StrategyPeriodsTests(unittest.TestCase):
    def test_shanghai_midnight_close_counts_today_even_when_opened_yesterday(self):
        result=strategy_periods([row("cross")],scope=SCOPE,now=datetime(2026,10,8,0,10,tzinfo=SH))
        today=result["periods"]["today"]["scalp"]
        self.assertEqual(today["closed"],1)
        self.assertEqual(today["net_pnl"],1)
        self.assertEqual(today["opened"],0)
        self.assertEqual(result["periods"]["today"]["coverage"]["start"],"2026-10-08T00:00:00+08:00")
        self.assertEqual(result["periods"]["all"]["scalp"]["opened"],1)

    def test_reset_baseline_does_not_change_today_or_all_periods(self):
        rows=[row("old",opened="2026-01-01 00:00:00",closed="2026-01-01 01:00:00",pnl=-3),row("new")]
        one=strategy_periods(rows,scope=SCOPE,now=datetime(2026,10,8,12,tzinfo=SH))
        two=strategy_periods(rows,scope=SCOPE,now=datetime(2026,10,8,12,tzinfo=SH))
        self.assertEqual(one,two)
        self.assertEqual(one["periods"]["all"]["scalp"]["closed"],2)
        self.assertIn("not exchange lifetime",one["periods"]["all"]["coverage"]["limitations"][0])

    def test_pending_unknown_null_net_and_holding_are_not_fabricated(self):
        rows=[row("pend",status="closed_pending",pnl=None),row("unknown",pnl=None,horizon="mystery"),
              row("hold",status="holding",opened="2026-10-08 09:00:00",closed=None,pnl=999,horizon="mystery")]
        result=strategy_periods(rows,scope=SCOPE,now=datetime(2026,10,8,12,tzinfo=SH))["periods"]["today"]
        self.assertEqual(result["scalp"]["closed"],0)
        self.assertEqual(result["scalp"]["pending_settlements"],1)
        self.assertEqual(result["scalp"]["net_pnl"],0)
        self.assertEqual(result["unknown"]["closed"],0)
        self.assertEqual(result["unknown"]["observed"]["unknown_net_rows"],1)
        self.assertEqual(result["unknown"]["opened"],1)

    def test_unknown_net_is_null_not_a_displayable_zero(self):
        result=strategy_periods([row("unknown-net",pnl=None)],scope=SCOPE,
                                now=datetime(2026,10,8,12,tzinfo=SH))["periods"]["today"]["scalp"]
        self.assertEqual(result["closed"],0)
        self.assertIsNone(result["net_pnl"])
        self.assertEqual(result["observed"]["net_pnl"],0)
        self.assertFalse(result["completeness"]["net_pnl"])

    def test_breakeven_and_partial_financial_completeness(self):
        result=strategy_periods([row("zero",pnl=0),row("partial",pnl=2,fee=None,gross_pnl=None)],
                                scope=SCOPE,now=datetime(2026,10,8,12,tzinfo=SH))["periods"]["today"]["scalp"]
        self.assertEqual((result["closed"],result["breakeven"],result["wins"]),(2,1,1))
        self.assertEqual(result["win_rate"],0.5)
        self.assertFalse(result["completeness"]["fees"])
        self.assertIsNone(result["fees"])
        self.assertEqual(result["observed"]["fees"],0)
        self.assertFalse(result["completeness"]["funding_fee"])
        self.assertIsNone(result["funding_fee"])
        self.assertEqual(result["observed"]["funding_fee"],0)

    def test_missing_close_time_is_unknown_not_a_today_close(self):
        rows=[row("bad-close",opened="2026-10-08 09:00:00",closed=None,pnl=8),
              row("bad-date",opened="2026-10-08 10:00:00",closed="not-a-time",pnl=9),
              row("prior-gap",opened="2026-10-07 09:00:00",closed=None,pnl=10)]
        periods=strategy_periods(rows,scope=SCOPE,now=datetime(2026,10,8,12,tzinfo=SH))["periods"]
        today=periods["today"]
        self.assertEqual(today["scalp"]["closed"],0)
        self.assertEqual(today["unknown_settlement_evidence"],2)
        self.assertEqual(periods["all"]["scalp"]["closed"],0)
        self.assertEqual(periods["all"]["unknown_settlement_evidence"],3)

    def test_all_excludes_future_open_and_close_events(self):
        rows=[row("future-close",closed="2026-10-09 01:00:00",pnl=5),
              row("future-open",opened="2026-10-09 01:00:00",closed=None,status="holding",pnl=50),
              row("past",opened="2026-10-07 01:00:00",closed="2026-10-07 02:00:00",pnl=2)]
        periods=strategy_periods(rows,scope=SCOPE,now=datetime(2026,10,8,12,tzinfo=SH))["periods"]["all"]
        self.assertEqual(periods["scalp"]["closed"],1)
        self.assertEqual(periods["scalp"]["opened"],2)
        self.assertLessEqual(periods["coverage"]["end"],"2026-10-08T12:00:00+08:00")

    def test_missing_scope_fails_closed(self):
        result=strategy_periods([row("mine")],scope=None,now=datetime(2026,10,8,12,tzinfo=SH))
        self.assertEqual(result["periods"]["all"]["scalp"]["closed"],0)

    def test_scope_conflicts_and_unowned_rows_excluded(self):
        rows=[row("mine"),row("other",scope="other"),
              {**row("conflict"),"account_source_id":"other"},
              {k:v for k,v in row("legacy").items() if k!="environment_id"}]
        periods=strategy_periods(rows,scope=SCOPE,now=datetime(2026,10,8,12,tzinfo=SH))
        self.assertEqual(periods["periods"]["all"]["scalp"]["closed"],1)

    def test_lifecycle_ids_deduplicate_and_latest_correction_wins(self):
        rows=[row("same",pnl=2),row("same",pnl=5),row("old",pnl=99),
              row("corrected",pnl=1,superseded_ledger_ids=["old"]),
              row("no-alias-list",pnl=3,superseded_ledger_ids=None)]
        periods=strategy_periods(rows,scope=SCOPE,now=datetime(2026,10,8,12,tzinfo=SH))
        self.assertEqual(periods["periods"]["all"]["scalp"]["closed"],3)
        self.assertEqual(periods["periods"]["all"]["scalp"]["net_pnl"],9)

    def test_scoped_adapter_and_pagination_fail_closed_for_invalid_scope(self):
        rows=[row("owned")]
        for scope in (None,"", "   ", 7):
            self.assertEqual(scoped_rows(rows,scope),[])
            with self.assertRaises(ValueError): page_trades(rows,scope)

    def test_pagination_uses_full_scoped_ledger_not_a_front_60_snapshot(self):
        rows=[row(f"id-{i:03}",pnl=i) for i in range(75)] + [row("foreign",scope="else")]
        page=page_trades(rows,SCOPE,page=2,page_size=30)
        self.assertEqual(page["total"],75)
        self.assertEqual(len(page["trades"]),30)
        self.assertEqual(page["total_pages"],3)
        with self.assertRaises(ValueError): page_trades(rows,SCOPE,page_size=201)

    def test_horizon_rebuild_remains_legacy_compatible_and_adds_periods(self):
        result=rebuild([row("one")],scope=SCOPE)
        self.assertIn("by_strategy",result)
        self.assertIn("periods",result)
        self.assertEqual(result["scope"],SCOPE)
        self.assertEqual(result["version"],2)
        self.assertEqual(result["timezone"],"Asia/Shanghai")

    def test_legacy_today_stats_keeps_percent_contract_and_counts_breakeven(self):
        result=today_lifecycle_stats([
            {"status":"closed","close_time":"2026-10-08 09:00:00","net_pnl":0},
            {"status":"closed","close_time":"2026-10-08 09:01:00","net_pnl":None},
        ],"2026-10-08")
        self.assertEqual(result["closed_trades"],2)
        self.assertEqual(result["outcome_trades"],1)
        self.assertEqual(result["breakeven_trades"],1)
        self.assertEqual(result["win_rate"],0.0)
        self.assertEqual(result["unsettled_amount_rows"],1)

    def test_shared_timestamp_normalization_and_canonical_correction(self):
        stamp=normalize_timestamp("2026-10-07T17:00:00Z")
        self.assertEqual(stamp.isoformat(),"2026-10-08T01:00:00+08:00")
        rows=[
            {"id":"corrected","status":"closed","close_time":"2026-10-07T17:00:00Z","net_pnl":2},
            {"id":"corrected","status":"closed","close_time":"2026-10-07T17:00:00Z","net_pnl":5},
        ]
        result=today_lifecycle_stats(rows,"2026-10-08",as_of=datetime(2026,10,8,12,tzinfo=SH))
        self.assertEqual(result["closed_trades"],1)
        self.assertEqual(result["net_realized"],5)
        self.assertEqual(canonical_rows(rows)[0]["net_pnl"],5)

    def test_unknown_amount_totals_are_null_with_observed_partial_sum(self):
        result=today_lifecycle_stats([
            {"id":"known","status":"closed","close_time":"2026-10-08 09:00:00","net_pnl":3,"gross_pnl":4,"fee":-1,"funding_fee":0},
            {"id":"unknown","status":"closed","close_time":"2026-10-08 10:00:00","net_pnl":None,"gross_pnl":None,"fee":None,"funding_fee":None},
        ],"2026-10-08",as_of=datetime(2026,10,8,12,tzinfo=SH))
        self.assertEqual(result["closed_trades"],2)
        self.assertEqual(result["outcome_trades"],1)
        self.assertIsNone(result["net_realized"])
        self.assertEqual(result["observed"]["net_realized"],3)
        self.assertIsNone(result["realized_gross"])
        self.assertEqual(result["observed"]["realized_gross"],4)
        self.assertFalse(result["fees_complete"])
        self.assertFalse(result["funding_complete"])

    def test_closed_missing_or_invalid_close_time_is_never_allocated_by_open_time(self):
        result=today_lifecycle_stats([
            {"id":"missing","status":"closed","open_time":"2026-10-08 09:00:00","net_pnl":9},
            {"id":"invalid","status":"closed","open_time":"2026-10-08 09:30:00","close_time":"bad","net_pnl":7},
        ],"2026-10-08",as_of=datetime(2026,10,8,12,tzinfo=SH))
        self.assertEqual(result["closed_trades"],0)
        self.assertEqual(result["unknown_close_time_rows"],2)
        self.assertEqual(result["outcome_trades"],0)

    def test_legacy_finite_rejects_boolean_and_overflow(self):
        result=today_lifecycle_stats([
            {"status":"closed","close_time":"2026-10-08 09:00:00","net_pnl":True},
            {"status":"closed","close_time":"2026-10-08 09:01:00","net_pnl":10**10000},
        ],"2026-10-08")
        self.assertEqual(result["closed_trades"],2)
        self.assertEqual(result["outcome_trades"],0)
        self.assertIsNone(result["net_realized"])
        self.assertEqual(result["observed"]["net_realized"],0)
        self.assertFalse(result["net_complete"])


if __name__ == "__main__": unittest.main()
