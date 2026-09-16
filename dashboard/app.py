"""
Web Dashboard Application Module
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any
from pathlib import Path
from scripts.okx_runtime import replace_cli_prefix as okx_private_command
from scripts.instrument_pool import load_instruments
from scripts.evolution_status import public_status as evolution_status
from scripts.memory_registry import public_view as memory_publication
from scripts.dashboard_stats import today_lifecycle_stats
import os
import json
import time
import datetime
import subprocess
import shutil
import asyncio
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = BASE_DIR
WORKSPACE_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")

LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
LOG_FILE = os.path.join(LOGS_DIR, "ai_factor_trader.log")
NEWS_SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
REVIEW_JOURNAL_FILE = os.path.join(DATA_DIR, "trade_review_journal.json")
REPORT_JSON_FILE = os.path.join(DATA_DIR, "self_improvement_report.json")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")
SNAPSHOTS_JSON_FILE = os.path.join(DATA_DIR, "snapshots.json")
STATE_JSON_FILE = os.path.join(DATA_DIR, "trading_state.json")
AI_DECISIONS_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
AI_HISTORY_FILE = os.path.join(DATA_DIR, "ai_brain_history.json")
AI_LAST_PROMPT_FILE = os.path.join(DATA_DIR, "ai_brain_last_prompt.txt")
FACTOR_LIBRARY_FILE = os.path.join(DATA_DIR, "factor_library_snapshot.json")
AI_MEMORY_MD_FILE = os.path.join(DATA_DIR, "AI_TRADING_MEMORY.md")
DASHBOARD_CACHE_FILE = os.path.join(DATA_DIR, "dashboard_last_good.json")


def get_target_instruments() -> list[dict[str, Any]]:
    return load_instruments()


TARGET_INSTRUMENTS = load_instruments()

@asynccontextmanager
async def lifespan(_: FastAPI):
    start_dashboard_background_worker()
    try:
        yield
    finally:
        stop_dashboard_background_worker()


app = FastAPI(title="OKXQuant AI Quantitative Matrix", docs_url=None, redoc_url=None, lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(DASHBOARD_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(DASHBOARD_DIR, "static")), name="static")

def run_json_cmd_status(cmd):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            return True, json.loads(res.stdout.strip()), ""
        return False, None, res.stderr.strip() or res.stdout.strip() or "empty response"
    except Exception as e:
        return False, None, str(e)


def read_account_resource(resource, environment, inst_id=""):
    if environment.configured or resource == "algos":
        from okxquant_backend.okx_read_service import read_private_resource
        try:
            return True, read_private_resource(resource, environment, inst_id), ""
        except Exception as exc:
            return False, None, str(exc)
    commands = {
        "balance": "okx account balance --json",
        "positions": "okx account positions --json",
        "orders": "okx swap orders --json",
        "bills": "okx account bills --limit 100 --json",
    }
    return run_json_cmd_status(okx_private_command(commands[resource]))


def run_json_cmd(cmd):
    ok, data, _ = run_json_cmd_status(cmd)
    return data if ok else None

def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_position_trackers():
    try:
        with open(POSITION_TRACKER_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def project_live_holding_rows(rows, positions, scope, captured_at):
    """One response uses one account position snapshot for every holding view.

    Do not rewrite settled history or persist estimates into the lifecycle ledger.
    Missing/replaced positions remain settlement work, never guessed live PnL.
    """
    by_key = {(str(p.get('instId')), str(p.get('posSide') or p.get('side'))): p for p in positions}
    result = []
    for raw in rows:
        row = dict(raw)
        if row.get('status') == 'holding' and row.get('environment_id') in (None, '', scope):
            side = {'多':'long','空':'short'}.get(row.get('side'),row.get('side'))
            live = by_key.get((str(row.get('instId') or str(row.get('inst',''))+'-USDT-SWAP'),side))
            if live and (not row.get('pos_id') or not live.get('posId') or str(row['pos_id'])==str(live['posId'])):
                row.update(margin=live.get('margin_usdt'), pnl=live.get('upl'), net_pnl=live.get('upl'),
                    gross_pnl=live.get('upl'), roi_pct=live.get('roi_pct'), roi=live.get('roi_pct'),
                    close_px=live.get('markPx'), sz=live.get('pos_sz'), open_px=live.get('avgPx'),
                    valuation_at=captured_at, valuation_source='shared_position_snapshot')
        result.append(row)
    return result


def enrich_position_risk_fields(positions, trackers=None):
    """Add margin and stop-line fields even when OKX protection lookup is unavailable."""
    trackers = trackers if isinstance(trackers, dict) else load_position_trackers()
    contract_values = {item.get("instId"): _safe_float(item.get("ctVal"), 1.0) for item in load_instruments()}
    for position in positions or []:
        inst_id = str(position.get("instId") or "")
        side = str(position.get("posSide") or position.get("side") or "net").lower()
        position["posSide"] = side
        tracker = trackers.get(f"{inst_id}_{side}", {})
        size = abs(_safe_float(position.get("pos_sz", position.get("pos"))))
        price = _safe_float(position.get("markPx")) or _safe_float(position.get("avgPx"))
        notional = abs(_safe_float(position.get("notional_usdt"))) or round(size * contract_values.get(inst_id, 1.0) * price, 2)
        leverage = abs(_safe_float(position.get("lever"), 1.0)) or 1.0
        exchange_margin = abs(_safe_float(position.get("imr")))
        existing_margin = abs(_safe_float(position.get("margin_usdt")))
        if exchange_margin > 0:
            margin = exchange_margin
            margin_source = "exchange_imr"
        elif existing_margin > 0:
            margin = existing_margin
            margin_source = str(position.get("marginSource") or "cached")
        else:
            margin = round(notional / leverage, 2) if notional > 0 else 0.0
            margin_source = "notional_div_leverage"
        exchange_stop = _safe_float(position.get("exchangeSl"))
        tracker_stop = _safe_float(position.get("trailingSl")) or _safe_float(tracker.get("trailingStopPx"))
        exchange_tp = _safe_float(position.get("exchangeTp"))
        tracker_tp = _safe_float(tracker.get("takeProfitPx"))
        position.update({
            "pos_sz": size,
            "notional_usdt": round(notional, 2),
            "margin_usdt": round(margin, 2) if margin > 0 else None,
            "marginSource": margin_source,
            "trailingSl": tracker_stop or None,
            "displayStop": exchange_stop or tracker_stop or None,
            "stopSource": "exchange_cloud" if exchange_stop else ("local_tracker" if tracker_stop else "unavailable"),
            "displayTakeProfit": exchange_tp or tracker_tp or None,
            "stageDesc": position.get("stageDesc") or tracker.get("stage_desc") or "持有监控中",
            "strategyTag": position.get("strategyTag") or tracker.get("strategy_tag") or ("多头持仓" if "long" in side else "空头持仓"),
            "cloudProtectionLastVerified": (tracker.get("cloudProtection") or {}).get("verifiedAt"),
            "cloudProtectionLastDetail": (tracker.get("cloudProtection") or {}).get("detail"),
        })
        if position.get("protectionStatus") in {None, "unknown_stale"} and position["cloudProtectionLastVerified"]:
            position["protectionStatus"] = "verification_stale"
    return positions


def _load_local_factor_library():
    """Load factor_library_snapshot.json — a local file independent of OKX private API."""
    if os.path.exists(FACTOR_LIBRARY_FILE):
        try:
            with open(FACTOR_LIBRARY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _read_horizon_stats():
    try:
        with open(os.path.join(DATA_DIR, "horizon_stats.json"), encoding="utf-8") as handle:
            value=json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _wait_state(state_data, factors, execution_profile):
    cycle=state_data if isinstance(state_data,dict) else {}
    counts=cycle.get("decision_cycle",{}).get("counts",{}) if isinstance(cycle.get("decision_cycle",{}),dict) else {}
    notices=cycle.get("environment_notices",[]) or []
    if any("AI" in str(x) and ("unavailable" in str(x).lower() or "\u672a\u5c31\u7eea" in str(x)) for x in notices):
        return {"code":"AI_UNAVAILABLE","detail":"\u6a21\u578b\u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u672c\u8f6e\u6ca1\u6709\u65b0\u7684\u53ef\u6267\u884c\u51b3\u7b56","next_trigger":"\u7b49\u5f85\u4e0b\u4e00\u8f6e\u6a21\u578b\u63a8\u7406\u6210\u529f"}
    if any("\u884c\u60c5" in str(x) or "account" in str(x).lower() for x in notices):
        return {"code":"DATA_UNAVAILABLE","detail":"\u884c\u60c5\u6216\u8d26\u6237\u8bc1\u636e\u4e0d\u5b8c\u6574","next_trigger":"\u7b49\u5f85\u65b0\u7684\u5b8c\u6574\u884c\u60c5\u548c\u8d26\u6237\u5feb\u7167"}
    if counts.get("execution_rejected",0):
        return {"code":"EXECUTION_REJECTED","detail":"\u5df2\u53d1\u73b0\u5019\u9009\uff0c\u4f46\u672a\u901a\u8fc7\u6700\u7ec8\u6267\u884c\u6838\u9a8c","next_trigger":"\u67e5\u770b\u62d2\u7edd\u539f\u56e0\uff0c\u4e0b\u4e00\u8f6e\u5c06\u91cd\u65b0\u8bc4\u4f30"}
    if counts.get("entry_candidate",0):
        return {"code":"CANDIDATE_REVIEW","detail":"\u5df2\u53d1\u73b0\u53ef\u6267\u884c\u5019\u9009\uff0c\u6b63\u5728\u8fdb\u884c\u8bc1\u636e\u548c\u98ce\u9669\u5ba1\u67e5","next_trigger":"\u7b49\u5f85\u8d26\u6237\u3001\u4ef7\u683c\u548c\u98ce\u9669\u9884\u68c0"}
    if counts.get("incomplete",0):
        return {"code":"AUDIT_INCOMPLETE","detail":"\u672c\u8f6e\u6a21\u578b\u8f93\u51fa\u4e0d\u5b8c\u6574\uff0c\u4e0d\u80fd\u6388\u6743\u65b0\u5efa\u4ed3\u4f4d","next_trigger":"\u7b49\u5f85\u5b8c\u6574\u4e14\u901a\u8fc7\u6821\u9a8c\u7684\u51b3\u7b56"}
    return {"code":"NO_PROGRAM_CANDIDATE","detail":"\u672c\u8f6e\u6536\u76d8 K \u7ebf\u6ca1\u6709\u5f62\u6210\u53ef\u6267\u884c\u5019\u9009\uff0c\u8fd9\u662f\u6b63\u5e38 WAIT\uff0c\u4e0d\u662f\u81ea\u52a8\u4ea4\u6613\u9501\u5b9a","next_trigger":"\u7b49\u5f85\u65b0\u7684\u6536\u76d8 K \u7ebf\u56de\u8e29\u3001\u7a81\u7834\u6216\u53cd\u8f6c\u5019\u9009"}

def _execution_profile_snapshot():
    try:
        from scripts.execution_profiles import runtime
        value=runtime(); execution=dict(value.get("execution", {}))
        # Standard keeps its original policy source; expose the effective values
        # so the dashboard never renders missing profile fields as question marks.
        from scripts.risk_policy import Policy
        policy=Policy()
        execution.setdefault("per_trade_equity_pct", policy.per_trade_equity_pct)
        execution.setdefault("single_asset_margin_usdt", policy.single_asset_margin_usdt)
        execution.setdefault("max_leverage", policy.max_leverage)
        execution.setdefault("daily_drawdown_pct", policy.daily_drawdown_pct)
        execution.setdefault("max_active_instruments", len(load_instruments()))
        execution.setdefault("total_margin_usdt", round(policy.single_asset_margin_usdt * max(1, min(3, len(load_instruments()))), 2))
        execution.setdefault("max_same_direction_positions", execution["max_active_instruments"])
        return {"profile_id": value.get("profile_id"), "execution": execution, "signature": value.get("signature")}
    except Exception as exc:
        return {"profile_id": "unknown", "execution": {}, "error": type(exc).__name__}

def _build_factors_from_local_files(positions, timestamp_full):
    """Build factors_list from trading_state.json + ai_brain_decisions.json.

    These local files do not depend on OKX private endpoints, so they are
    available even when the dashboard is in STALE/OFFLINE degraded mode.
    """
    factors_list = []
    pos_map = {p.get("instId"): p for p in positions} if isinstance(positions, list) else {}
    state_data = {}
    ai_decisions = {}
    factor_lib_map = {}
    active_pool = load_instruments()

    if os.path.exists(FACTOR_LIBRARY_FILE):
        try:
            with open(FACTOR_LIBRARY_FILE, "r", encoding="utf-8") as f_lib:
                lib_data = json.load(f_lib)
                for item in lib_data.get("instruments", []):
                    if isinstance(item, dict) and item.get("instId"):
                        factor_lib_map[item["instId"]] = item
        except Exception:
            pass

    if os.path.exists(AI_DECISIONS_FILE):
        try:
            with open(AI_DECISIONS_FILE, "r", encoding="utf-8") as f:
                ai_decisions = json.load(f)
        except Exception:
            pass

    inst_map = {}
    if os.path.exists(STATE_JSON_FILE):
        try:
            with open(STATE_JSON_FILE, "r", encoding="utf-8") as f:
                state_data = json.load(f)
                for ins in state_data.get("instruments", []):
                    if isinstance(ins, dict) and ins.get("instId"):
                        inst_map[ins["instId"]] = ins
        except Exception:
            pass

    for target in active_pool:
        inst_id = target.get("instId")
        ins = inst_map.get(inst_id) or {}
        lib_item = factor_lib_map.get(inst_id) or {}
        ai_info = ai_decisions.get(inst_id, {})
        ai_dec = ai_info.get("decision", {})
        ai_thought = ai_info.get("thought_process", {})
        action_val = ai_dec.get("action", ins.get("action", "WAIT"))
        confidence = ai_dec.get("confidence")
        reason = ai_dec.get("summary_reason", ins.get("desc", "新组合标的，雷达与量化特征已接入"))
        strategy_val = "🟢 建议做多" if action_val == "BUY_LONG" else ("🔴 建议做空" if action_val == "SELL_SHORT" else "⚪ AI观望")
        score_val = 2.5 if action_val == "BUY_LONG" else (-2.5 if action_val == "SELL_SHORT" else 0.0)
        m_struct = ai_thought.get("market_structure", f"{ins.get('market_regime', 'CHOP')} ({ins.get('trend_1h', '震荡')})")
        v_oi = ai_thought.get("volume_and_oi", f"OBV: {ins.get('obv_flow', 'NEUTRAL')}, 量能: {ins.get('vol_ratio', 1.0)}x")
        rr_ratio = ai_thought.get("risk_reward_evaluation", "盈亏比评估中")
        raw_t = ai_info.get("raw_ticker", {})
        chg_val = raw_t.get("chg24h") if raw_t.get("chg24h") is not None else lib_item.get("chg24h")
        price_val = ins.get("price") if ins.get("price") not in (None, "--") else lib_item.get("price", "--")
        rsi_val = ins.get("rsi") if ins.get("rsi") is not None else lib_item.get("trend_momentum", {}).get("rsi_14", 50.0)
        adx_val = ai_info.get("adx_1h") if ai_info.get("adx_1h") not in (None, "--") else lib_item.get("trend_momentum", {}).get("adx_1h", "--")
        sm_val = ai_info.get("smart_money") or lib_item.get("smart_money_derivatives", {})
        factors_list.append({
            "name": target.get("name") or ins.get("name"),
            "instId": inst_id,
            "position": pos_map.get(inst_id),
            "type": target.get("type", "crypto"),
            "price": price_val,
            "score": score_val,
            "chg24h": chg_val,
            "bidPx": raw_t.get("bidPx", ins.get("price", lib_item.get("microstructure", {}).get("bid_px", "--"))),
            "askPx": raw_t.get("askPx", ins.get("price", lib_item.get("microstructure", {}).get("ask_px", "--"))),
            "fundingRate": ai_info.get("raw_funding_rate") or (f"{lib_item.get('smart_money_derivatives', {}).get('funding_rate_pct', 0.0):.4f}%" if "funding_rate_pct" in lib_item.get("smart_money_derivatives", {}) else "--"),
            "oiUsd": ai_info.get("raw_oi") or lib_item.get("smart_money_derivatives", {}).get("oi_usd", "--"),
            "takerNetUsd": ai_info.get("raw_taker_vol") or lib_item.get("volume_money_flow", {}).get("taker_net_usd", "--"),
            "lsRatio": ai_info.get("raw_ls_ratio") or lib_item.get("smart_money_derivatives", {}).get("long_short_ratio", "--"),
            "rsi": rsi_val,
            "rsi_7": ins.get("rsi_7", 50.0),
            "vwap_bias": ins.get("vwap_bias", 0.0),
            "macd_hist": ins.get("macd_hist", 0.0),
            "macd_accel": ins.get("macd_accel", 0.0),
            "obv_flow": ins.get("obv_flow", lib_item.get("volume_money_flow", {}).get("obv_flow", "NEUTRAL")),
            "bb_bandwidth": ins.get("bb_bandwidth", lib_item.get("volatility_channel", {}).get("bb_width_1h", 0.0)),
            "vol_ratio": ins.get("vol_ratio", lib_item.get("volume_money_flow", {}).get("vol_ratio_15m", 1.0)),
            "trend_1h": ins.get("trend_1h", "震荡"),
            "trend_4h": ins.get("trend_4h", "震荡"),
            "market_regime": ins.get("market_regime", "CHOP"),
            "strategy_tag": strategy_val,
            "action": action_val,
            "decision_status": ai_dec.get("decision_status", "incomplete" if action_val == "WAIT" else "entry_candidate"),
            "horizon": ai_dec.get("horizon") or ai_dec.get("strategy_horizon") or ins.get("horizon") or "unknown",
            "strategy_type": ai_dec.get("strategy_type") or ai_dec.get("setup") or ins.get("strategy_tag") or "unknown",
            "regime": ins.get("market_regime") or ai_info.get("macro_assessment") or "unknown",
            "wait_reason": ai_dec.get("validation_reason") or ai_dec.get("summary_reason") or "",
            "confidence": confidence,
            "smart_money": sm_val,
            "adx_1h": adx_val,
            "leverage": ai_dec.get("leverage", 3),
            "margin_usdt": ai_dec.get("margin_usdt", 0.0),
            "entry_price": ai_dec.get("entry_price", 0.0),
            "take_profit_price": ai_dec.get("take_profit_price", 0.0),
            "stop_loss_price": ai_dec.get("stop_loss_price", 0.0),
            "risk_reward_ratio": ai_dec.get("risk_reward_ratio", "--"),
            "reason": reason,
            "market_structure": m_struct,
            "volume_and_oi": v_oi,
            "rr_ratio": rr_ratio,
            "thought_process": ai_thought,
            "desc": reason,
            "time_str": ai_info.get("time_str") or state_data.get("timestamp") or timestamp_full,
            "timestamp": ai_info.get("timestamp"),
        })
    return factors_list, state_data


def _inject_local_data_into_stale(stale, positions, timestamp_full):
    """Inject local-only data (factor library, factors, news, review) into a stale cache.

    When OKX private endpoints are unavailable, the dashboard enters STALE mode
    and returns the last-known-good snapshot. However, local files like
    factor_library_snapshot.json, trading_state.json, ai_brain_decisions.json,
    news_sentiment.json and the review report do NOT depend on OKX private API
    and should always reflect their latest on-disk state.
    """
    # Factor library — the source of calculus_dynamics, definite_integrals,
    # smart_money_derivatives, probability_theory, microstructure, etc.
    stale["factor_library"] = _load_local_factor_library()

    # Factors list — rebuilt from local trading_state + ai_brain_decisions
    factors_list, state_data = _build_factors_from_local_files(positions, timestamp_full)
    if factors_list:
        stale["factors"] = factors_list
        stale["state_snapshot"] = state_data

    # News intelligence — local file, no OKX dependency
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            with open(NEWS_SENTIMENT_FILE, "r", encoding="utf-8") as f:
                stale["news_intelligence"] = json.load(f)
        except Exception:
            pass

    # AI brain history — local file
    if os.path.exists(AI_HISTORY_FILE):
        try:
            with open(AI_HISTORY_FILE, "r", encoding="utf-8") as f:
                stale["ai_brain_history"] = json.load(f)
        except Exception:
            pass

    # Review report — local file
    if os.path.exists(REPORT_JSON_FILE):
        try:
            with open(REPORT_JSON_FILE, "r", encoding="utf-8") as f:
                stale["review"] = json.load(f)
        except Exception:
            pass

    # AI last prompt — local file
    if os.path.exists(AI_LAST_PROMPT_FILE):
        try:
            with open(AI_LAST_PROMPT_FILE, "r", encoding="utf-8") as f:
                stale["ai_last_prompt"] = f.read()
        except Exception:
            pass

    # AI trading memory — local file
    if os.path.exists(AI_MEMORY_MD_FILE):
        try:
            with open(AI_MEMORY_MD_FILE, "r", encoding="utf-8") as f:
                stale["ai_trading_memory_md"] = f.read()
        except Exception:
            pass

    # Log lines — local file
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                stale["logs"] = [l.strip() for l in lines[-60:] if l.strip()]
        except Exception:
            pass

    # Trades table and settled statistics remain locally refreshable even when
    # private account endpoints are temporarily stale/unavailable.
    local_ledger_rows = []
    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                local_ledger_rows = json.load(f)
                stale["trades"] = local_ledger_rows[:60]
        except Exception:
            pass
    try:
        from scripts.dashboard_stats import today_lifecycle_stats
        from okxquant_backend.account_baseline import load_account_baseline
        _today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")
        _reset = load_account_baseline().get("reset_time", "1970-01-01 00:00:00")
        _local_today = today_lifecycle_stats(local_ledger_rows, _today, _reset)
        _old_today = dict(stale.get("today_stats") or {})
        _local_today.pop("settled_rows", None)
        _local_today["total_pnl"] = _local_today["net_realized"] + _safe_float(_old_today.get("total_pnl"), 0.0) - _safe_float(_old_today.get("net_realized"), 0.0)
        stale["today_stats"] = {**_old_today, **_local_today, "source": "lifecycle_ledger_stale_account"}
    except Exception:
        pass

    from okxquant_backend.macro_status import fields as macro_fields
    from scripts import ledger_monitor, wait_audit, capital_pool, scenario_shadow, entry_opportunities
    from scripts.okx_runtime import selected_environment
    stale.update(macro_fields(DATA_DIR, history=stale.get('ai_brain_history', []), state=state_data))
    stale['trades']=ledger_monitor.project_rows(stale.get('trades', []), selected_environment().identity)
    stale['ledger_sync']=ledger_monitor.load('ledger_sync_status.json', {})
    from scripts.wait_audit import public_status as wait_status
    stale['evolution_review']=evolution_status(DATA_DIR)
    stale['memory_publication']=memory_publication(DATA_DIR,selected_environment().identity)
    stale['ai_trading_memory_md']=stale['memory_publication'].get('content','')
    stale['wait_audit']=wait_status(selected_environment().identity)
    stale['entry_opportunities']=entry_opportunities.public_status(selected_environment().identity)
    stale['decision_cycle']=state_data.get('decision_cycle', {})
    stale['capital_pool']=capital_pool.status(selected_environment())
    stale['scenario_shadow']=scenario_shadow.public_status(selected_environment().identity)
    return stale


def _is_meaningful_dashboard_snapshot(data):
    return isinstance(data, dict) and isinstance(data.get("account"), dict) and bool(data.get("account")) and "total_eq" in data["account"]


def load_persisted_dashboard_cache():
    try:
        with open(DASHBOARD_CACHE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if _is_meaningful_dashboard_snapshot(data) else {}
    except Exception:
        return {}


def persist_dashboard_cache(data):
    if not _is_meaningful_dashboard_snapshot(data):
        return
    cache_dir = os.path.dirname(DASHBOARD_CACHE_FILE) or DATA_DIR
    os.makedirs(cache_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".dashboard-cache-", suffix=".json", dir=cache_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, DASHBOARD_CACHE_FILE)
        os.chmod(DASHBOARD_CACHE_FILE, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


CACHE_DATA = load_persisted_dashboard_cache()
try:
    LAST_CACHE_TIME = os.path.getmtime(DASHBOARD_CACHE_FILE) if CACHE_DATA else 0
except OSError:
    LAST_CACHE_TIME = 0
CACHE_LOCK = None
CACHE_UPDATE_LOCK = threading.Lock()
SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dashboard_sync")
_BG_WORKER_THREAD = None
_BG_WORKER_RUNNING = False

def _dashboard_background_worker_loop():
    global _BG_WORKER_RUNNING
    # Initial small pause so server boots cleanly
    time.sleep(0.5)
    while _BG_WORKER_RUNNING:
        try:
            update_cache_cycle()
        except Exception:
            pass
        # Refresh every 2 seconds in background
        time.sleep(2.0)

def start_dashboard_background_worker():
    if os.getenv("OKXQUANT_TESTING") == "1":
        return
    global _BG_WORKER_THREAD, _BG_WORKER_RUNNING
    if _BG_WORKER_THREAD is None or not _BG_WORKER_THREAD.is_alive():
        _BG_WORKER_RUNNING = True
        _BG_WORKER_THREAD = threading.Thread(
            target=_dashboard_background_worker_loop,
            daemon=True,
            name="dashboard_cache_worker"
        )
        _BG_WORKER_THREAD.start()

def stop_dashboard_background_worker():
    global _BG_WORKER_RUNNING
    _BG_WORKER_RUNNING = False

def get_cache_lock():
    global CACHE_LOCK
    if CACHE_LOCK is None:
        CACHE_LOCK = asyncio.Lock()
    return CACHE_LOCK

def _refresh_owned_cache():
    try:
        _update_cache_cycle()
    finally:
        CACHE_UPDATE_LOCK.release()


def update_cache_cycle():
    """Only one cache producer may call upstream APIs at a time."""
    if not CACHE_UPDATE_LOCK.acquire(blocking=False):
        return False
    _refresh_owned_cache()
    return True


def request_cache_refresh():
    """Queue work without making an HTTP reader wait for exchange requests."""
    if os.getenv("OKXQUANT_TESTING") == "1" or not CACHE_UPDATE_LOCK.acquire(blocking=False):
        return False
    try:
        threading.Thread(target=_refresh_owned_cache, name="dashboard_refresh", daemon=True).start()
    except Exception:
        CACHE_UPDATE_LOCK.release()
        raise
    return True


def _update_cache_cycle():
    global CACHE_DATA, LAST_CACHE_TIME
    tz_beijing = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_beijing)
    today_bj_str = now_bj.strftime("%Y-%m-%d")
    timestamp_full = now_bj.strftime("%Y-%m-%d %H:%M:%S (北京时间)")

    source_errors = []

    # Freeze credentials/mode once for all reads in this update. API-key users
    # do not need the CLI for monitoring; OAuth-only installs retain that path.
    from scripts.okx_runtime import selected_environment
    environment = selected_environment()
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_bal = pool.submit(read_account_resource, "balance", environment)
        f_pos = pool.submit(read_account_resource, "positions", environment)
        f_ord = pool.submit(read_account_resource, "orders", environment)
        balance_ok, bal_data, balance_error = f_bal.result()
        positions_ok, pos_data, positions_error = f_pos.result()
        orders_ok, orders_data, orders_error = f_ord.result()

    if not balance_ok:
        source_errors.append(f"balance: {balance_error}")
        bal_data = []
    if not positions_ok:
        source_errors.append(f"positions: {positions_error}")
        pos_data = []
    if not orders_ok:
        source_errors.append(f"orders: {orders_error}")
        orders_data = []

    total_eq = 0.0
    avail_eq = 0.0
    cash_bal = 0.0
    upl_acc = 0.0

    if isinstance(bal_data, list) and bal_data:
        for d in bal_data[0].get("details", []):
            if d.get("ccy") == "USDT":
                total_eq = float(d.get("eq", 0.0) or 0.0)
                avail_eq = float(d.get("availBal", 0.0) or 0.0)
                cash_bal = float(d.get("cashBal", 0.0) or 0.0)
                upl_acc = float(d.get("upl", 0.0) or 0.0)
                break

    positions = []
    total_pos_upl = 0.0
    long_count = 0
    short_count = 0

    trackers = load_position_trackers()

    if isinstance(pos_data, list):
        for p in pos_data:
            pos_val = float(p.get("pos", 0.0) or 0.0)
            if pos_val == 0.0:
                continue

            pos_side = p.get("posSide", p.get("side", "")).lower()
            if "long" in pos_side:
                long_count += 1
            elif "short" in pos_side:
                short_count += 1

            upl = float(p.get("upl", 0.0) or 0.0)
            total_pos_upl += upl

            pos_key = f"{p.get('instId')}_{p.get('posSide', 'net')}"
            t_info = trackers.get(pos_key, {})
            trailing_sl = t_info.get("trailingStopPx", "--")
            stage_desc = t_info.get("stage_desc", "持有监控中")
            strategy_tag = t_info.get("strategy_tag") or ("🌊 低吸" if "long" in pos_side else "⚡ 高空")

            avg_px = float(p.get("avgPx", 0) or 0)
            mark_px = float(p.get("markPx", 0) or 0)
            pos_sz = float(p.get("pos", 0) or 0)

            ct_val = 1.0
            inst_id_val = p.get("instId", "")
            for target_item in load_instruments():
                if target_item["instId"] == inst_id_val:
                    ct_val = target_item.get("ctVal", 1.0)
                    break
            
            okx_notional = float(p.get("notionalUsd", 0) or 0)
            okx_imr = float(p.get("imr", 0) or 0)

            notional_usdt = round(okx_notional if okx_notional > 0 else (pos_sz * ct_val * (mark_px if mark_px > 0 else avg_px)), 2)
            raw_upl_ratio = float(p.get("uplRatio", 0.0) or 0.0)
            real_roi_pct = round(raw_upl_ratio * 100, 2)
            price_chg = round(((mark_px - avg_px) / avg_px * 100) if avg_px > 0 else 0, 2)

            lever_val = float(p.get("lever", "3") or 3.0)
            margin_usdt_val = round(okx_imr if okx_imr > 0 else (notional_usdt / lever_val), 2)

            positions.append({
                "instId": p.get("instId"),
                "name": p.get("instId", "").replace("-USDT-SWAP", ""),
                "posSide": pos_side,
                "side": pos_side,
                "pos": p.get("pos"),
                "posId": p.get("posId"),
                "pos_sz": pos_sz,
                "notional_usdt": notional_usdt,
                "margin_usdt": margin_usdt_val,
                "marginSource": "exchange_imr" if okx_imr > 0 else "notional_div_leverage",
                "imr": okx_imr or None,
                "lever": p.get("lever", "3"),
                "avgPx": avg_px,
                "markPx": mark_px,
                "upl": upl,
                "uplRatio": real_roi_pct,
                "roi_pct": real_roi_pct,
                "price_change_pct": price_chg,
                "liqPx": p.get("liqPx", "--"),
                "bePx": p.get("bePx", "--"),
                "trailingSl": trailing_sl,
                "stageDesc": stage_desc,
                "strategyTag": strategy_tag,
                "tp1Hit": t_info.get("tp1_hit", False),
                "tp2Hit": t_info.get("tp2_hit", False)
            })

    # Parse Pending Maker Orders
    pending_orders_list = []
    if isinstance(orders_data, list):
        for o in orders_data:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_beijing).strftime("%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            inst_clean = inst_id.replace("-USDT-SWAP", "").replace("-SWAP", "")
            side_raw = str(o.get("side", "")).lower()
            pos_side = str(o.get("posSide", "net")).lower()
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()
            raw_px = str(o.get("px") or "").strip()

            if reduce_only:
                if side_raw == "sell":
                    side_label = "市价平多" if ord_type == "market" else "限价平多"
                    is_long = False
                    side_color = "rose"
                else:
                    side_label = "市价平空" if ord_type == "market" else "限价平空"
                    is_long = True
                    side_color = "emerald"
            else:
                if side_raw == "buy":
                    side_label = "市价买多" if ord_type == "market" else "限价买多"
                    is_long = True
                    side_color = "emerald"
                else:
                    side_label = "市价卖空" if ord_type == "market" else "限价卖空"
                    is_long = False
                    side_color = "rose"

            if not raw_px or raw_px == "0":
                px_display = "市价" if ord_type == "market" else "--"
            else:
                try:
                    px_float = float(raw_px)
                    px_display = f"{px_float:g}"
                except ValueError:
                    px_display = raw_px
            
            attach_list = o.get("attachAlgoOrds", [])
            tp_px = "--"
            sl_px = "--"
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_px = str(att.get("tpTriggerPx") or "--")
                sl_px = str(att.get("slTriggerPx") or "--")

            pending_orders_list.append({
                "ordId": str(o.get("ordId", "")),
                "name": inst_clean,
                "inst": inst_clean,
                "instId": inst_id,
                "side": "buy" if side_raw == "buy" else "sell",
                "side_label": side_label,
                "side_raw": side_raw,
                "posSide": pos_side,
                "is_long": is_long,
                "side_color": side_color,
                "ord_type": ord_type,
                "lever": f"{o.get('lever', '3')}x",
                "px": px_display,
                "sz": str(o.get("sz", "--")),
                "cTime": str(o.get("cTime", "")),
                "time": c_time_str,
                "state": str(o.get("state", "live")),
                "tp_px": tp_px,
                "sl_px": sl_px
            })

    # A failed core account query must never overwrite last-known-good data with zeros.
    if not balance_ok or not positions_ok:
        if (_is_meaningful_dashboard_snapshot(CACHE_DATA)
                and CACHE_DATA.get("account_source_id") == environment.identity):
            stale = dict(CACHE_DATA)
            stale_positions = (stale.get("positions_summary") or {}).get("items", [])
            enrich_position_risk_fields(stale_positions, trackers)
            stale["data_health"] = {
                "status": "STALE",
                "partial": True,
                "errors": source_errors,
                "last_success_at": CACHE_DATA.get("timestamp"),
                "attempted_at": timestamp_full,
                "cache_age_seconds": max(0.0, round(time.time() - LAST_CACHE_TIME, 1)) if LAST_CACHE_TIME > 0 else None,
            }
            # Inject local-only data that does not depend on OKX private API.
            # factor_library, factors, news, review, logs, trades etc. are
            # read from local files and should always be fresh even in STALE mode.
            _inject_local_data_into_stale(stale, stale_positions, timestamp_full)
            CACHE_DATA = stale
            LAST_CACHE_TIME = time.time()
            return
        CACHE_DATA = {
            "timestamp": timestamp_full,
            "okx_environment": environment.mode,
            "data_health": {"status": "OFFLINE", "partial": True, "errors": source_errors},
            "account": {}, "today_stats": {}, "performance": {},
            "execution_profile": _execution_profile_snapshot(), "horizon_stats": _read_horizon_stats(),
            "wait_state": {"code": "DATA_UNAVAILABLE", "detail": "Market or account evidence is incomplete", "next_trigger": "Wait for a complete fresh snapshot"},
            "positions_summary": {"total": 0, "max_positions": _execution_profile_snapshot().get("execution", {}).get("max_active_instruments", len(load_instruments())), "items": []},
            "factors": [], "trades": [], "logs": [], "snapshots": [],
        }
        LAST_CACHE_TIME = time.time()
        return

    # One complete account snapshot per refresh; never N positions x 2 request types.
    if positions:
        algo_ok, account_algos, algo_error = read_account_resource("algos", environment)
        if not algo_ok:
            source_errors.append(f"algo verification unknown: {algo_error}")
        algo_results = {p["instId"]: (algo_ok, [o for o in (account_algos or []) if o.get("instId") == p["instId"]], algo_error) for p in positions}

        for position in positions:
            algo_ok, algo_orders, algo_error = algo_results.get(position["instId"], (False, [], "timeout"))
            if not algo_ok:
                position.update({"exchangeSl": None, "exchangeTp": None,
                                 "protectionStatus": "unknown_stale", "protectionCoveragePct": 0.0,
                                 "protectionAlgoId": ""})
                continue  # Unknown is not a confirmed absence of protection.

            matching_algos = [
                o for o in (algo_orders or [])
                if str(o.get("state", "live")).lower() in {"live", "effective"}
                and str(o.get("posSide", "net")).lower() in {position["posSide"], "net"}
                and str(o.get("reduceOnly", "true")).lower() in {"true", "1", "yes"}
            ]
            protected_size = sum(float(o.get("sz", 0) or 0) for o in matching_algos if o.get("slTriggerPx"))
            full_coverage = protected_size >= float(position["pos_sz"]) * 0.999
            live_algo = next((o for o in matching_algos if o.get("slTriggerPx") and o.get("tpTriggerPx")), None)
            if live_algo and full_coverage:
                position["exchangeSl"] = float(live_algo.get("slTriggerPx", 0) or 0)
                position["exchangeTp"] = float(live_algo.get("tpTriggerPx", 0) or 0)
                position["protectionStatus"] = "fully_protected"
                position["protectionCoveragePct"] = 100.0
                position["protectionAlgoId"] = live_algo.get("algoId", "")
            elif matching_algos:
                sl_algo = next((o for o in matching_algos if o.get("slTriggerPx")), {})
                position["exchangeSl"] = float(sl_algo.get("slTriggerPx", 0) or 0) or None
                position["exchangeTp"] = float(sl_algo.get("tpTriggerPx", 0) or 0) or None
                position["protectionStatus"] = "partially_protected"
                position["protectionCoveragePct"] = round(min(100.0, protected_size / max(position["pos_sz"], 1e-12) * 100), 1)
                position["protectionAlgoId"] = sl_algo.get("algoId", "")
            else:
                position["exchangeSl"] = None
                position["exchangeTp"] = None
                position["protectionStatus"] = "unprotected"
                position["protectionCoveragePct"] = 0.0
                position["protectionAlgoId"] = ""
    else:
        algo_results = {}

    enrich_position_risk_fields(positions, trackers)

    # Default seed capital is not a user-confirmed performance baseline.
    from okxquant_backend.account_baseline import load_account_baseline
    baseline = load_account_baseline()
    reset_time_str = baseline["reset_time"]
    initial_capital_val = baseline["initial_capital"]
    baseline_configured = baseline["baseline_configured"]

    # 4. Load Bills and Real Order-Level Ledger
    bills_ok, bills_data, bills_error = read_account_resource("bills", environment)
    if not bills_ok:
        source_errors.append(f"bills: {bills_error}")
        bills_data = []
    
    # Process Real Orders Aggregation (Minute + Inst + Action)
    orders_by_key = {}
    today_realized_gross = 0.0
    today_fees = 0.0
    cum_total_fees = 0.0
    today_funding = 0.0
    funding_history_list = []
    
    if isinstance(bills_data, list):
        for b in reversed(bills_data):
            ts = int(b.get("ts", 0) or 0) / 1000.0
            dt_bj = datetime.datetime.fromtimestamp(ts, tz=tz_beijing).strftime("%Y-%m-%d %H:%M:%S")
            if dt_bj < reset_time_str:
                continue

            sub_type = str(b.get("subType", ""))
            b_type = str(b.get("type", ""))
            inst = b.get("instId", "").replace("-USDT-SWAP", "")
            pnl = float(b.get("pnl", 0) or 0)
            fee = float(b.get("fee", 0) or 0)
            bal_chg = float(b.get("balChg", 0) or 0)
            sz = float(b.get("sz", 0) or 0)

            # Accumulate all trading fees (Cum & Today)
            cum_total_fees += fee
            if today_bj_str in dt_bj:
                today_fees += fee

            if b_type == "8" or sub_type in ["173", "174"]:
                funding_pnl = (bal_chg if bal_chg != 0 else pnl)
                if today_bj_str in dt_bj:
                    today_funding += funding_pnl
                funding_desc = "收取资金费 (+)" if sub_type == "174" or funding_pnl > 0 else "支付资金费 (-)"
                funding_history_list.append({
                    "time": dt_bj,
                    "inst": inst,
                    "type_desc": funding_desc,
                    "pnl": round(funding_pnl, 6),
                    "pos_sz": f"{sz} 张"
                })
                continue

            if sub_type in ["5", "6"]: # Closed order
                # Group by exact Minute + Inst + Close Action
                time_min = dt_bj[:16]
                agg_key = f"{time_min}_{inst}"
                if agg_key not in orders_by_key:
                    orders_by_key[agg_key] = {
                        "time": dt_bj,
                        "inst": inst,
                        "gross_pnl": 0.0,
                        "fee": 0.0,
                        "pnl": 0.0
                    }
                orders_by_key[agg_key]["gross_pnl"] += pnl
                orders_by_key[agg_key]["fee"] += fee
                orders_by_key[agg_key]["pnl"] += (pnl + fee)

    today_win_trades = 0
    today_loss_trades = 0
    all_win_trades = 0
    all_loss_trades = 0
    all_win_amt = 0.0
    all_loss_amt = 0.0
    by_inst = {}

    for agg_k, o in orders_by_key.items():
        net = o["pnl"]
        inst = o["inst"]
        t_time = o["time"]

        if inst not in by_inst:
            by_inst[inst] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        by_inst[inst]["trades"] += 1
        by_inst[inst]["pnl"] += net

        # Exclude friction dust / zero-margin test orders (< 0.01 USDT absolute PnL) from win/loss trade count
        if abs(net) < 0.01 and abs(o.get("gross_pnl", 0.0)) < 0.01:
            continue

        if net > 0:
            all_win_trades += 1
            all_win_amt += net
            by_inst[inst]["wins"] += 1
            if today_bj_str in t_time:
                today_win_trades += 1
        elif net < 0:
            all_loss_trades += 1
            all_loss_amt += abs(net)
            by_inst[inst]["losses"] += 1
            if today_bj_str in t_time:
                today_loss_trades += 1

        if today_bj_str in t_time:
            today_realized_gross += o["gross_pnl"]

    today_closed = today_win_trades + today_loss_trades
    today_win_rate = round((today_win_trades / today_closed) * 100, 1) if today_closed > 0 else 0.0

    all_closed = all_win_trades + all_loss_trades
    all_win_rate = round((all_win_trades / all_closed) * 100, 1) if all_closed > 0 else 0.0
    profit_factor = round((all_win_amt / all_loss_amt), 2) if all_loss_amt > 0 else (99.0 if all_win_amt > 0 else 0.0)
    avg_win = round(all_win_amt / all_win_trades, 2) if all_win_trades > 0 else 0.0
    avg_loss = round(all_loss_amt / all_loss_trades, 2) if all_loss_trades > 0 else 0.0

    # Strict Realized PnL strictly from settled trades + settled fundings (Fixed, not jumping with mark price)
    today_net_realized_pnl = round(today_realized_gross + today_fees + today_funding, 2)
    
    # Strict Total Cumulative Net PnL strictly from Equity vs Base Capital
    total_cum_net_pnl = round(total_eq - initial_capital_val, 2)
    cum_roi_pct = round((total_cum_net_pnl / initial_capital_val * 100) if initial_capital_val > 0 else 0.0, 2)
    total_cum_realized_pnl = round(total_cum_net_pnl - total_pos_upl, 2)

    inst_leaderboard = []
    for inst, s in by_inst.items():
        w_r = round((s["wins"] / s["trades"]) * 100, 1) if s["trades"] > 0 else 0.0
        inst_leaderboard.append({
            "inst": inst,
            "trades": s["trades"],
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": w_r,
            "pnl": round(s["pnl"], 2)
        })
    inst_leaderboard.sort(key=lambda x: x["pnl"], reverse=True)

    # 5. Load Log Lines
    log_lines = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                log_lines = [l.strip() for l in lines[-60:] if l.strip()]
        except Exception:
            pass

    # 6. Read Trading State & AI Brain LLM Decisions
    state_data = {}
    ai_decisions = {}
    if os.path.exists(AI_DECISIONS_FILE):
        try:
            with open(AI_DECISIONS_FILE, "r", encoding="utf-8") as f:
                ai_decisions = json.load(f)
        except Exception:
            pass

    factors_list = []
    pos_map = {p.get("instId"): p for p in positions} if isinstance(positions, list) else {}
    active_pool = load_instruments()
    inst_state_map = {}
    if os.path.exists(STATE_JSON_FILE):
        try:
            with open(STATE_JSON_FILE, "r", encoding="utf-8") as f:
                state_data = json.load(f)
                for ins in state_data.get("instruments", []):
                    if isinstance(ins, dict) and ins.get("instId"):
                        inst_state_map[ins["instId"]] = ins
        except Exception:
            pass

    factor_lib_map = {}
    if os.path.exists(FACTOR_LIBRARY_FILE):
        try:
            with open(FACTOR_LIBRARY_FILE, "r", encoding="utf-8") as f_lib:
                lib_data = json.load(f_lib)
                for item in lib_data.get("instruments", []):
                    if isinstance(item, dict) and item.get("instId"):
                        factor_lib_map[item["instId"]] = item
        except Exception:
            pass

    for target in active_pool:
        inst_id = target.get("instId")
        ins = inst_state_map.get(inst_id) or {}
        lib_item = factor_lib_map.get(inst_id) or {}
        ai_info = ai_decisions.get(inst_id, {})
        ai_dec = ai_info.get("decision", {})
        ai_thought = ai_info.get("thought_process", {})

        action_val = ai_dec.get("action", ins.get("action", "WAIT"))
        confidence = ai_dec.get("confidence")
        reason = ai_dec.get("summary_reason", ins.get("desc", "新组合标的，雷达与量化特征已接入"))

        strategy_val = "🟢 建议做多" if action_val == "BUY_LONG" else ("🔴 建议做空" if action_val == "SELL_SHORT" else "⚪ AI观望")
        score_val = 2.5 if action_val == "BUY_LONG" else (-2.5 if action_val == "SELL_SHORT" else 0.0)
        vwap_b = float(ins.get("vwap_bias", 0.0) or 0.0)

        m_struct = ai_thought.get("market_structure", f"{ins.get('market_regime', 'CHOP')} ({ins.get('trend_1h', '震荡')})")
        v_oi = ai_thought.get("volume_and_oi", f"OBV: {ins.get('obv_flow', 'NEUTRAL')}, 量能: {ins.get('vol_ratio', 1.0)}x")
        rr_ratio = ai_thought.get("risk_reward_evaluation", "盈亏比评估中")

        raw_t = ai_info.get("raw_ticker", {})
        funding_r = ai_info.get("raw_funding_rate") or (f"{lib_item.get('smart_money_derivatives', {}).get('funding_rate_pct', 0.0):.4f}%" if "funding_rate_pct" in lib_item.get("smart_money_derivatives", {}) else "--")
        oi_str = ai_info.get("raw_oi") or lib_item.get("smart_money_derivatives", {}).get("oi_usd", "--")
        taker_str = ai_info.get("raw_taker_vol") or lib_item.get("volume_money_flow", {}).get("taker_net_usd", "--")
        ls_str = ai_info.get("raw_ls_ratio") or lib_item.get("smart_money_derivatives", {}).get("long_short_ratio", "--")

        chg_val = raw_t.get("chg24h") if raw_t.get("chg24h") is not None else lib_item.get("chg24h")
        price_val = ins.get("price") if ins.get("price") not in (None, "--") else lib_item.get("price", "--")
        rsi_val = ins.get("rsi") if ins.get("rsi") is not None else lib_item.get("trend_momentum", {}).get("rsi_14", 50.0)
        adx_val = ai_info.get("adx_1h") if ai_info.get("adx_1h") not in (None, "--") else lib_item.get("trend_momentum", {}).get("adx_1h", "--")
        sm_val = ai_info.get("smart_money") or lib_item.get("smart_money_derivatives", {})

        factors_list.append({
            "name": target.get("name") or ins.get("name"),
            "instId": inst_id,
            "position": pos_map.get(inst_id),
            "type": target.get("type", "crypto"),
            "price": price_val,
            "score": score_val,
            "change24h": chg_val,
            "chg24h": chg_val,
            "bidPx": raw_t.get("bidPx", ins.get("price", lib_item.get("microstructure", {}).get("bid_px", "--"))),
            "askPx": raw_t.get("askPx", ins.get("price", lib_item.get("microstructure", {}).get("ask_px", "--"))),
            "fundingRate": funding_r,
            "oiUsd": oi_str,
            "takerNetUsd": taker_str,
            "lsRatio": ls_str,
            "rsi": rsi_val,
            "rsi_7": ins.get("rsi_7", 50.0),
            "vwap_bias": vwap_b,
            "macd_hist": ins.get("macd_hist", 0.0),
            "macd_accel": ins.get("macd_accel", 0.0),
            "obv_flow": ins.get("obv_flow", lib_item.get("volume_money_flow", {}).get("obv_flow", "NEUTRAL")),
            "bb_bandwidth": ins.get("bb_bandwidth", lib_item.get("volatility_channel", {}).get("bb_width_1h", 0.0)),
            "vol_ratio": ins.get("vol_ratio", lib_item.get("volume_money_flow", {}).get("vol_ratio_15m", 1.0)),
            "trend_1h": ins.get("trend_1h", "震荡"),
            "trend_4h": ins.get("trend_4h", "震荡"),
            "market_regime": ins.get("market_regime", "CHOP"),
            "strategy_tag": strategy_val,
            "action": action_val,
            "decision_status": ai_dec.get("decision_status", "incomplete" if action_val == "WAIT" else "entry_candidate"),
            "horizon": ai_dec.get("horizon") or ai_dec.get("strategy_horizon") or ins.get("horizon") or "unknown",
            "strategy_type": ai_dec.get("strategy_type") or ai_dec.get("setup") or ins.get("strategy_tag") or "unknown",
            "regime": ins.get("market_regime") or ai_info.get("macro_assessment") or "unknown",
            "wait_reason": ai_dec.get("validation_reason") or ai_dec.get("summary_reason") or "",
            "confidence": confidence,
            "smart_money": sm_val,
            "adx_1h": adx_val,
            "leverage": ai_dec.get("leverage", 3),
            "margin_usdt": ai_dec.get("margin_usdt", 0.0),
            "entry_price": ai_dec.get("entry_price", 0.0),
            "take_profit_price": ai_dec.get("take_profit_price", 0.0),
            "stop_loss_price": ai_dec.get("stop_loss_price", 0.0),
            "risk_reward_ratio": ai_dec.get("risk_reward_ratio", "--"),
            "reason": reason,
            "market_structure": m_struct,
            "volume_and_oi": v_oi,
            "rr_ratio": rr_ratio,
            "thought_process": ai_thought,
            "confluence_15m": m_struct,
            "confluence_1h": v_oi,
            "desc": reason,
            "ai_last_prompt": ai_info.get("ai_last_prompt", ""),
            "time_str": ai_info.get("time_str") or state_data.get("timestamp") or timestamp_full,
            "timestamp": ai_info.get("timestamp"),
        })

    # 7. Read the lifecycle ledger only. Sync belongs to the trading/daily
    # workers (which already call sync_full_ledger.py), not a monitoring refresh:
    # syncing can write the ledger and emit close notifications.
    ledger_trades = []

    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                ledger_trades = json.load(f)
        except Exception:
            pass
    
    # Filter lifecycle trades past reset_time
    valid_ledger_trades = []
    for t in ledger_trades:
        # Check either close_time or open_time >= reset_time
        c_time = str(t.get("close_time", ""))
        o_time = str(t.get("open_time", ""))
        t_time = str(t.get("time", ""))
        if (c_time and c_time >= reset_time_str) or (o_time and o_time >= reset_time_str) or (t_time and t_time >= reset_time_str) or t.get("status") == "holding":
            valid_ledger_trades.append(t)

    trades_table = valid_ledger_trades[:60]

    # Lifecycle ledger is the authoritative trade-count/PnL source. Bills remain
    # useful for funding and reconciliation, but recent-bill pagination cannot
    # represent one trade reliably when fills and fees are split across rows.
    lifecycle_today = today_lifecycle_stats(ledger_trades, today_bj_str, reset_time_str)
    if lifecycle_today['source'] == 'lifecycle_ledger':
        today_win_trades = lifecycle_today['win_trades']
        today_loss_trades = lifecycle_today['loss_trades']
        today_realized_gross = lifecycle_today['realized_gross']
        today_fees = lifecycle_today['fees_paid']
        today_funding = lifecycle_today['funding_paid']
        today_win_rate = lifecycle_today['win_rate']
        today_net_realized_pnl = lifecycle_today['net_realized']

    # 8. Read Review & Adaptive Config
    review_data = {}
    if os.path.exists(REPORT_JSON_FILE):
        try:
            with open(REPORT_JSON_FILE, "r", encoding="utf-8") as f:
                review_data = json.load(f)
        except Exception:
            pass

    adaptive_cfg = {}

    # 9. Read Snapshots
    snapshots_list = []
    if os.path.exists(SNAPSHOTS_JSON_FILE):
        try:
            with open(SNAPSHOTS_JSON_FILE, "r", encoding="utf-8") as f:
                snaps = json.load(f)
                if isinstance(snaps, list):
                    # Filter strictly >= reset_time
                    for s in snaps:
                        s_time = str(s.get("time", ""))
                        if s_time >= reset_time_str:
                            t_eq = float(s.get("total_eq", s.get("equity", initial_capital_val)) or initial_capital_val)
                            pnl_v = round(t_eq - initial_capital_val, 2)
                            roi_v = round((pnl_v / initial_capital_val * 100), 2)
                            snapshots_list.append({
                                "time": s_time,
                                "total_eq": round(t_eq, 2),
                                "pnl": pnl_v,
                                "roi": roi_v
                            })
                    snapshots_list = snapshots_list[-60:]
        except Exception:
            pass

    # Append live current point
    snapshots_list.append({
        "time": timestamp_full.replace(" (北京时间)", ""),
        "total_eq": round(total_eq, 2),
        "pnl": round(total_eq - initial_capital_val, 2),
        "roi": round((total_eq - initial_capital_val) / initial_capital_val * 100, 2)
    })

    # 10. Read News & AI Decisions History
    news_data = {}
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            with open(NEWS_SENTIMENT_FILE, "r", encoding="utf-8") as f:
                news_data = json.load(f)
        except Exception:
            pass

    ai_last_prompt_text = ""
    if os.path.exists(AI_LAST_PROMPT_FILE):
        try:
            with open(AI_LAST_PROMPT_FILE, "r", encoding="utf-8") as f:
                ai_last_prompt_text = f.read()
        except Exception:
            pass

    ai_history_list = []
    if os.path.exists(AI_HISTORY_FILE):
        try:
            with open(AI_HISTORY_FILE, "r", encoding="utf-8") as f:
                raw_history = json.load(f)
                # Keep up to 25 records and trim heavy repeated prompts in older history
                for idx, item in enumerate(raw_history[:25]):
                    c = dict(item)
                    if idx > 0 and "ai_last_prompt" in c and len(str(c["ai_last_prompt"])) > 500:
                        c["ai_last_prompt"] = str(c["ai_last_prompt"])[:200] + "...(历史已收敛)"
                    ai_history_list.append(c)
        except Exception:
            pass

    # Inject latest prompt into review payload if running under older worker
    if isinstance(review_data, dict):
        review_data["ai_last_prompt"] = ai_last_prompt_text

    factor_lib_snapshot = {}
    if os.path.exists(FACTOR_LIBRARY_FILE):
        try:
            with open(FACTOR_LIBRARY_FILE, "r", encoding="utf-8") as f:
                factor_lib_snapshot = json.load(f)
        except Exception:
            pass

    ai_memory_md_content = ""
    if os.path.exists(AI_MEMORY_MD_FILE):
        try:
            with open(AI_MEMORY_MD_FILE, "r", encoding="utf-8") as f:
                ai_memory_md_content = f.read()
        except Exception:
            pass

    ai_last_prompt_text = ""
    if os.path.exists(AI_LAST_PROMPT_FILE):
        try:
            with open(AI_LAST_PROMPT_FILE, "r", encoding="utf-8") as f:
                ai_last_prompt_text = f.read()
        except Exception:
            pass

    # System Disk info
    total_b, used_b, free_b = shutil.disk_usage("/")
    disk_free_gb = round(free_b / (1024 ** 3), 1)

    from okxquant_backend.macro_status import fields as macro_fields
    from scripts import ledger_monitor, wait_audit, capital_pool, scenario_shadow, entry_opportunities
    trades_table = ledger_monitor.project_rows(trades_table, environment.identity)
    published_memory=memory_publication(DATA_DIR,environment.identity)
    CACHE_DATA = {
        **macro_fields(DATA_DIR, ai_decisions, ai_history_list, state=state_data),
        "ledger_sync": ledger_monitor.load("ledger_sync_status.json", {}),
        "wait_audit": wait_audit.public_status(environment.identity),
        "entry_opportunities": entry_opportunities.public_status(environment.identity),
        "capital_pool": capital_pool.status(environment),
        "scenario_shadow": scenario_shadow.public_status(environment.identity),
        "decision_cycle": state_data.get("decision_cycle", {}),
        "execution_profile": _execution_profile_snapshot(),
        "horizon_stats": _read_horizon_stats(),
        "wait_state": _wait_state(state_data, factors_list, _execution_profile_snapshot()),
        "timestamp": timestamp_full,
        "okx_environment": environment.mode,
        "account_source_id": environment.identity,
        "date": today_bj_str,
        "data_health": {
            "status": "LIVE" if not source_errors else "PARTIAL",
            "partial": bool(source_errors),
            "errors": source_errors,
            "last_success_at": timestamp_full,
            "cache_age_seconds": 0,
            "timezone": "Asia/Shanghai",
            "bills_complete": False,
            "bills_coverage_note": "OKX latest 100 bills; NAV remains the cumulative equity source of truth"
        },
        "system": {
            "disk": {
                "free_gb": disk_free_gb
            }
        },
        "account": {
            "initial_capital": round(initial_capital_val, 2) if baseline_configured else None,
            "baseline_configured": baseline_configured,
            "total_eq": round(total_eq, 2),
            "avail_eq": round(avail_eq, 2),
            "cash_bal": round(cash_bal, 2),
            "upl": round(upl_acc, 2),
            "pos_upl_total": round(total_pos_upl, 2),
            "cum_realized_pnl": round(total_cum_realized_pnl, 2),
            "cum_net_pnl": round(total_cum_net_pnl, 2) if baseline_configured else None,
            "cum_roi_pct": cum_roi_pct if baseline_configured else None,
            "cum_total_fees": round(cum_total_fees, 2),
            "margin_usage_pct": round(((total_eq - avail_eq) / total_eq * 100) if total_eq > 0 else 0, 1)
        },
        "today_stats": {
            "realized_gross": round(today_realized_gross, 2),
            "fees_paid": round(today_fees, 2),
            "funding_paid": round(today_funding, 2),
            "net_realized": round(today_net_realized_pnl, 2),
            "total_pnl": round(today_net_realized_pnl + total_pos_upl, 2),
            "win_trades": today_win_trades,
            "loss_trades": today_loss_trades,
            "win_rate": today_win_rate,
            "closed_trades": today_win_trades + today_loss_trades,
            "breakeven_trades": lifecycle_today.get("breakeven_trades", 0),
            "source": lifecycle_today.get("source", "bills")
        },
        "performance": {
            "all_trades": all_closed,
            "win_trades": all_win_trades,
            "loss_trades": all_loss_trades,
            "win_rate": all_win_rate,
            "profit_factor": profit_factor,
            "total_win_amt": round(all_win_amt, 2),
            "total_loss_amt": round(all_loss_amt, 2),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "leaderboard": inst_leaderboard
        },
        "positions_summary": {
            "total": len(positions),
            "active_count": len(positions),
            "max": _execution_profile_snapshot().get("execution", {}).get("max_active_instruments", len(load_instruments())),
            "max_positions": _execution_profile_snapshot().get("execution", {}).get("max_active_instruments", len(load_instruments())),
            "long_count": long_count,
            "short_count": short_count,
            "total_upl": round(total_pos_upl, 2),
            "items": positions
        },
        "pending_orders": pending_orders_list,
        "factors": factors_list,
        "funding_settlements": {
            "total_funding_pnl": round(today_funding, 4),
            "items": sorted(funding_history_list, key=lambda x: x["time"], reverse=True)[:30]
        },
        "adaptive_config": adaptive_cfg,
        "review": review_data,
        "evolution_review": evolution_status(DATA_DIR),
        "ai_trading_memory_md": published_memory.get("content",""),
        "memory_publication": published_memory,
        "ai_last_prompt": ai_last_prompt_text,
        "snapshots": snapshots_list if baseline_configured else [],
        "state_snapshot": state_data,
        "logs": log_lines,
        "trades": trades_table,
        "news_intelligence": news_data,
        "ai_brain_history": ai_history_list,
        "factor_library": factor_lib_snapshot
    }
    try:
        from okxquant_backend.llm_manager import get_active_llm_runtime
        active_llm_info = get_active_llm_runtime()
        CACHE_DATA["llm_runtime"] = {
            "model": active_llm_info.get("model", "gemini-3.8-flash-high"),
            "provider_name": active_llm_info.get("provider_name", "默认"),
            "reasoning_effort": active_llm_info.get("reasoning_effort", "high"),
            "api_format": active_llm_info.get("api_format", "openai_chat"),
        }
    except Exception:
        CACHE_DATA["llm_runtime"] = {
            "model": os.getenv("LLM_MODEL", "gemini-3.8-flash-high"),
            "provider_name": "默认",
            "reasoning_effort": os.getenv("LLM_REASONING_EFFORT", "high"),
            "api_format": "openai_chat",
        }
    persist_dashboard_cache(CACHE_DATA)
    LAST_CACHE_TIME = time.time()

async def refresh_cache_if_needed(ttl_seconds: float = 3.0):
    global LAST_CACHE_TIME, CACHE_DATA
    if time.time() - LAST_CACHE_TIME <= ttl_seconds and CACHE_DATA:
        return CACHE_DATA
    lock = get_cache_lock()
    async with lock:
        if time.time() - LAST_CACHE_TIME <= ttl_seconds and CACHE_DATA:
            return CACHE_DATA
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(SYNC_EXECUTOR, update_cache_cycle)
        return CACHE_DATA

# Background work belongs to the ASGI lifespan, never module import.

VUE_DIST_DIR = os.path.join(WORKSPACE_DIR, "okxquant_frontend", "dist")
VUE_ASSETS_DIR = os.path.join(VUE_DIST_DIR, "assets")
DOCS_IMAGES_DIR = os.path.join(WORKSPACE_DIR, "docs", "images")


class CachedStaticFiles(StaticFiles):
    """Custom static files handler that injects Cloudflare/browser long-term caching headers."""
    def __init__(self, *args, cache_control: str = "public, max-age=31536000, immutable", **kwargs):
        self.cache_control = cache_control
        super().__init__(*args, **kwargs)

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = self.cache_control
        return resp


if os.path.isdir(VUE_ASSETS_DIR):
    app.mount("/assets", CachedStaticFiles(directory=VUE_ASSETS_DIR, cache_control="public, max-age=31536000, immutable"), name="vue_assets")

if os.path.isdir(DOCS_IMAGES_DIR):
    app.mount("/docs/images", CachedStaticFiles(directory=DOCS_IMAGES_DIR, cache_control="public, max-age=604800, stale-while-revalidate=86400"), name="docs_images")
    app.mount("/images", CachedStaticFiles(directory=DOCS_IMAGES_DIR, cache_control="public, max-age=604800, stale-while-revalidate=86400"), name="images")

VUE_ADMIN_DIST_DIR = VUE_DIST_DIR  # Same SPA build handles both / and /admin/*
VUE_ADMIN_LEGACY_FILE = os.path.join(VUE_DIST_DIR, "admin", "legacy.html")


def _vue_build_missing() -> HTMLResponse:
    return HTMLResponse(
        "Vue build not found (okxquant_frontend/dist/index.html). "
        "Run `npm ci` and `npm run build` in okxquant_frontend/.",
        status_code=503,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate, max-age=0"},
    )


def _serve_vue_spa(html_path: str, is_public: bool = True) -> HTMLResponse:
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    # HTML shell strictly never cached in browser or edge to ensure users always load latest Vite bundle immediately
    cache_header = "no-cache, no-store, must-revalidate, max-age=0"
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": cache_header,
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# --- SEO Endpoints: robots.txt, sitemap.xml, favicon.svg ---

@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    f = os.path.join(VUE_DIST_DIR, "robots.txt")
    if os.path.isfile(f):
        return FileResponse(f, media_type="text/plain", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    pf = os.path.join(WORKSPACE_DIR, "okxquant_frontend", "public", "robots.txt")
    if os.path.isfile(pf):
        return FileResponse(pf, media_type="text/plain", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    return PlainTextResponse("User-agent: *\nAllow: /\nAllow: /docs\nAllow: /images/\nDisallow: /admin/\nDisallow: /api/\nSitemap: https://trade.112102.xyz/sitemap.xml\n")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    f = os.path.join(VUE_DIST_DIR, "sitemap.xml")
    if os.path.isfile(f):
        return FileResponse(f, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    pf = os.path.join(WORKSPACE_DIR, "okxquant_frontend", "public", "sitemap.xml")
    if os.path.isfile(pf):
        return FileResponse(pf, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    return Response(content="""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://trade.112102.xyz/</loc><priority>1.0</priority></url><url><loc>https://trade.112102.xyz/docs</loc><priority>0.8</priority></url></urlset>""", media_type="application/xml")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    f = os.path.join(VUE_DIST_DIR, "favicon.svg")
    if os.path.isfile(f):
        return FileResponse(f, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=2592000, immutable"})
    pf = os.path.join(WORKSPACE_DIR, "okxquant_frontend", "public", "favicon.svg")
    if os.path.isfile(pf):
        return FileResponse(pf, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=2592000, immutable"})
    return Response(status_code=404)


# --- HTML Page Handlers ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    vue_index_file = os.path.join(VUE_DIST_DIR, "index.html")
    if os.path.isfile(vue_index_file):
        return _serve_vue_spa(vue_index_file, is_public=True)
    return _vue_build_missing()


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_spa_root(request: Request):
    """Serve the Vue SPA at /admin — the router handles sub-routes client-side."""
    vue_index_file = os.path.join(VUE_DIST_DIR, "index.html")
    if os.path.isfile(vue_index_file):
        return _serve_vue_spa(vue_index_file, is_public=False)
    return _vue_build_missing()


@app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
async def admin_spa_root_trailing(request: Request):
    return await admin_spa_root(request)


@app.get("/admin/{subpath:path}", response_class=HTMLResponse, include_in_schema=False)
async def admin_spa_deep_link(request: Request, subpath: str):
    """Vue Router history mode: any /admin/* deep link or refresh serves the SPA shell.
    Real files under dist/admin (e.g. legacy.html) keep priority."""
    admin_dir = Path(VUE_DIST_DIR, "admin").resolve()
    try:
        candidate = (admin_dir / subpath).resolve()
        if candidate.is_relative_to(admin_dir) and candidate.is_file():
            return FileResponse(str(candidate), headers={"Cache-Control": "private, no-cache, no-store, must-revalidate"})
    except (ValueError, OSError):
        pass
    return await admin_spa_root(request)


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
@app.get("/docs/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/docs/{subpath:path}", response_class=HTMLResponse, include_in_schema=False)
@app.get("/doc", response_class=HTMLResponse, include_in_schema=False)
async def docs_spa_root(request: Request, subpath: str = ""):
    """Serve the public system documentation page in Vue SPA."""
    vue_index_file = os.path.join(VUE_DIST_DIR, "index.html")
    if os.path.isfile(vue_index_file):
        return _serve_vue_spa(vue_index_file, is_public=True)
    return _vue_build_missing()


@app.get("/trading", response_class=HTMLResponse, include_in_schema=False)
@app.get("/factors", response_class=HTMLResponse, include_in_schema=False)
@app.get("/news", response_class=HTMLResponse, include_in_schema=False)
@app.get("/lab", response_class=HTMLResponse, include_in_schema=False)
@app.get("/history", response_class=HTMLResponse, include_in_schema=False)
async def public_tab_spa_routes(request: Request):
    """Serve the public Vue SPA shell for dedicated tab routes with Cloudflare edge caching."""
    vue_index_file = os.path.join(VUE_DIST_DIR, "index.html")
    if os.path.isfile(vue_index_file):
        return _serve_vue_spa(vue_index_file, is_public=True)
    return await index(request)


# --- Realtime Public Polling APIs (with Cloudflare Edge Micro-Caching) ---

def monitoring_snapshot():
    from scripts.okx_runtime import selected_environment
    environment = selected_environment()
    cached = CACHE_DATA
    age = max(0, time.time() - LAST_CACHE_TIME)
    matches = bool(cached and cached.get("account_source_id") == environment.identity)
    if not matches or age > 5:
        request_cache_refresh()
    if not matches:
        return {
            "timestamp": "", "okx_environment": environment.mode,
            "account_source_id": environment.identity,
            "initializing": True,
            "data_health": {"status": "OFFLINE", "partial": True, "errors": [], "refreshing": True},
            "account": {}, "positions_summary": {"items": [], "total": 0},
            "pending_orders": [], "factors": [], "trades": [], "logs": [],
        }
    # Copy only envelopes; never mutate the cached snapshot while serving it.
    data = dict(cached)
    data['trades'] = project_live_holding_rows(cached.get('trades') or [],
        (cached.get('positions_summary') or {}).get('items') or [], environment.identity, cached.get('timestamp'))
    from scripts.instrument_pool import load_instruments
    from scripts.instrument_support import pool_support
    data["instrument_support"] = pool_support(load_instruments(), environment.mode)
    health = dict(cached.get("data_health") or {})
    health["cache_age_seconds"] = round(age, 1)
    health["refreshing"] = CACHE_UPDATE_LOCK.locked()
    if age > 15:
        health.update({"status": "STALE", "partial": True})
    data["data_health"] = health
    return data


@app.get("/api/all")
async def get_all_data():
    return JSONResponse(
        monitoring_snapshot(),
        headers={"Cache-Control": "public, max-age=0, s-maxage=2, stale-while-revalidate=5"},
    )


@app.get("/api/overview")
async def get_overview():
    return JSONResponse(
        monitoring_snapshot(),
        headers={"Cache-Control": "public, max-age=1, s-maxage=3, stale-while-revalidate=5"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
