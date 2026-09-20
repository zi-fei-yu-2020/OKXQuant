"""A04 regressions. All exchange, clock and storage boundaries are local/mocked."""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import exit_policy, strategy_evidence as evidence, strategy_origin
from scripts import fill_accounting, ledger_monitor, horizon_stats, db_manager
from scripts import close_attribution, position_lifecycle

INST = "BTC-USDT-SWAP"
SCOPE = "okx:demo:a04-demo"

def receipt(**changes):
    return dict(instId=INST, direction="long", posId="p1", cTime="1000000",
                uTime="2000000", openAvgPx="100", closeAvgPx="102", closeTotalPos="2",
                openMaxPos="2", pnl="4", fee="-.03", fundingFee="-.01",
                realizedPnl="3.96", lever="3", **changes) if not changes else {**receipt(), **changes}

class ExitBindingTests(unittest.TestCase):
    def test_profile_switch_in_both_directions_does_not_rebind_live_tracker(self):
        for initial, active in (("standard", "small300"), ("small300", "standard")):
            tracker = {}
            read = lambda name: {"execution": {"id": name}, "signature": "signed-" + name}
            expected, _ = exit_policy.resolve(tracker, lambda: read(initial))
            tracker = json.loads(json.dumps(tracker))
            snapshot = copy.deepcopy(tracker["exitPolicy"])
            actual, _ = exit_policy.resolve(tracker, lambda: read(active))
            self.assertEqual(actual, expected)
            self.assertEqual(tracker["exitPolicy"], snapshot)
            fresh, _ = exit_policy.resolve({}, lambda: read(active))
            self.assertEqual(fresh, exit_policy.thresholds(active))

    def test_same_identity_keeps_peak_and_pending_write_after_reload(self):
        position = dict(instId=INST, posSide="long", posId="p1", cTime="1000")
        t = {"positionIdentity": position_lifecycle.identity(position, SCOPE),
             "highWaterMark": 123, "pendingStopAmendment": {"algoId": "unresolved"}}
        trackers = json.loads(json.dumps({"key": t}))
        self.assertEqual(position_lifecycle.reconcile(trackers, "key", position, SCOPE), "same")
        self.assertEqual(trackers["key"], t)

    def test_reset_cannot_discard_pending_evidence_when_archive_fails(self):
        old = {"positionIdentity": {"posId": "old"}, "pendingStopAmendment": {"algoId": "pending"}}
        trackers = {"key": copy.deepcopy(old)}
        position = dict(instId=INST, posSide="long", posId="new", cTime="2000")
        with patch.object(evidence, "best_effort", return_value=None):
            self.assertEqual(position_lifecycle.reconcile(trackers, "key", position, SCOPE), "unknown")
        self.assertEqual(trackers["key"], old)

    def test_retirement_archives_pending_evidence_before_removing_authority(self):
        old = {"positionIdentity": {"posId": "old"}, "pendingStopAmendment": {"algoId": "pending"}}
        trackers = {"key": copy.deepcopy(old)}
        with tempfile.TemporaryDirectory() as td, patch.object(evidence, "DB_PATH", Path(td)/"e.db"):
            self.assertTrue(position_lifecycle.retire(trackers, "key", SCOPE, reason="flat_observed"))
            self.assertNotIn("key", trackers)
            events = evidence.export_events(SCOPE)
            self.assertEqual(events[0]["payload"]["previous"], old)

class EvidenceIdentityTests(unittest.TestCase):
    def test_scope_and_kind_are_part_of_idempotency(self):
        with tempfile.TemporaryDirectory() as td, patch.object(evidence, "DB_PATH", Path(td)/"e.db"):
            evidence.append(SCOPE, "fill", {"n": 1}, "same")
            evidence.append(SCOPE, "fill", {"n": 1}, "same")
            for scope, kind in (("another-account", "fill"), (SCOPE, "decision")):
                with self.assertRaises(ValueError):
                    evidence.append(scope, kind, {"n": 1}, "same")
                with self.assertRaises(ValueError):
                    evidence.append_batch(scope, kind, [("new", {"n": 2}), ("same", {"n": 1})])
            self.assertEqual(len(evidence.export_events(SCOPE)), 1)

    def test_exchange_order_without_local_origin_is_not_proven_automatic(self):
        result = strategy_origin.resolve(receipt(), None, {},
            {"status": "verified", "opening_order_ids": ["external-order"]})
        self.assertEqual(result["source_status"], "external_or_unlinked")
        self.assertNotEqual(result["strategy_evidence"], "automatic_entry_unlinked")

class PartialCloseTests(unittest.TestCase):
    def test_partial_order_accumulated_fill_is_retained_and_deduplicated(self):
        order = dict(ordId="close1", instId=INST, posSide="long", side="sell",
                     state="partially_filled", accFillSz="1", fillTime="1500000", uTime="1500000")
        result = close_attribution.reason(receipt(), [order, dict(order)])
        self.assertEqual(result["close_order_ids"], ["close1"])
        self.assertEqual(result["attribution_status"], "partial")

    def test_later_lifecycle_fill_is_not_attributed_to_earlier_receipt(self):
        order = dict(ordId="next-life", instId=INST, posSide="long", side="sell", state="filled",
                     accFillSz="2", fillTime="2000001", uTime="2000001", clOrdId="okxquantclose1234567890")
        result = close_attribution.reason(receipt(), [order])
        self.assertEqual(result["close_order_ids"], [])
        self.assertEqual(result["attribution_status"], "unknown")

class LedgerLifecycleTests(unittest.TestCase):
    def setUp(self):
        from scripts import sync_full_ledger as ledger
        self.ledger = ledger
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        env = SimpleNamespace(identity=SCOPE, mode="demo")
        for module, name, value in (
            (evidence, "DB_PATH", self.root/"e.db"), (ledger_monitor, "DATA", self.root),
            (ledger, "DATA_DIR", str(self.root)), (ledger, "LEDGER_JSON_FILE", str(self.root/"ledger.json")),
            (ledger, "INITIAL_STATE_FILE", str(self.root/"initial.json")),
            (ledger, "POSITION_TRACKER_FILE", str(self.root/"trackers.json")),
        ):
            self.stack.enter_context(patch.object(module, name, value))
        self.stack.enter_context(patch.object(ledger, "selected_environment", return_value=env))
        self.stack.enter_context(patch.object(ledger, "TARGET_INSTRUMENTS", [{"name": "BTC", "instId": INST, "ctVal": 1}]))
        self.stack.enter_context(patch.object(ledger, "close_inputs", return_value={"orders": [], "algos": [], "executions": []}))
        self.stack.enter_context(patch("scripts.horizon_stats.write", side_effect=horizon_stats.rebuild))

    def build(self, history, positions=()):
        with patch.object(self.ledger, "read_snapshot", side_effect=[history, list(positions), []]):
            return self.ledger.build_lifecycle_ledger(notify=False)

    def test_updated_receipt_replaces_one_lifecycle_not_double_pnl_or_fees(self):
        first = self.build([receipt()])[0]
        rows = self.build([receipt(uTime="2001000", realizedPnl="3.95", fundingFee="-.02")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], first["id"])
        self.assertEqual(rows[0]["net_pnl"], 3.95)
        stats = horizon_stats.rebuild(rows)["unknown"]
        self.assertEqual(stats["closed"], 1)
        self.assertEqual(stats["fees"], -.03)
        self.assertEqual(stats["net_pnl"], 3.95)

    def test_older_receipt_page_cannot_roll_back_newer_persisted_settlement(self):
        self.build([receipt(uTime='2001000', realizedPnl='3.95')])
        rows = self.build([receipt()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['net_pnl'], 3.95)
        self.assertEqual(rows[0]['exit_snapshot']['uTime'], '2001000')

    def test_same_millisecond_hedge_sides_have_distinct_ids(self):
        rows = self.build([receipt(), receipt(direction="short", posId="p2")])
        self.assertEqual(len({r["id"] for r in rows}), 2)

    def test_duplicate_receipt_pages_select_latest_snapshot_once(self):
        rows = self.build([receipt(uTime="2001000", realizedPnl="3.95"), receipt(), receipt()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["net_pnl"], 3.95)

    def test_new_holding_does_not_erase_old_unsettled_lifecycle(self):
        old = dict(instId=INST, posSide="long", posId="old", cTime="1000000", pos="2",
                   avgPx="100", markPx="101", upl="2", lever="3")
        previous = self.build([], [old])[0]
        rows = self.build([], [{**old, "posId": "new", "cTime": "3000000"}])
        self.assertEqual(len(rows), 2)
        pending = next(r for r in rows if r["id"] == previous["id"])
        self.assertEqual(pending["status"], "closed_pending")
        self.assertIsNone(pending["net_pnl"])

    def test_legacy_receipt_identity_migration_does_not_double_count(self):
        old = self.build([receipt()])[0]
        old["id"] = "pos_hist_2000.0_BTC"
        old.pop("position_created_at", None)
        (self.root/"ledger.json").write_text(json.dumps([old]))
        rows = self.build([receipt(uTime="2001000")])
        self.assertEqual(len(rows), 1)

    def seed_fills(self):
        for identity, side, ts, fee in (("open", "buy", "1000000", "-.02"),
                                        ("close", "sell", "2000000", "-.01")):
            evidence.append(SCOPE, "fill", dict(instId=INST, posSide="long", ordId=identity,
                billId=identity, tradeId=identity, side=side, ts=ts, fee=fee, fillSz="2", feeCcy="USDT"))

    def test_verified_fee_evidence_survives_temporary_archive_loss(self):
        self.seed_fills()
        first = self.build([receipt()])[0]
        self.assertEqual(first["fee_reconciliation"]["status"], "verified")
        with patch.object(self.ledger, "read_fill_archive", return_value=fill_accounting.FillArchive("unavailable", {})):
            row = self.build([receipt()])[0]
        self.assertEqual(row["open_fee"], -.02)
        self.assertEqual(row["close_fee"], -.01)
        self.assertEqual(row["fee_reconciliation"]["bill_ids"], first["fee_reconciliation"]["bill_ids"])
        self.assertEqual(row["net_pnl"], 3.96)

    def test_changed_receipt_does_not_reuse_stale_fee_allocation(self):
        self.seed_fills()
        first = self.build([receipt()])[0]
        with patch.object(self.ledger, "read_fill_archive", return_value=fill_accounting.FillArchive("unavailable", {})):
            row = self.build([receipt(fee="-.04", realizedPnl="3.95")])[0]
        self.assertIsNone(row["open_fee"])
        self.assertEqual(row["net_pnl"], 3.95)
        self.assertEqual(row["prior_lifecycle_evidence"]["fee_reconciliation"], first["fee_reconciliation"])

    def test_same_lifecycle_with_remaining_size_is_not_finalized(self):
        active = dict(instId=INST, posSide="long", posId="p1", cTime="1000000", pos="1",
                      avgPx="100", markPx="101", upl="1", lever="3")
        rows = self.build([receipt()], [active])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "holding")
        self.assertEqual(rows[0]["partial_close_receipt"], receipt())
        self.assertEqual(horizon_stats.rebuild(rows)["unknown"].get("closed", 0), 0)

    def test_same_time_conflicting_receipts_abort_without_overwriting(self):
        self.build([receipt()])
        old = (self.root/"ledger.json").read_bytes()
        with self.assertRaises(ValueError):
            self.build([receipt(), receipt(fee="-.04")])
        self.assertEqual((self.root/"ledger.json").read_bytes(), old)

    def test_distinct_millisecond_lifecycles_do_not_merge(self):
        self.build([receipt(cTime="1000001", uTime="1000002")])
        rows = self.build([receipt(cTime="1000003", uTime="1000004")])
        self.assertEqual(len(rows), 2)

    def test_scoped_replacement_keeps_other_accounts_pending_row_with_legacy_id_collision(self):
        old = self.build([receipt()])[0]
        old['id'] = 'legacy-shared-id'
        other = {**old, 'environment_id': 'another-account', 'status': 'closed_pending', 'pnl': None, 'net_pnl': None}
        (self.root/'ledger.json').write_text(json.dumps([old, other]))
        rows = self.build([receipt()])
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(r['environment_id'] == 'another-account' and r['status'] == 'closed_pending' for r in rows))

    def test_legacy_mirror_alias_is_replaced_atomically_not_double_counted(self):
        old = self.build([receipt()])[0]
        old['id'] = 'pos_hist_2000.0_BTC'
        old.pop('position_created_at', None)
        (self.root/'ledger.json').write_text(json.dumps([old]))
        with patch.object(db_manager, 'DB_PATH', str(self.root/'mirror.db')), patch.object(db_manager, 'DATA_DIR', str(self.root)):
            db_manager.sync_json_to_sqlite(self.root/'ledger.json')
            rows = self.build([receipt(uTime='2001000')])
            db_manager.sync_json_to_sqlite(self.root/'ledger.json')
            db_manager.sync_json_to_sqlite(self.root/'ledger.json')
            with sqlite3.connect(self.root/'mirror.db') as db:
                values = db.execute('SELECT bill_id, pnl, fee FROM trades').fetchall()
            self.assertEqual(values, [(rows[0]['id'], 3.96, -.03)])

    def test_bad_existing_ledger_is_preserved_not_silently_replaced(self):
        original = b"[{broken-ledger"
        (self.root/"ledger.json").write_bytes(original)
        with self.assertRaises((ValueError, RuntimeError)):
            self.build([receipt()])
        self.assertEqual((self.root/"ledger.json").read_bytes(), original)

class MirrorTests(unittest.TestCase):
    def test_mirror_preserves_canonical_pnl_unknowns_and_zero_without_fee_subtraction(self):
        for values, expected in (({"pnl": 3.96, "net_pnl": 9}, 3.96),
                                 ({"pnl": None, "net_pnl": 9}, None),
                                 ({"net_pnl": 9}, None),
                                 ({"pnl": 0, "net_pnl": 9}, 0)):
            row = db_manager._mirror_row({"id": "1", "status": "closed", "fee": -.03,
                                          "gross_pnl": 4, **values})
            self.assertEqual(row[9], expected)
