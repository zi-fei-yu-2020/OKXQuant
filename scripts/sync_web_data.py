#!/usr/bin/env python3
"""Generate local OKXQuant dashboard cache without an external console dependency."""

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.okx_runtime import selected_environment
from scripts.dashboard_stats import scoped_rows, today_lifecycle_stats
import json
from scripts import public_market as market
import subprocess
import datetime
import math
import tempfile

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
SNAPSHOTS_JSON_FILE = os.path.join(DATA_DIR, "snapshots.json")
LOG_FILE = os.path.join(LOGS_DIR, "trading.log")
DATA_JSON_PATH = os.path.join(DATA_DIR, "trading_data.json")

from scripts.instrument_pool import load_instruments

def account_command(environment, arguments):
    return f"{environment.cli_prefix()} account {arguments} --json"


def observed_number(value):
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError("Account observation is missing")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Account observation is not finite")
    return number


def run_json_cmd(cmd: str, timeout: int = 15, *, environment=None):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, env=environment.cli_env() if environment is not None else None)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip())
    except Exception:
        pass
    return None

def get_disk_info():
    try:
        import shutil
        total, used, free = shutil.disk_usage(WORKSPACE_DIR)
        return {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "percent": round((used / total) * 100, 1)
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}

def generate_trading_data():
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    today_str = now_bj.strftime("%Y-%m-%d")

    environment = selected_environment()
    auth_data = run_json_cmd("okx auth status --json") or {}
    if not isinstance(auth_data, dict):
        auth_data = {}
    is_authenticated = auth_data.get("status") == "logged_in"

    # 1. Balance
    bal_data = run_json_cmd(account_command(environment, "balance"), environment=environment)
    if not isinstance(bal_data, list) or not bal_data or not isinstance(bal_data[0], dict):
        raise ValueError("Balance unavailable; previous scoped cache preserved")
    usdt_bal = {}
    if bal_data and isinstance(bal_data, list) and "details" in bal_data[0]:
        for d in bal_data[0]["details"]:
            if d.get("ccy") == "USDT":
                usdt_bal = d
                break

    if "eq" not in usdt_bal:
        raise ValueError("USDT equity unavailable; previous scoped cache preserved")
    total_eq = observed_number(usdt_bal["eq"])
    avail_eq = float(usdt_bal.get("availEq", 0) or 0)
    cash_bal = float(usdt_bal.get("cashBal", 0) or 0)
    upl_acc = float(usdt_bal.get("upl", 0) or 0)

    # 2. Positions
    pos_data = run_json_cmd(account_command(environment, "positions"), environment=environment)
    if not isinstance(pos_data, list) or any(not isinstance(row, dict) for row in pos_data):
        raise ValueError("Positions unavailable; previous scoped cache preserved")
    positions = []
    long_count = 0
    short_count = 0
    total_pos_upl = 0.0

    if isinstance(pos_data, list):
        for p in pos_data:
            pos_sz = float(p.get("pos", 0) or 0)
            if pos_sz == 0:
                continue
            pos_side = p.get("posSide", "net")
            direction = pos_side if pos_side in {"long", "short"} else ("long" if pos_sz > 0 else "short")
            if direction == "long":
                long_count += 1
            elif direction == "short":
                short_count += 1
            upl = float(p.get("upl", 0) or 0)
            total_pos_upl += upl
            positions.append({
                "instId": p.get("instId"),
                "posSide": pos_side,
                "pos": p.get("pos"),
                "lever": p.get("lever", "3"),
                "avgPx": float(p.get("avgPx", 0) or 0),
                "markPx": float(p.get("markPx", 0) or 0),
                "upl": upl,
                "uplRatio": float(p.get("uplRatio", 0) or 0) * 100,
                "liqPx": p.get("liqPx", "--"),
                "bePx": p.get("bePx", "--")
            })

    # 4. Snapshots & Trades from JSON
    snapshots = []
    trades = []
    if os.path.exists(SNAPSHOTS_JSON_FILE):
        try:
            with open(SNAPSHOTS_JSON_FILE, "r", encoding="utf-8") as f:
                snapshots = scoped_rows(json.load(f), environment.identity)[-40:]
        except Exception:
            pass

    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                trades = list(reversed(scoped_rows(json.load(f), environment.identity)))
        except Exception:
            pass

    # 5. Multi-factor signals & AI Brain Decisions
    ai_decisions_file = os.path.join(DATA_DIR, "ai_brain_decisions.json")
    ai_decisions = {}
    if os.path.exists(ai_decisions_file):
        try:
            with open(ai_decisions_file, "r", encoding="utf-8") as f:
                ai_decisions = json.load(f)
        except Exception:
            pass

    factors = []
    for item in load_instruments():
        inst_id = item["instId"]
        name = item["name"]
        try:
            ticker_res = market.get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}")["data"]
        except Exception:
            ticker_res = []
        ticker = ticker_res[0] if isinstance(ticker_res, list) and ticker_res else (ticker_res if isinstance(ticker_res, dict) else {})
        last_px = float(ticker.get("last", 0) or 0)
        open24h = float(ticker.get("open24h", 0) or 0)
        high24h = float(ticker.get("high24h", 0) or 0)
        low24h = float(ticker.get("low24h", 0) or 0)
        chg_24h = round(((last_px - open24h) / open24h * 100) if open24h > 0 else 0, 2)
        
        ai_data = ai_decisions.get(inst_id, {})
        ai_dec = ai_data.get("decision", {})
        ai_thought = ai_data.get("thought_process", {})
        
        action = ai_dec.get("action", "WAIT")
        score = 0.0
        if action == "BUY_LONG":
            score = 2.5
        elif action == "SELL_SHORT":
            score = -2.5

        factors.append({
            "instId": inst_id,
            "name": name,
            "lastPx": last_px,
            "high24h": high24h,
            "low24h": low24h,
            "chg24h": chg_24h,
            "score": score,
            "action": action,
            "confidence": ai_dec.get("confidence"),
            "reason": ai_dec.get("summary_reason", "等待高确定性行情出现"),
            "thought_process": ai_thought,
            "ai_decision": ai_dec
        })

    # 6. Recent Logs
    logs = []
    if os.path.exists(LOG_FILE):
        try:
            res = subprocess.run(f"tail -n 60 {LOG_FILE}", shell=True, capture_output=True, text=True)
            logs = res.stdout.splitlines()
        except Exception:
            pass

    disk = get_disk_info()

    data = {
        "account_source_id": environment.identity,
        "environment": environment.mode,
        "account_data_status": "ready",
        "timestamp": now_bj.strftime("%Y-%m-%d %H:%M:%S (北京时间)"),
        "date": today_str,
        "auth": {
            "is_logged_in": is_authenticated,
            "status": auth_data.get("status", "not_logged_in"),
            "site": auth_data.get("site", "global"),
            "verificationUri": auth_data.get("verificationUri", "https://www.okx.com/account/oauth?flow=device"),
            "userCode": auth_data.get("userCode", "")
        },
        "account": {
            "total_eq": round(total_eq, 2),
            "avail_eq": round(avail_eq, 2),
            "cash_bal": round(cash_bal, 2),
            "upl": round(upl_acc, 2),
            "pos_upl_total": round(total_pos_upl, 2),
            "margin_usage_pct": round(((total_eq - avail_eq) / total_eq * 100) if total_eq > 0 else 0, 1)
        },
        "positions_summary": {
            "total": len(positions),
            "max": 10,
            "long_count": long_count,
            "short_count": short_count,
            "items": positions
        },
        "factors": factors,
        "snapshots": snapshots,
        "trades": trades[:60],
        "logs": logs,
        "system": {
            "disk": disk
        }
    }

    # Wins/losses are lifecycle settlements, not individual fee/bill rows.
    settled = today_lifecycle_stats(trades, today_str)
    settled.pop("settled_rows", None)
    data["today_stats"] = {**settled, "total_pnl": round(settled["net_realized"] + total_pos_upl, 2)}
    # An account switch during collection must not publish a mixed observation.
    if selected_environment().identity != environment.identity:
        raise ValueError("Account changed while collecting dashboard data")
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".trading-data-", suffix=".json", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, DATA_JSON_PATH)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

if __name__ == "__main__":
    generate_trading_data()
