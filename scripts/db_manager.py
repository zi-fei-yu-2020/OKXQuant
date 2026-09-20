import sqlite3
import os
import json
import math
from contextlib import closing

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "okxquant_quant.db")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    os.makedirs(DATA_DIR, exist_ok=True)
    with closing(get_db()) as conn, conn:
        # DDL needs an explicit transaction too; context entry alone does not begin one.
        conn.execute("BEGIN")
        cursor = conn.cursor()
        # 1. Trades table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id TEXT UNIQUE,
            time TEXT NOT NULL,
            inst TEXT NOT NULL,
            action TEXT NOT NULL,
            direction TEXT NOT NULL,
            size REAL,
            price REAL,
            fee REAL,
            gross_pnl REAL,
            pnl REAL,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_inst ON trades(inst);")

        # 2. Daily Backups log
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_date TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)


def _number(row, *fields):
    """Use the first present field; missing and explicit null are both unknown.

    Numeric strings remain supported for legacy ledgers. Zero must never trigger
    an alias fallback, and invalid/non-finite values must abort the whole write.
    """
    for field in fields:
        if field not in row:
            continue
        value = row[field]
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"Invalid numeric field: {field}")
        try:
            number = float(value)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid numeric field: {field}") from exc
        if not math.isfinite(number):
            raise ValueError(f"Non-finite numeric field: {field}")
        return number
    return None


def _mirror_row(t):
    if not isinstance(t, dict):
        raise ValueError("Each ledger row must be an object")
    t_time = str(t.get("close_time") or t.get("time") or t.get("open_time") or "")
    inst = str(t.get("inst") or t.get("name") or "")
    act = str(t.get("status") or t.get("action") or t.get("action_type") or "closed")
    closed = act in {"closed", "closed_pending", "confirmed_closed"}
    # An entry price is not evidence of an exit price, even for old rows.
    price_fields = ("close_px", "price") if closed else ("close_px", "price", "open_px")
    size_fields = ("closed_size", "sz", "size") if closed else ("sz", "size")
    px = _number(t, *price_fields)
    bill_id = t.get("id") or t.get("bill_id") or f"{t_time}_{inst}_{act}_{px}"
    return (
        bill_id,
        t_time,
        inst,
        act,
        str(t.get("side") or t.get("direction") or ""),
        _number(t, *size_fields),
        px,
        _number(t, "fee"),
        # Net PnL cannot establish gross PnL without full fee accounting.
        _number(t, "gross_pnl"),
        _number(t, "pnl"),
        str(t.get("exit_reason") or t.get("remark") or t.get("comment") or ""),
    )


def _write_rows(rows, *, superseded=()):
    init_database()
    # sqlite3's transaction context commits/rolls back but does not close.
    # Closing is the outer context so it also runs on execute/commit failures.
    with closing(get_db()) as conn, conn:
        cursor = conn.cursor()
        inserted = 0
        # Remove only explicit, reconciled lifecycle aliases, never arbitrary rows
        # absent from a partial ledger. Roll back alias removal if any insert fails.
        for identity in superseded:
            cursor.execute('DELETE FROM trades WHERE bill_id=?', (identity,))
        for row in rows:
            cursor.execute("""
            INSERT OR REPLACE INTO trades
            (bill_id, time, inst, action, direction, size, price, fee, gross_pnl, pnl, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
            if cursor.rowcount > 0:
                inserted += 1
    return inserted


def _reject_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def sync_json_to_sqlite(ledger_path=None):
    try:
        with open(LEDGER_JSON_FILE if ledger_path is None else ledger_path, "r", encoding="utf-8") as f:
            trades = json.load(f, parse_constant=_reject_json_constant)
    except FileNotFoundError:
        return 0
    # Do not turn a corrupt/unreadable ledger into an apparently successful sync.
    # Validate the entire input before opening the database; SQL errors during
    # the subsequent batch are handled by the single write transaction.
    if not isinstance(trades, list):
        raise ValueError("Ledger JSON must be an array of objects")
    rows = [_mirror_row(t) for t in trades]
    superseded = set()
    current_ids = {row[0] for row in rows}
    for trade in trades:
        aliases = trade.get('superseded_ledger_ids', [])
        if not isinstance(aliases, list) or any(not isinstance(x, str) or not x for x in aliases):
            raise ValueError('Invalid reconciled ledger aliases')
        if aliases and (trade.get('status') != 'closed' or not trade.get('environment_id')
                        or not trade.get('pos_id') or not trade.get('position_created_at')):
            raise ValueError('Ledger aliases require a settled scoped lifecycle')
        superseded.update(aliases)
    return _write_rows(rows, superseded=superseded-current_ids)


def record_trade_sqlite(trade_data: dict):
    t_time = str(trade_data.get("time", ""))
    inst = str(trade_data.get("inst", trade_data.get("name", "")))
    act = str(trade_data.get("action", trade_data.get("action_type", "")))
    px = _number(trade_data, "price")
    bill_id = trade_data.get("bill_id") or f"{t_time}_{inst}_{act}_{px}"
    _write_rows([(
        bill_id,
        t_time,
        inst,
        act,
        str(trade_data.get("direction", trade_data.get("side", ""))),
        _number(trade_data, "size", "sz"),
        px,
        _number(trade_data, "fee"),
        _number(trade_data, "gross_pnl"),
        _number(trade_data, "pnl"),
        str(trade_data.get("comment") or trade_data.get("remark") or ""),
    )])

if __name__ == "__main__":
    init_database()
    ins = sync_json_to_sqlite()
    print(f"SQLite DB initialized and synced {ins} trades.")
