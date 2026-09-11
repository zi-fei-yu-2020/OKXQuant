"""Environment-specific SWAP availability, independent of price/data quality.

Only a successful full catalog can prove an instrument absent. Network failures
are UNKNOWN, never 'unsupported'. Existing position protection is not gated here.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import threading
import time
try:
    from . import public_market as market
except ImportError:
    import public_market as market

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MAX_AGE = 60
DISPLAY_MAX_AGE = 300  # Display-only grace; opening_status always uses MAX_AGE.
_WORKERS = set()
_WORKER_LOCK = threading.Lock()
_FAILED_UNTIL = {}


def _mode(environment):
    if environment not in {"demo", "live"}:
        raise ValueError("Invalid OKX environment")
    return environment


def _path(environment):
    return DATA_DIR / f"instrument_support_{_mode(environment)}.json"


def _read(environment, *, max_age=MAX_AGE):
    try:
        value = json.loads(_path(environment).read_text(encoding="utf-8"))
        if (value.get("version") == 1 and value.get("environment") == environment
                and value.get("ok") is True and isinstance(value.get("instruments"), dict)
                and value["instruments"]
                and all(isinstance(r, dict) and r.get("state") for r in value["instruments"].values())
                and 0 <= time.time() - value["checked_at"] < max_age):
            return value
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def refresh_catalog(environment, *, force=False):
    environment = _mode(environment)
    cached = _read(environment)
    if cached and not force:
        return cached
    deadline = time.monotonic() + 8
    try:
        with market.file_lock(("instrument-support", environment), deadline):
            cached = _read(environment)
            if cached and not force:
                return cached
            @market.observe_collection
            def collect():
                payload = market.get_json("https://www.okx.com/api/v5/public/instruments?instType=SWAP",
                                          timeout=8, simulated=environment == "demo")
                return {"rows": payload["data"]}
            result = market.run_with_deadline(deadline, collect)
            rows = result["rows"]
            if not rows or any(not isinstance(row, dict) or not row.get("instId") or not row.get("state") for row in rows):
                raise market.MarketDataError("Incomplete instrument catalog")
            snapshot = {"version": 1, "environment": environment, "ok": True,
                        "checked_at": result["collection_quality"]["oldest_source_at"],
                        "instruments": {r["instId"]: {"state": r["state"], "settleCcy": r.get("settleCcy", "")} for r in rows}}
            market.atomic_json(_path(environment), snapshot)
            return snapshot
    except Exception:
        # Never overwrite known catalog contents with an empty/failed response.
        return None


def _background(environment):
    if os.getenv("OKXQUANT_TESTING") == "1":
        return
    with _WORKER_LOCK:
        if environment in _WORKERS or time.monotonic() < _FAILED_UNTIL.get(environment, 0):
            return
        _WORKERS.add(environment)
    def work():
        try:
            if refresh_catalog(environment, force=True) is None:
                _FAILED_UNTIL[environment] = time.monotonic() + 5
        finally:
            with _WORKER_LOCK:
                _WORKERS.discard(environment)
    threading.Thread(target=work, name="instrument-support-" + environment, daemon=True).start()


def _status(inst_id, environment, catalog):
    label = "模拟盘" if environment == "demo" else "实盘"
    checked_at = catalog["checked_at"] if catalog else None
    row = catalog["instruments"].get(inst_id) if catalog else None
    if catalog is None:
        status, title, message = "unknown", "支持状态待确认", f"暂时无法确认 {label} 是否支持 {inst_id}；核验前不参与新开仓或加仓，已有持仓保护不受此检查影响。"
    elif row is None:
        status, title, message = "unsupported", label + "不支持", f"{inst_id} 不在当前 OKX {label}永续合约目录中，仅供行情观察，不参与新开仓或加仓。可见公共行情不代表当前环境支持交易。"
    elif row["state"] != "live" or row.get("settleCcy") not in ("", "USDT"):
        status, title, message = "unavailable", label + "暂不可交易", f"{inst_id} 当前合约状态为 {row['state']}，暂不参与新开仓或加仓；已有持仓保护继续运行。"
    else:
        status, title, message = "supported", label + "支持", f"已在 OKX {label}合约目录核验；不代表账户权限、余额或下单一定成功。"
    return {"instId": inst_id, "environment": environment, "status": status,
            "can_open": status == "supported", "label": title, "message": message,
            "checked_at": checked_at, "market_data_source": "public_market",
            "indicator_source": environment}


def pool_support(instruments, environment, *, refresh=False):
    environment = _mode(environment)
    catalog = refresh_catalog(environment) if refresh else _read(environment)
    display_stale = False
    if not refresh:
        if catalog is None:
            catalog = _read(environment, max_age=DISPLAY_MAX_AGE)
            display_stale = catalog is not None
            _background(environment)
        elif time.time()-catalog['checked_at'] >= MAX_AGE/2:
            _background(environment)
    items = {item["instId"]: _status(item["instId"], environment, catalog) for item in instruments}
    if display_stale:
        for row in items.values():
            row.update(previous_status=row['status'], status='refreshing', can_open=False,
                       label=row['label']+' · 更新中',
                       message='沿用最近合约核验结果展示，后台刷新中；这不是新增交易授权。')
    return {"environment": environment, "checked_at": catalog["checked_at"] if catalog else None,
            "status": "refreshing" if display_stale else "verified" if catalog else "unknown", "items": items,
            "supported_count": sum(v["can_open"] for v in items.values()),
            "observation_count": sum(not v["can_open"] for v in items.values())}


def opening_status(inst_id, environment):
    return pool_support([{"instId": inst_id}], environment, refresh=True)["items"][inst_id]


def trading_universe(instruments, positions, environment):
    """Retain held instruments for management even when new exposure is blocked."""
    snapshot = pool_support(instruments, environment, refresh=True)
    held = {p.get("instId") for p in (positions or [])}
    return [i for i in instruments if snapshot["items"][i["instId"]]["can_open"] or i["instId"] in held], snapshot
