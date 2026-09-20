"""Persist observed account equity only; never manufacture an equity curve from bills."""
import datetime
import json
import math
import os
import tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.okx_runtime import selected_environment
from scripts.sync_web_data import account_command, run_json_cmd
from scripts.dashboard_stats import scoped_rows
from okxquant_backend.account_baseline import load_account_baseline

DATA_DIR = str(Path(__file__).resolve().parents[1] / "data")
SNAPSHOTS_FILE = os.path.join(DATA_DIR, "snapshots.json")


def generate_live_snapshots():
    environment = selected_environment()
    balance = run_json_cmd(account_command(environment, "balance"), environment=environment)
    if not isinstance(balance, list) or not balance or not isinstance(balance[0], dict):
        raise ValueError("Balance unavailable; previous snapshots preserved")
    detail = next((row for row in balance[0].get("details", [])
                   if isinstance(row, dict) and row.get("ccy") == "USDT"), {})
    if "eq" not in detail:
        raise ValueError("USDT equity unavailable; previous snapshots preserved")
    equity = float(detail["eq"])
    if not math.isfinite(equity):
        raise ValueError("Equity is not finite")
    snapshots = []
    try:
        snapshots = scoped_rows(json.loads(Path(SNAPSHOTS_FILE).read_text(encoding="utf-8")), environment.identity)
    except (OSError, ValueError):
        pass
    # A07 owns baseline migration/account selection; do not duplicate its file schema.
    baseline = load_account_baseline(scope=environment.identity)
    try:
        capital = float(baseline.get("initial_capital", 0))
    except (TypeError, ValueError):
        capital = 0
    configured = (baseline.get("baseline_configured") is True
                  and baseline.get("account_scope") == environment.identity
                  and math.isfinite(capital) and capital > 0)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    snapshots.append({
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "total_eq": round(equity, 2), "equity": round(equity, 2),
        "pnl": round(equity - capital, 2) if configured else None,
        "roi": round((equity - capital) / capital * 100, 2) if configured else None,
        "baseline_configured": configured,
        "environment_id": environment.identity, "environment": environment.mode,
        "source": "observed_balance",
    })
    if selected_environment().identity != environment.identity:
        raise ValueError("Account changed while collecting snapshots")
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".equity-", suffix=".json", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshots, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, SNAPSHOTS_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return snapshots


if __name__ == "__main__":
    generate_live_snapshots()
