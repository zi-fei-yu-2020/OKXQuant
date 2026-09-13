"""Offline SQLite mirror accounting tests; every file lives in a temporary directory."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import db_manager as db


class MirrorAccountingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="db-mirror-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / "ledger.json"
        self.database = self.root / "mirror.sqlite"
        for name, value in (
            ("DATA_DIR", str(self.root)),
            ("DB_PATH", str(self.database)),
            ("LEDGER_JSON_FILE", str(self.ledger)),
        ):
            patcher = patch.object(db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.connections = []
        original_get_db = db.get_db

        def tracked_get_db():
            conn = original_get_db()
            self.connections.append(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            return conn

        patcher = patch.object(db, "get_db", side_effect=tracked_get_db)
        patcher.start()
        self.addCleanup(patcher.stop)
        # This is also the pre-existing schema used by older databases.
        db.init_database()

    def tearDown(self):
        for conn in self.connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def write(self, rows):
        self.ledger.write_text(json.dumps(rows), encoding="utf-8")

    def snapshot(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute("SELECT * FROM trades ORDER BY bill_id")]

    def seed(self):
        self.write([
            {"id": "existing", "status": "closed", "price": 42, "size": 3,
             "fee": -1, "gross_pnl": 8, "pnl": 7},
            {"id": "untouched", "status": "holding", "open_px": 10, "sz": 2},
        ])
        self.assertEqual(db.sync_json_to_sqlite(), 2)
        return self.snapshot()

    def install_trigger(self, sql):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript(sql)

    def test_explicit_nulls_do_not_fall_back(self):
        self.write([{
            "id": "pending", "status": "closed_pending", "close_px": None,
            "price": 99, "open_px": 100, "closed_size": None, "sz": 0, "size": 5,
            "fee": None, "gross_pnl": None, "pnl": None, "net_pnl": 12,
        }])
        self.assertEqual(db.sync_json_to_sqlite(), 1)
        row = self.snapshot()[0]
        self.assertEqual(row["action"], "closed_pending")
        for field in ("price", "size", "fee", "gross_pnl", "pnl"):
            self.assertIsNone(row[field], field)

    def test_explicit_null_gross_pnl_does_not_use_known_net_pnl(self):
        self.write([{"id": "unknown-gross", "gross_pnl": None, "pnl": 7, "fee": -1}])
        db.sync_json_to_sqlite()
        row = self.snapshot()[0]
        self.assertIsNone(row["gross_pnl"])
        self.assertEqual(row["pnl"], 7)

    def test_pending_unknowns_replace_previously_numeric_mirror(self):
        self.seed()
        self.write([{"id": "existing", "status": "closed_pending", "close_px": None,
                     "open_px": 42, "fee": None, "gross_pnl": None, "pnl": None, "sz": 0}])
        self.assertEqual(db.sync_json_to_sqlite(), 1)
        rows = self.snapshot()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["action"], "closed_pending")
        for field in ("price", "fee", "gross_pnl", "pnl"):
            self.assertIsNone(rows[0][field], field)
        self.assertEqual(rows[0]["size"], 0)

    def test_zero_is_not_replaced_by_nonzero_aliases(self):
        self.write([{
            "id": "zero", "status": "closed", "close_px": 0, "price": 99,
            "open_px": 100, "closed_size": 0, "sz": 8, "size": 9,
            "fee": 0, "gross_pnl": 0, "pnl": -3,
        }])
        db.sync_json_to_sqlite()
        row = self.snapshot()[0]
        for field in ("price", "size", "fee", "gross_pnl"):
            self.assertEqual(row[field], 0, field)
        self.assertEqual(row["pnl"], -3)
        self.write([{"id": "zero", "pnl": 0}])
        db.sync_json_to_sqlite()
        self.assertEqual(self.snapshot()[0]["pnl"], 0)

    def test_closed_size_overrides_zero_remaining_position(self):
        self.write([{
            "id": "settled", "status": "closed", "closed_size": "4.5", "sz": 0,
            "close_px": "123.5", "fee": "-0.75", "gross_pnl": "0", "pnl": "-0.75",
        }])
        db.sync_json_to_sqlite()
        row = self.snapshot()[0]
        self.assertEqual(row["size"], 4.5)
        self.assertEqual(row["price"], 123.5)
        self.assertEqual(row["fee"], -0.75)
        self.assertEqual(row["gross_pnl"], 0)
        self.assertEqual(row["pnl"], -0.75)

    def test_missing_legacy_fields_use_only_compatible_aliases(self):
        self.write([{
            "bill_id": "legacy", "time": "2026-09-08 10:00:00", "name": "BTC",
            "action_type": "closed", "direction": "long", "size": "2.5",
            "price": "50", "pnl": 7, "fee": -1, "comment": "old schema",
        }])
        db.sync_json_to_sqlite()
        row = self.snapshot()[0]
        self.assertEqual(row["bill_id"], "legacy")
        self.assertEqual(row["inst"], "BTC")
        self.assertEqual(row["action"], "closed")
        self.assertEqual(row["direction"], "long")
        self.assertEqual(row["size"], 2.5)
        self.assertEqual(row["price"], 50)
        self.assertEqual(row["pnl"], 7)
        self.assertIsNone(row["gross_pnl"])

    def test_missing_numbers_are_unknown_not_zero(self):
        self.write([{"id": "minimal", "net_pnl": 9}])
        db.sync_json_to_sqlite()
        row = self.snapshot()[0]
        for field in ("size", "price", "fee", "gross_pnl", "pnl"):
            self.assertIsNone(row[field], field)

    def test_missing_exit_price_never_uses_entry_price_for_closed_rows(self):
        for status in ("closed", "closed_pending", "confirmed_closed"):
            with self.subTest(status=status):
                self.write([{"id": "exit", "status": status, "open_px": 200, "sz": 0}])
                db.sync_json_to_sqlite()
                row = self.snapshot()[0]
                self.assertIsNone(row["price"])
                self.assertEqual(row["size"], 0)

    def test_holding_price_and_size_aliases_preserve_null_and_zero(self):
        for value in (None, 0):
            with self.subTest(value=value):
                self.write([{"id": "holding", "status": "holding", "close_px": value,
                             "price": 80, "open_px": 90, "sz": value, "size": 10,
                             "closed_size": 20}])
                db.sync_json_to_sqlite()
                row = self.snapshot()[0]
                self.assertEqual(row["price"], value)
                self.assertEqual(row["size"], value)
        self.write([{"id": "holding", "status": "holding", "open_px": 90, "size": 10}])
        db.sync_json_to_sqlite()
        self.assertEqual(self.snapshot()[0]["price"], 90)
        self.assertEqual(self.snapshot()[0]["size"], 10)

    def test_invalid_json_and_root_leave_mirror_unchanged(self):
        before = self.seed()
        payloads = ('[{', '', 'null', '{}', '"not an array"', '42', 'true',
                    '[NaN]', '[Infinity]', '[{"pnl": -Infinity}]', '[{"pnl": 1e309}]')
        for payload in payloads:
            with self.subTest(payload=payload):
                self.ledger.write_text(payload, encoding="utf-8")
                count = len(self.connections)
                with self.assertRaises(ValueError):
                    db.sync_json_to_sqlite()
                self.assertEqual(len(self.connections), count)
                self.assertEqual(self.snapshot(), before)
        self.ledger.write_bytes(b'\xff')
        with self.assertRaises(UnicodeError):
            db.sync_json_to_sqlite()
        self.assertEqual(self.snapshot(), before)

    def test_bad_later_row_cannot_replace_or_insert_earlier_rows(self):
        before = self.seed()
        bad_rows = [None, [], "bad", 123, {"id": "bad", "fee": "invalid"},
                    {"id": "bad", "close_px": []}, {"id": "bad", "sz": True},
                    {"id": "bad", "gross_pnl": "NaN"}, {"id": "bad", "pnl": "Infinity"},
                    {"id": "bad", "closed_size": ""}]
        for bad in bad_rows:
            with self.subTest(bad=bad):
                self.write([{"id": "existing", "pnl": 999}, {"id": "new", "pnl": 5}, bad])
                count = len(self.connections)
                with self.assertRaises(ValueError):
                    db.sync_json_to_sqlite()
                self.assertEqual(len(self.connections), count)
                self.assertEqual(self.snapshot(), before)

    def test_each_numeric_field_rejects_invalid_values(self):
        before = self.seed()
        for field in ("closed_size", "sz", "size", "close_px", "price", "fee", "gross_pnl", "pnl"):
            for value in ("", "bad", True, False, [], {}, "NaN", "inf", "-inf"):
                with self.subTest(field=field, value=value):
                    self.write([{"id": "existing", field: value}])
                    with self.assertRaises(ValueError):
                        db.sync_json_to_sqlite()
                    self.assertEqual(self.snapshot(), before)

    def test_multirow_sql_failure_rolls_back_replacement_and_insertion(self):
        before = self.seed()
        self.install_trigger("""
            CREATE TRIGGER reject_bad BEFORE INSERT ON trades
            WHEN NEW.bill_id = 'bad'
            BEGIN SELECT RAISE(ABORT, 'test row rejected'); END;
        """)
        self.write([{"id": "existing", "pnl": 999}, {"id": "new", "pnl": 5}, {"id": "bad"}])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test row rejected"):
            db.sync_json_to_sqlite()
        self.assertEqual(self.snapshot(), before)
        # A subsequent write must work (no leaked transaction/lock).
        self.write([{"id": "existing", "pnl": 0}])
        self.assertEqual(db.sync_json_to_sqlite(), 1)
        self.assertEqual(self.snapshot()[0]["pnl"], 0)

    def test_commit_failure_rolls_back_all_rows_and_closes_connections(self):
        before = self.seed()
        self.install_trigger("""
            CREATE TABLE parent (id INTEGER PRIMARY KEY);
            CREATE TABLE guard (
                parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER reject_at_commit AFTER INSERT ON trades
            WHEN NEW.bill_id = 'bad'
            BEGIN INSERT INTO guard VALUES (123); END;
        """)
        self.write([{"id": "existing", "pnl": 999}, {"id": "new"}, {"id": "bad"}])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
            db.sync_json_to_sqlite()
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM guard").fetchone()[0], 0)

    def test_missing_empty_and_unreadable_ledger_preserve_existing_rows(self):
        before = self.seed()
        self.ledger.unlink()
        count = len(self.connections)
        self.assertEqual(db.sync_json_to_sqlite(), 0)
        self.assertEqual(len(self.connections), count)
        self.write([])
        self.assertEqual(db.sync_json_to_sqlite(), 0)
        self.assertEqual(self.snapshot(), before)
        count = len(self.connections)
        with patch("builtins.open", side_effect=PermissionError("test unreadable ledger")):
            with self.assertRaises(PermissionError):
                db.sync_json_to_sqlite()
        self.assertEqual(len(self.connections), count)
        self.assertEqual(self.snapshot(), before)

    def test_existing_schema_needs_no_migration(self):
        with closing(sqlite3.connect(self.database)) as conn:
            schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        self.write([{"id": "legacy", "status": "closed_pending", "pnl": None}])
        db.sync_json_to_sqlite()
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall(), schema)

    def test_initialization_failure_closes_connection(self):
        broken = self.root / "broken.sqlite"
        with closing(sqlite3.connect(broken)) as conn, conn:
            conn.execute("CREATE TABLE trades (id INTEGER, time TEXT)")
        with patch.object(db, "DB_PATH", str(broken)):
            with self.assertRaises(sqlite3.OperationalError):
                db.init_database()
        with closing(sqlite3.connect(broken)) as conn:
            # The time index created before the failing inst index is rolled back.
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall(), [])

    def test_direct_writer_preserves_unknown_and_zero(self):
        for value in (None, 0):
            with self.subTest(value=value):
                db.record_trade_sqlite({"bill_id": "direct", "price": value, "size": value,
                                        "sz": 9, "fee": value, "gross_pnl": value, "pnl": value})
                row = self.snapshot()[0]
                for field in ("price", "size", "fee", "gross_pnl", "pnl"):
                    self.assertEqual(row[field], value, field)
        db.record_trade_sqlite({"bill_id": "direct", "pnl": 5})
        row = self.snapshot()[0]
        self.assertIsNone(row["fee"])
        self.assertIsNone(row["gross_pnl"])

    def test_direct_writer_sql_failure_preserves_mirror(self):
        before = self.seed()
        self.install_trigger("""
            CREATE TRIGGER reject_direct BEFORE INSERT ON trades
            BEGIN SELECT RAISE(ABORT, 'test direct rejection'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test direct rejection"):
            db.record_trade_sqlite({"bill_id": "existing", "pnl": 999})
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
