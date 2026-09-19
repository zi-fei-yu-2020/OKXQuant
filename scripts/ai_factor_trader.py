#!/usr/bin/env python3
"""
OKXQuant High-Alpha Quantitative Multi-Factor Trading Matrix & Execution Engine (OKXQuant v0.1.0)
Architecture:
1. Multi-Dimensional Quant Factor Sub-Engine:
   - Trend Momentum: EMA Slope (9/21/55), Multi-Timeframe Alignment (15M, 1H, 4H)
   - Volume & Price Dynamics: MACD Histogram Acceleration, OBV Flow Divergence, Volume Expansion Ratio
   - Mean Reversion & Volatility: Multi-Scale VWAP Bias, RSI 14/7 Dynamic Zones, Bollinger Bandwidth & Squeeze
   - Market Microstructure: Dynamic High/Low Dow Theory, Wick Absorption Geometry, Volatility Quantile (ATR%)
2. Continuous Non-Linear Alpha Scoring (-5.0 to +5.0 Score Distribution):
   - Dynamic weight synthesis across Momentum, Volume, Volatility, and Macro Sentiment
3. 6 Institutional Quant Setups:
   - 🌊 Institutional Pullback (顺势机构回踩)
   - 🚀 Momentum Squeeze Breakout (动量挤压突破)
   - 💎 Extreme Mean Reversion (极值均值回归)
   - ⚡ Resistance Exhaustion Short (阻力抛压做空)
   - 🌪️ Breakdown Acceleration Short (破位放量追空)
   - 🛡️ Liquidity Sweep Reversal (流动性猎杀反转)
4. Dynamic Adaptive Position Sizing, Volatility-Trailing Exits & Cooldown Protection.
"""

# Standalone scheduler children must not depend on an inherited PYTHONPATH.
import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[1])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)


import os
from okx_runtime import freeze_environment as freeze_okx_environment, replace_cli_prefix as okx_private_command, unfreeze_environment as unfreeze_okx_environment, selected_environment
import json
import time
import datetime
import math
import re
import subprocess
import urllib.request
import public_market as market
import instrument_support as support
import algo_reader
from scripts import trade_lock, risk_policy, entry_gateway, strategy_evidence, exit_policy
from scripts.execution_profiles import runtime as execution_runtime
import fcntl
from typing import Tuple, Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")

LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
LOG_FILE = os.path.join(LOGS_DIR, "ai_factor_trader.log")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")
STOP_COOLDOWN_FILE = os.path.join(DATA_DIR, "stop_cooldown.json")
CIRCUIT_BREAKER_FILE = os.path.join(DATA_DIR, "circuit_breaker.json")
NEWS_SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
AI_POSITION_MANAGEMENT_FILE = os.path.join(DATA_DIR, "ai_position_management.json")
TRADER_LOCK_FILE = os.path.join(DATA_DIR, ".ai_factor_trader.lock")
TRADER_SLOT_FILE = os.path.join(DATA_DIR, ".ai_factor_trader_slot.json")
HORIZON_INTENTS_FILE = os.path.join(DATA_DIR, 'horizon_intents.json')
HORIZON_STATS_FILE = os.path.join(DATA_DIR, 'horizon_stats.json')
CURRENT_HORIZON = 'unknown'

try:
    import sys
    sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
    from db_manager import record_trade_sqlite
    from qq_notifier import notify_trade_open, notify_trade_close
    from ai_brain_trader import execute_batch_ai_brain_cycle, get_latest_ai_decision, get_last_inference_error
except Exception:
    record_trade_sqlite = None
    notify_trade_open = None
    notify_trade_close = None
    execute_batch_ai_brain_cycle = None
    get_last_inference_error = lambda: "统一推理模块未加载"
    get_latest_ai_decision = None

from instrument_pool import load_instruments

TARGET_INSTRUMENTS = load_instruments()

ASSET_CLASS_PROFILES = {
    "commodity": {
        "entry_threshold": 2.2,
        "min_profit_ratio": 0.0075,
        "tp_atr_mult": 2.2,
        "sl_atr_mult": 1.3,
        "trailing_kick_in": 1.1,
        "trailing_pullback": 0.45
    },
    "index": {
        "entry_threshold": 2.2,
        "min_profit_ratio": 0.0065,
        "tp_atr_mult": 2.0,
        "sl_atr_mult": 1.2,
        "trailing_kick_in": 1.0,
        "trailing_pullback": 0.40
    },
    "stock": {
        "entry_threshold": 2.2,
        "min_profit_ratio": 0.0090,
        "tp_atr_mult": 2.3,
        "sl_atr_mult": 1.3,
        "trailing_kick_in": 1.2,
        "trailing_pullback": 0.50
    },
    "crypto": {
        "entry_threshold": 2.2,
        "min_profit_ratio": 0.0250,
        "tp_atr_mult": 3.5,
        "sl_atr_mult": 1.5,
        "trailing_kick_in": 2.5,
        "trailing_pullback": 1.2
    }
}

MAX_CONCURRENT_POSITIONS = len(TARGET_INSTRUMENTS)
MAX_SAME_DIRECTION_POSITIONS = 6
LAST_ENTRY_PLAN = {}
TAKER_FEE_RATE = 0.0005
MAKER_FEE_RATE = 0.0002 # Limit Order Maker Fee (60% Lower Than Market Taker)
MAX_DAILY_LOSS_USDT = 75.0

# 🚀 Pyramiding Scale-In Hard Risk Gateways (顺势浮盈金字塔加仓风控硬门禁)
MAX_SINGLE_ASSET_MARGIN = 600.0   # 单标的最大累计占用保证金上限 (USDT)
MAX_SCALE_IN_COUNT = 1            # 单标的最大顺势加仓次数 (底仓+最多1次顺势追加)
MIN_SCALE_IN_PROFIT_RATIO = 0.008 # 允许顺势加仓的最小底仓浮盈率 (+0.8%)

def _exit_preset(tracker, executed_actions, name):
    """Keep last verified selection across restarts; warn once per degraded state."""
    previous = tracker.get('exitPolicyStatus')
    settings, status = exit_policy.resolve(tracker, execution_runtime)
    if status['source'] != 'active_profile' and status != previous:
        source = '沿用最近核验预设' if status['source'] == 'last_verified' else '采用保守保护参数'
        executed_actions.append(f"[{name}] 退出预设读取异常({status['error_type']})，{source} {status['preset_id']}；硬止损与云端保护继续")
    return settings


def is_tradfi_market_liquid(asset_type: str) -> bool:
    """Strict US Regular Trading Window (BJ 21:30 ~ 次日 04:00)"""
    if asset_type in ["crypto", "commodity"]:
        return True
    
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    weekday = now_bj.weekday()
    hour = now_bj.hour
    minute = now_bj.minute

    # Weekend check
    if weekday == 5 and hour >= 5: return False
    if weekday == 6: return False
    if weekday == 0 and (hour < 21 or (hour == 21 and minute < 30)): return False

    # Mon-Fri Core Hours
    if (hour == 21 and minute >= 30) or (hour >= 22) or (hour < 4):
        return True
    return False

def run_cmd_result(cmd, timeout=15):
    """Return process metadata; callers must inspect returncode before mutating local state."""
    try:
        from okxquant_backend.account_connections import assert_current
        assert_current(market._selected())
        with algo_reader.command_barrier(cmd, market._selected()):
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        parsed = None
        if res.stdout.strip():
            try:
                parsed = json.loads(res.stdout.strip())
            except json.JSONDecodeError:
                pass
        business_ok = True
        if isinstance(parsed, dict) and "code" in parsed:
            business_ok = str(parsed.get("code")) == "0"
        elif isinstance(parsed, list):
            status_rows = [row for row in parsed if isinstance(row, dict) and "sCode" in row]
            if status_rows:
                business_ok = all(str(row.get("sCode")) == "0" for row in status_rows)
        return {
            "ok": res.returncode == 0 and business_ok,
            "returncode": res.returncode,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
            "data": parsed,
        }
    except Exception as e:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(e), "data": None, "error_type": type(e).__name__}


def run_cmd(cmd, timeout=15):
    result = run_cmd_result(cmd, timeout)
    return result["stdout"] if result["ok"] else f"Error: {result['stderr'] or result['stdout']}"

def run_json_cmd(cmd, timeout=15):
    try:
        with algo_reader.command_barrier(cmd, market._selected()):
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip())
        return None
    except Exception:
        return None

def fetch_candles_direct(inst_id: str, bar: str = "15m", limit: int = 45):
    """Public REST + bounded shared cache; never fall back to spawning Node."""
    try:
        return market.candles(inst_id, bar, limit)
    except Exception:
        return []

def fetch_signal_candles(inst_id, bar, limit):
    try: return market.signal_candles(inst_id, bar, limit)
    except Exception: return []


def load_horizon_intents():
    try:
        with open(HORIZON_INTENTS_FILE, encoding='utf-8') as f: return json.load(f)
    except Exception: return {}

def _save_horizon_intents(data):
    import tempfile
    parent=os.path.dirname(HORIZON_INTENTS_FILE)
    os.makedirs(parent,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.entry-context-',suffix='.json',dir=parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(data,handle,ensure_ascii=False,allow_nan=False)
            handle.flush();os.fsync(handle.fileno())
        os.replace(tmp,HORIZON_INTENTS_FILE)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def save_horizon_intent(inst_id, side, horizon, decision_id=None, strategy_mode=None, setup=None, entry_context=None):
    data=load_horizon_intents(); from scripts.strategy_modes import mode_for
    mode=strategy_mode or mode_for(horizon)
    data[f'{inst_id}_{side}']={'horizon': horizon if horizon in {'scalp','swing'} else 'swing','mode_version':mode.get('version','strategy-modes-v2'),'mode_signature':mode.get('signature',''),'entry_timeframe':mode['entry_timeframe'],'confirmation_timeframe':mode['confirmation_timeframe'],'bias_timeframe':mode['bias_timeframe'],'max_holding_seconds':mode['max_holding_seconds'],'mode':mode,'setup':setup,'entry_context':entry_context,'decision_id':decision_id,'ts':int(time.time())}
    _save_horizon_intents(data)

def consume_horizon_intent(inst_id, side):
    data=load_horizon_intents(); key=f'{inst_id}_{side}'; item=data.pop(key,None)
    if item:
        try:
            _save_horizon_intents(data)
        except Exception: pass
    return item.get('horizon','swing') if isinstance(item,dict) else 'swing'

def load_trackers():
    if os.path.exists(POSITION_TRACKER_FILE):
        try:
            with open(POSITION_TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_trackers(trackers):
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='.position-trackers-',suffix='.tmp',dir=DATA_DIR)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(trackers,handle,ensure_ascii=False,indent=2,allow_nan=False)
            handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,POSITION_TRACKER_FILE)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def load_stop_cooldowns():
    if os.path.exists(STOP_COOLDOWN_FILE):
        try:
            with open(STOP_COOLDOWN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def add_stop_cooldown(inst_id: str, side: str, reason: str = "止损冷却"):
    cooldowns = load_stop_cooldowns()
    key = f"{inst_id}_{side}"
    previous = cooldowns.get(key) if isinstance(cooldowns.get(key), dict) else {}
    if previous.get('reason')=='setup结构失效':previous={}
    count = int(previous.get('count', 0) or 0) + 1
    cooldowns[key] = {
        "instId": inst_id,
        "side": side,
        "ts": int(time.time()),
        "reason": reason,
        "count": count,
        "cooldown_seconds": 7200 if count >= 2 else 1800,
    }
    try:
        with open(STOP_COOLDOWN_FILE, "w", encoding="utf-8") as f:
            json.dump(cooldowns, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def is_in_stop_cooldown(inst_id: str, side: str) -> bool:
    cooldowns = load_stop_cooldowns()
    key = f"{inst_id}_{side}"
    if key in cooldowns:
        if cooldowns[key].get('reason')=='setup结构失效':return False
        seconds = int(cooldowns[key].get('cooldown_seconds', 1800) or 1800)
        rem_sec = seconds - (int(time.time()) - int(cooldowns[key].get("ts", 0) or 0))
        if rem_sec > 0:
            return True
    return False

def clamp(value, lower, upper, default):
    try:
        return max(lower, min(upper, float(value)))
    except (TypeError, ValueError):
        return default


def load_adaptive_config():
    """Fallback config reader maintaining compatibility."""
    return {}

def execution_limits():
    """Resolve portfolio limits from the active execution binding.

    The old trader loop kept 6 slots/600U per asset even when the 300U
    profile was active; the gateway later clipped the order, but the strategy
    still over-traded. Keep planning and final authorization on one budget.
    """
    try:
        cfg=execution_runtime()['execution']
    except Exception:
        cfg={}
    return {
        'max_positions': int(cfg.get('max_active_instruments', MAX_CONCURRENT_POSITIONS)),
        'max_same_direction': int(cfg.get('max_same_direction_positions', cfg.get('max_active_instruments', MAX_SAME_DIRECTION_POSITIONS))),
        'single_asset_margin': float(cfg.get('single_asset_margin_usdt', MAX_SINGLE_ASSET_MARGIN)),
    }

def clean_stale_open_orders() -> Tuple[bool, str]:
    """Cancel stale entry orders; any inability to verify/cancel blocks the trading cycle."""
    result = run_cmd_result(okx_private_command("okx swap orders --json"), timeout=20)
    if not result["ok"] or not isinstance(result.get("data"), list):
        return False, result["stderr"] or result["stdout"] or "invalid open-orders response"
    now_ts = int(time.time() * 1000)
    for order in result["data"]:
        inst_id = str(order.get("instId") or "")
        order_id = str(order.get("ordId") or "")
        state = str(order.get("state", "live")).lower()
        created_at = int(order.get("cTime", now_ts) or now_ts)
        if state not in {"live", "partially_filled"} or not order_id or now_ts - created_at <= 240000:
            continue
        canceled = run_cmd_result(okx_private_command(f"okx swap cancel {inst_id} --ordId {order_id} --json"), timeout=20)
        if not canceled["ok"]:
            return False, f"failed to cancel stale order {inst_id}/{order_id}: {canceled['stderr'] or canceled['stdout']}"
        print(f"[挂单生命周期管理] 自动撤销超时挂单: {inst_id} (ordId={order_id}, state={state})")
    return True, "open orders verified"

def check_black_swan_sentinel() -> Tuple[bool, str]:
    """Minute-level Black Swan Sentinel: Checks for BTC 5M extreme flash-crash or catastrophic news"""
    try:
        # Check BTC 5M candles for extreme plunge (> 3.0% in 15 mins)
        candles = fetch_candles_direct("BTC-USDT-SWAP", "15m", 3)
        if candles and len(candles) >= 2:
            latest_c = candles[0]
            c_open = float(latest_c[1])
            c_close = float(latest_c[4])
            c_low = float(latest_c[3])
            drop_pct = (c_close - c_open) / c_open * 100.0
            if drop_pct <= -3.0 or ((c_low - c_open) / c_open * 100.0 <= -4.0):
                return True, f"🚨 监测到 BTC 15M 级别发生断崖式暴跌插针 ({drop_pct:.2f}%)，触发全网黑天鹅紧急熔断！"
    except Exception:
        pass

    # Check news sentiment file
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            with open(NEWS_SENTIMENT_FILE, "r", encoding="utf-8") as f:
                n_data = json.load(f)
                score = float(n_data.get("overall_score", 50.0))
                if score <= 20.0:
                    return True, f"🚨 监测到突发黑天鹅极度恶性利空舆情 (情绪指数: {score:.1f})，触发全网黑天鹅紧急熔断！"
        except Exception:
            pass

    return False, ""

def is_circuit_breaker_active():
    # 1. Black Swan Sentinel Check
    bs_active, bs_reason = check_black_swan_sentinel()
    if bs_active:
        return True, bs_reason

    # 2. File-based Circuit Breaker Check (shared schema with news harvester)
    if os.path.exists(CIRCUIT_BREAKER_FILE):
        try:
            with open(CIRCUIT_BREAKER_FILE, "r", encoding="utf-8") as f:
                cb = json.load(f)
            expires_at = float(cb.get("expires_at_ts", 0) or 0)
            active = bool(cb.get("active")) or cb.get("status") == "triggered"
            if active and (expires_at <= 0 or time.time() < expires_at):
                return True, cb.get("reason") or cb.get("headline") or "黑天鹅极端行情熔断中"
        except Exception as e:
            return True, f"熔断状态文件损坏，安全暂停开仓: {e}"

    # 3. Daily ledger loss gate uses the same settled lifecycle rows shown in the dashboard.
    try:
        from scripts.risk_policy import ledger_daily_drawdown, load_policy
        ledger_gate=ledger_daily_drawdown(load_policy())
        if ledger_gate.get('blocked'):
            return True, f"日内账本亏损熔断: {ledger_gate['net_pnl']:.2f}U / 阈值 {ledger_gate['threshold']:.2%}"
    except Exception as exc:
        return True, f'日内亏损风控数据读取失败，安全暂停开仓: {type(exc).__name__}'

    # 4. Daily/peak loss gate uses the authoritative reconciled equity state.
    try:
        from scripts import strategy_evidence
        from scripts.okx_runtime import selected_environment
        env = selected_environment()
        with strategy_evidence.connection() as db:
            row = db.execute('SELECT payload FROM equity_state WHERE scope=?', (env.identity,)).fetchone()
        if row:
            state = json.loads(row[0])
            if state.get('blocked'):
                return True, f"权威权益状态触发亏损熔断: daily={float(state.get('daily_drawdown', 0)):.2%}, peak={float(state.get('peak_drawdown', 0)):.2%}"
    except Exception as e:
        return True, f"权威权益风控数据读取失败，安全暂停开仓: {e}"

    return False, ""

def query_positions(timeout=20) -> Tuple[bool, List[Dict[str, Any]], str]:
    """Distinguish an exchange-confirmed empty account from a failed query."""
    result = run_cmd_result(okx_private_command("okx account positions --json"), timeout=timeout)
    if not result["ok"] or not isinstance(result.get("data"), list):
        return False, [], result["stderr"] or result["stdout"] or "invalid positions response"
    return True, result["data"], ""


def close_position_confirmed(inst_id: str, pos_side: str, before_size: float, *, exit_reason='strategy_close', position=None) -> Tuple[bool, str]:
    """Close a position and verify at the exchange before changing local state."""
    env=market._selected()
    if getattr(env,"configured",False) and position and position.get("posId") and position.get("cTime"):
        from scripts.close_execution import close
        return close(env,inst_id,pos_side,before_size,position,exit_reason)
    # Pre-cancel any conflicting pending/reduce-only orders for this instrument to release available size
    try:
        ord_res = run_cmd_result(okx_private_command(f"okx swap orders --instId {inst_id} --json"), timeout=10)
        if ord_res.get("ok") and isinstance(ord_res.get("data"), list):
            for o in ord_res["data"]:
                o_side = str(o.get("posSide", "net")).lower()
                if o_side in (pos_side.lower(), "net"):
                    o_id = str(o.get("ordId") or "")
                    if o_id:
                        run_cmd_result(okx_private_command(f"okx swap cancel {inst_id} --ordId {o_id} --json"), timeout=10)
    except Exception as e:
        print(f"[Close Pre-Clean] Warning cancelling pending orders for {inst_id}: {e}")

    close_started = time.time()
    import uuid
    attempt_id = uuid.uuid4().hex
    transport_code = None
    def journal(status, response):
        try:
            from scripts.close_evidence import record_close
            record_close(market._selected(),inst_id=inst_id,side=pos_side,size=before_size,
                started_at=close_started,confirmed_at=time.time(),reason=exit_reason,position=position,
                result=response,status=status,attempt_id=attempt_id,transport_code=transport_code)
        except Exception:
            pass  # Never retry or suppress a protective close because audit storage failed.
    journal('submitted', None)
    result = run_cmd_result(
        okx_private_command(f"okx swap close --instId {inst_id} --mgnMode cross --posSide {pos_side} --autoCxl --json"),
        timeout=20,
    )
    accepted = bool(result.get('ok'))
    if not accepted:
        code = re.search(r'(?:Code:|OKX|HTTP)\s*(\d{3,6})', str(result.get('stderr') or result.get('stdout') or ''))
        transport_code = code.group(1) if code else 'unconfirmed_transport'
    journal('accepted' if accepted else 'unconfirmed', result.get('data'))
    saw_successful_query = False
    deadline = time.monotonic() + 8
    for _ in range(6):
        remaining_budget = deadline - time.monotonic()
        if remaining_budget <= .6:
            break
        time.sleep(0.6)
        query_ok, positions, query_error = query_positions(timeout=min(2., max(.1, deadline - time.monotonic())))
        if not query_ok:
            continue
        saw_successful_query = True
        remaining = 0.0
        for current_position in positions:
            if current_position.get("instId") == inst_id and str(current_position.get("posSide", "net")).lower() == pos_side:
                remaining = abs(float(current_position.get("pos", 0) or 0))
                break
        if remaining < max(1e-12, abs(before_size) * 0.001):
            journal('confirmed' if accepted else 'flat_observed',result.get('data'))
            return True, ('exchange position closed' if accepted else 'position flat after uncertain close response; execution source requires order evidence')
    journal('unconfirmed', result.get('data'))
    if not saw_successful_query:
        return False, "position verification failed: no successful exchange response; no write retry"
    return False, f"exchange still reports an open position after one close request (before={before_size}); no write retry"


def reconcile_scale_trackers(trackers: Dict[str, Any], real_pos_dict: Dict[str, Any]) -> None:
    """Reconcile scale reservations from exchange order fills, once per fill delta.

    ACKs are not fills: unresolved reads leave the reservation in place and block
    another scale attempt rather than guessing or incrementing local state.
    """
    result = run_cmd_result(okx_private_command("okx swap orders --history --limit 100 --json"), timeout=20)
    if not result.get("ok") or not isinstance(result.get("data"), list):
        return
    for key, tracker in trackers.items():
        if not isinstance(tracker, dict):
            continue
        pending = tracker.get("pending_scale_orders") or []
        if not isinstance(pending, list):
            continue
        side = key.rsplit("_", 1)[-1]
        inst_id = key.rsplit("_", 1)[0]
        position = real_pos_dict.get(inst_id)
        if not position or str(position.get("side", position.get("posSide", ""))).lower() != side:
            continue
        position_binding = {str(position.get(field) or "") for field in ("posId", "cTime")}
        remaining = []
        changed = False
        for item in pending:
            if not isinstance(item, dict) or not item.get("order_id"):
                remaining.append(item); continue
            row = next((o for o in result["data"] if str(o.get("ordId")) == str(item["order_id"])), None)
            if row is None:
                remaining.append(item); continue
            state = str(row.get("state") or "").lower()
            if state in {"live", "partially_filled"}:
                remaining.append(item); continue
            if state not in {"filled", "canceled", "mmp_canceled"}:
                remaining.append(item); continue
            filled = abs(float(row.get("accFillSz") or 0))
            counted = abs(float(item.get("counted_fill") or 0))
            delta = max(0.0, filled - counted)
            if delta > 0 and position_binding:
                tracker["scale_count"] = int(tracker.get("scale_count", 0)) + 1
                item["counted_fill"] = filled
                item["position_binding"] = sorted(position_binding)
                changed = True
            if filled < abs(float(item.get("requested_size") or 0)) and state not in {"canceled", "mmp_canceled"}:
                remaining.append(item)
            elif delta <= 0 and state == "filled":
                remaining.append(item)
        tracker["pending_scale_orders"] = remaining
        if changed:
            tracker["last_scale_reconciled_at"] = time.time()


def prune_trackers(trackers: Dict[str, Any], real_pos_dict: Dict[str, Any]) -> int:
    """Remove stale/non-universe trackers while preserving every live exchange position."""
    valid_keys = {
        f"{inst_id}_{str(position.get('posSide', 'net')).lower()}"
        for inst_id, position in real_pos_dict.items()
        if float(position.get("pos", 0) or 0) > 0
    }
    removed = 0
    for key in list(trackers):
        if key not in valid_keys:
            trackers.pop(key, None)
            removed += 1
    return removed


def record_entry_rejection(inst_id, ai_info, reason, **details):
    """Persist why a selected AI entry did not reach entry_gateway."""
    try:
        strategy_evidence.best_effort(market._selected().identity, 'entry_rejection', {
            'instrument': inst_id, 'decision_id': (ai_info or {}).get('decision_id'),
            'candidate_id': ((ai_info or {}).get('decision') or {}).get('candidate_id'),
            'action': ((ai_info or {}).get('decision') or {}).get('action'),
            'reason': reason, 'details': details, 'at': time.time(),
        })
    except Exception:
        pass

@trade_lock.serialized
def submit_protected_limit_order(inst_id: str, side: str, pos_side: str, size: float, price: float, tp_px: float, sl_px: float, *, risk_budget_usdt=15, decision_id=None, decision_at=None, allow_demo_translation=True, horizon='swing', setup=None) -> Tuple[bool, str]:
    """Submit a protected limit order; acceptance is not treated as a fill."""
    # Check if we are running in simulated/demo mode and price diverged significantly from demo orderbook
    env = market._selected()
    availability = support.opening_status(inst_id, env.mode)
    if not availability["can_open"]:
        reason = str(availability.get("message") or "opening_environment_unavailable")
        strategy_evidence.best_effort(getattr(env,'identity','unknown'), 'entry_rejection', {'instrument': inst_id, 'decision_id': decision_id, 'candidate_id': None, 'action': 'BUY_LONG' if pos_side == 'long' else 'SELL_SHORT', 'reason': 'opening_environment_unavailable', 'details': {'message': reason}, 'at': time.time()})
        return False, reason
    effective_px = price
    effective_tp = tp_px
    effective_sl = sl_px

    if env.simulated:
        try:
            demo_ticker = run_json_cmd(f"okx --demo market ticker {inst_id} --json")
            if demo_ticker and isinstance(demo_ticker, list) and demo_ticker[0].get("last"):
                demo_last = float(demo_ticker[0]["last"])
                if demo_last > 0 and price > 0:
                    divergence = abs(price - demo_last) / demo_last
                    # If live market price diverged from demo sandbox by more than 5% (e.g. ASTER / illiquid demo pair)
                    if divergence > 0.05:
                        if not allow_demo_translation:
                            return False, '程序候选与模拟盘报价偏离，禁止平移价格计划，等待重新采集'
                        scale = demo_last / price
                        prec = len(str(demo_ticker[0]["last"]).split(".")[1]) if "." in str(demo_ticker[0]["last"]) else 4
                        effective_px = round(price * scale, prec)
                        effective_tp = round(tp_px * scale, prec)
                        effective_sl = round(sl_px * scale, prec)
                        # Re-verify boundary constraints for demo sandbox
                        if pos_side == "long":
                            if effective_sl >= effective_px:
                                effective_sl = round(effective_px * 0.98, prec)
                            if effective_tp <= effective_px:
                                effective_tp = round(effective_px * 1.04, prec)
                        else:
                            if effective_sl <= effective_px:
                                effective_sl = round(effective_px * 1.02, prec)
                            if effective_tp >= effective_px:
                                effective_tp = round(effective_px * 0.96, prec)
        except Exception:
            pass

    try:
        plan, client_id = entry_gateway.prepare(market._selected(), inst_id=inst_id, side=pos_side,
            entry=effective_px, stop=effective_sl, take_profit=effective_tp, requested_size=size,
            budget=risk_budget_usdt, decision_id=decision_id, decision_at=decision_at, horizon=horizon)
    except Exception as exc:
        reason = f"Final risk preflight rejected: {type(exc).__name__}: {exc}"
        strategy_evidence.best_effort(env.identity, 'entry_rejection', {'instrument': inst_id, 'decision_id': decision_id, 'candidate_id': None, 'action': 'BUY_LONG' if pos_side == 'long' else 'SELL_SHORT', 'reason': 'preflight_rejected', 'details': {'error_type': type(exc).__name__, 'message': str(exc)[:500]}, 'at': time.time()})
        return False, reason
    LAST_ENTRY_PLAN.clear(); LAST_ENTRY_PLAN.update(plan)
    save_horizon_intent(inst_id, pos_side, horizon, decision_id, strategy_mode=plan.get('strategy_mode'), setup=plan.get('setup'), entry_context=plan.get('entry_context'))
    size = plan['size']
    effective_px, effective_sl, effective_tp = plan['entry'], plan['stop'], plan['take_profit']
    command = okx_private_command(
        f"okx swap place --instId {inst_id} --tdMode cross --side {side} --clOrdId {client_id} "
        f"--posSide {pos_side} --ordType limit --px {effective_px} --sz {size} "
        f"--tpTriggerPx {effective_tp} --tpOrdPx=-1 --slTriggerPx {effective_sl} --slOrdPx=-1 --json"
    )
    result = run_cmd_result(command, timeout=20)
    from scripts.entry_diagnostics import submission_diagnostics
    failure_details = submission_diagnostics(result, env) if not result['ok'] else None
    strategy_evidence.best_effort(market._selected().identity, 'entry_submission',
        {'client_id': client_id, 'plan': plan, 'transport_ok': result['ok'],
         'response': result.get('data') if result['ok'] else None, 'failure': failure_details})
    if not result["ok"]:
        strategy_evidence.best_effort(env.identity, 'entry_rejection', {'instrument': inst_id, 'decision_id': decision_id, 'candidate_id': plan.get('candidate_id'), 'action': 'BUY_LONG' if pos_side == 'long' else 'SELL_SHORT', 'reason': 'submission_unknown', 'client_id': client_id, 'details': failure_details, 'at': time.time()})
        return False, "Entry outcome unknown; durable reservation retained for read-only reconciliation"
    payload = result.get("data")
    order_id = None
    if isinstance(payload, dict):
        order_id = payload.get("ordId") or payload.get("orderId")
        nested = payload.get("data")
        if not order_id and isinstance(nested, dict):
            order_id = nested.get("ordId")
        elif not order_id and isinstance(nested, list) and nested and isinstance(nested[0], dict):
            order_id = nested[0].get("ordId")
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        order_id = payload[0].get("ordId")
    if not order_id:
        strategy_evidence.best_effort(env.identity, 'entry_rejection', {'instrument': inst_id, 'decision_id': decision_id, 'candidate_id': plan.get('candidate_id'), 'action': 'BUY_LONG' if pos_side == 'long' else 'SELL_SHORT', 'reason': 'accepted_missing_order_id', 'client_id': client_id, 'at': time.time()})
        return False, "exchange accepted response without a verifiable order id"
    strategy_evidence.finish_intent(client_id, 'acknowledged', {'order_id': str(order_id), 'size': size})
    return True, str(order_id)


def _float_or_zero(value: Any) -> float:
    try:
        parsed = abs(float(value or 0.0))
        return parsed if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _live_oco_coverage(orders: List[Dict[str, Any]], pos_side: str, *, mark_px=None, entry_px=None) -> float:
    """Only explicitly proven live OCO rows count; never infer missing fields."""
    from scripts.protection_policy import oco_coverage
    snapshot = oco_coverage(orders, pos_side, mark_px, entry_px)
    return 0.0 if snapshot.unknown else snapshot.size


def ensure_cloud_position_protection(inst_id: str, pos_side: str, size: float, tp_px: float, sl_px: float) -> Tuple[bool, str]:
    """Unknown/triggered protection never becomes a gap eligible for blind repair."""
    from decimal import Decimal
    from scripts.protection_policy import oco_coverage, positive, trigger_geometry
    size = positive(size)
    if size is None or pos_side not in ('long', 'short'):
        return False, "UNKNOWN: invalid position side or size"
    env = market._selected()
    try:
        orders = algo_reader.orders_for_instrument(algo_reader.read_algo_orders(env, priority="risk", force=True), inst_id)
        position_ok, positions, _ = query_positions()
        matching = [p for p in positions if p.get('instId') == inst_id and p.get('posSide') == pos_side]
        if not position_ok or len(matching) != 1 or positive(matching[0].get('pos')) != size:
            return False, "UNKNOWN: position changed or current position snapshot unavailable"
        position = matching[0]
        mark_px, entry_px = positive(position.get('markPx')), positive(position.get('avgPx'))
        if mark_px is None and entry_px is None:
            return False, "UNKNOWN: no position price reference for protection geometry"
    except Exception as exc:
        detail = str(exc) if isinstance(exc, algo_reader.AlgoReadError) else type(exc).__name__
        return False, f"UNKNOWN: unable to verify cloud OCO after bounded reads: {detail}"
    snapshot = oco_coverage(orders, pos_side, mark_px, entry_px)
    if snapshot.unknown:
        return False, "UNKNOWN: ambiguous or possibly triggered protection; no repair sent"
    coverage = snapshot.size
    if coverage >= size:
        return True, f"cloud OCO coverage verified ({coverage:g}/{size:g})"
    if not trigger_geometry(pos_side, tp_px, sl_px, mark_px, entry_px):
        return False, "UNKNOWN: proposed repair triggers cross the position price reference"
    missing = Decimal(str(size)) - sum(Decimal(str(row['sz'])) for row in snapshot.orders)
    close_side = "sell" if pos_side == "long" else "buy"
    command = okx_private_command(
        f"okx swap algo place --instId {inst_id} --side {close_side} --posSide {pos_side} "
        f"--tdMode cross --ordType oco --sz {missing:f} --tpTriggerPx {tp_px} --tpOrdPx=-1 "
        f"--slTriggerPx {sl_px} --slOrdPx=-1 --reduceOnly --cxlOnClosePos --json"
    )
    try:
        placed = run_cmd_result(command, timeout=20)  # Exactly one write attempt.
        write_confirmed = placed["ok"]
    except Exception:
        write_confirmed = False
    # An ambiguous write is NOT repeated. Reconcile by fresh reads only.
    deadline = time.monotonic() + 6
    last_detail = "UNKNOWN: no fresh post-repair snapshot obtained before deadline"
    for _ in range(4):
        remaining = deadline - time.monotonic()
        if remaining <= .5: break
        time.sleep(.5)
        try:
            orders = algo_reader.orders_for_instrument(algo_reader.read_algo_orders(
                env, priority="risk", force=True, timeout=deadline - time.monotonic()), inst_id)
        except Exception as exc:
            detail = str(exc) if isinstance(exc, algo_reader.AlgoReadError) else type(exc).__name__
            last_detail = f"UNKNOWN: post-repair verification unavailable: {detail}"
            break  # Reader already applied bounded retry/Retry-After; no outer retry storm.
        try:
            position_ok, positions, _ = query_positions()
            matching = [p for p in positions if p.get('instId') == inst_id and p.get('posSide') == pos_side]
            if not position_ok or len(matching) != 1 or positive(matching[0].get('pos')) != size:
                return False, "UNKNOWN: position changed during protection repair"
            mark_px, entry_px = positive(matching[0].get('markPx')), positive(matching[0].get('avgPx'))
            if mark_px is None and entry_px is None:
                return False, "UNKNOWN: post-repair price reference unavailable"
        except Exception:
            return False, "UNKNOWN: post-repair position snapshot unavailable"
        snapshot = oco_coverage(orders, pos_side, mark_px, entry_px)
        if snapshot.unknown:
            return False, "UNKNOWN: ambiguous or possibly executing post-repair protection"
        if snapshot.size >= size:
            label = "repaired and verified" if write_confirmed else "reconciled after uncertain write"
            return True, f"cloud OCO {label} ({snapshot.size:g}/{size:g})"
        last_detail = "INSUFFICIENT: fresh post-repair snapshot still shows incomplete coverage"
    return False, last_detail


def record_trade(trade_data):
    # Keep independent scalp/swing outcome statistics alongside the raw ledger.
    # The ledger remains the source of truth; this is an additive read model.
    horizon = str(trade_data.get('horizon') or '').lower()
    if horizon not in {'scalp','swing'}:
        inst=str(trade_data.get('inst') or trade_data.get('name') or '')
        direction=str(trade_data.get('direction') or trade_data.get('side') or '').lower()
        side='long' if '?' in direction or 'long' in direction else 'short' if '?' in direction or 'short' in direction else ''
        tracked=load_trackers().get(f'{inst}-USDT-SWAP_{side}',{}) if inst and side else {}
        horizon=str(tracked.get('horizon') or CURRENT_HORIZON or 'unknown').lower()
    if horizon not in {'scalp','swing'}: horizon = 'unknown'
    trade_data.setdefault('horizon', horizon)
    try:
        stats={}
        if os.path.exists(HORIZON_STATS_FILE):
            with open(HORIZON_STATS_FILE,encoding='utf-8') as f: stats=json.load(f)
        bucket=stats.setdefault(horizon, {'closed':0,'wins':0,'losses':0,'net_pnl':0.0,'fees':0.0})
        pnl=float(trade_data.get('net_pnl',trade_data.get('pnl',0.0)) or 0.0)
        fee=float(trade_data.get('fee',0.0) or 0.0)
        bucket['closed']+=1; bucket['wins']+=int(pnl>0); bucket['losses']+=int(pnl<=0)
        bucket['net_pnl']=round(float(bucket.get('net_pnl',0.0))+pnl,8); bucket['fees']=round(float(bucket.get('fees',0.0))+fee,8)
        with open(HORIZON_STATS_FILE,'w',encoding='utf-8') as f: json.dump(stats,f,ensure_ascii=False,indent=2)
    except Exception as e:
        print(f"Failed to record horizon stats: {e}")
    try:
        ledger = []
        if os.path.exists(LEDGER_JSON_FILE):
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        ledger.append(trade_data)
        with open(LEDGER_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to record trade to JSON: {e}")

    try:
        if record_trade_sqlite:
            record_trade_sqlite(trade_data)
    except Exception as e:
        print(f"Failed to record trade to SQLite: {e}")

# =============================================================================
# 🧮 Enhanced Quantitative Technical Indicators Math Engine
# =============================================================================
def calc_ema(prices, period):
    if not prices or len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2.0 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_rsi(prices, period=14):
    if not prices or len(prices) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        chg = prices[i] - prices[i-1]
        if chg >= 0:
            gains.append(chg)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(chg))
    
    if len(gains) < period:
        return 50.0
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_atr(candles, period=14):
    if not candles or len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i][2])
        l = float(candles[i][3])
        prev_c = float(candles[i-1][4])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if not trs:
        return 0.0
    if len(trs) < period:
        return sum(trs) / len(trs)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

def calc_macd_histogram_acceleration(prices, fast=12, slow=26, signal=9):
    """Calculates MACD Line, Signal Line, Histogram, and Histogram Delta (Acceleration)"""
    if len(prices) < slow + signal:
        return 0.0, 0.0, 0.0, 0.0
    
    # Calculate fast & slow EMA series
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    k_sig = 2.0 / (signal + 1)

    fast_ema = prices[0]
    slow_ema = prices[0]
    macd_series = []

    for p in prices:
        fast_ema = p * k_fast + fast_ema * (1 - k_fast)
        slow_ema = p * k_slow + slow_ema * (1 - k_slow)
        macd_series.append(fast_ema - slow_ema)

    sig_ema = macd_series[0]
    hist_series = []
    for m in macd_series:
        sig_ema = m * k_sig + sig_ema * (1 - k_sig)
        hist_series.append(m - sig_ema)

    latest_macd = macd_series[-1]
    latest_sig = sig_ema
    latest_hist = hist_series[-1]
    hist_accel = hist_series[-1] - hist_series[-2] if len(hist_series) >= 2 else 0.0

    return latest_macd, latest_sig, latest_hist, hist_accel

def calc_obv_trend(closes, vols, period=14):
    """On-Balance Volume (OBV) and OBV Divergence Slope"""
    if len(closes) < period or len(vols) < period:
        return 0.0, "NEUTRAL"
    
    obv_val = 0.0
    obv_series = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv_val += vols[i]
        elif closes[i] < closes[i-1]:
            obv_val -= vols[i]
        obv_series.append(obv_val)

    # Slope of last 5 bars
    recent_obv = obv_series[-5:]
    recent_px = closes[-5:]
    obv_up = recent_obv[-1] > recent_obv[0]
    px_up = recent_px[-1] > recent_px[0]

    if obv_up and not px_up:
        div_state = "BULL_ACCUMULATION" # 主力隐蔽吸筹
    elif not obv_up and px_up:
        div_state = "BEAR_DISTRIBUTION" # 主力拉高出货背离
    elif obv_up and px_up:
        div_state = "BULL_FLOW"
    else:
        div_state = "BEAR_FLOW"

    return obv_val, div_state

def calc_bollinger_squeeze(closes, period=20, mult=2.0):
    """Bollinger Bandwidth & Squeeze Ratio"""
    if len(closes) < period:
        return 0.0, 0.0, False
    
    sub = closes[-period:]
    sma = sum(sub) / period
    variance = sum((x - sma) ** 2 for x in sub) / period
    std_dev = variance ** 0.5
    upper = sma + mult * std_dev
    lower = sma - mult * std_dev
    bandwidth = ((upper - lower) / sma) * 100.0 if sma > 0 else 0.0
    
    # Squeeze detected if bandwidth is in lowest 20% quantile (< 1.8% for crypto/stock)
    is_squeeze = bandwidth < 1.80
    return bandwidth, std_dev, is_squeeze

# =============================================================================
# 🚀 High-Alpha Multi-Factor Extraction & Quantitative Feature Assembly
# =============================================================================
def _execution_calculus_candles(rows):
    """Strip OKX timestamps/metadata, preserving newest-first calculator order."""
    normalized = []
    for row in rows:
        candle = [float(row[i]) for i in range(1, 6)]
        if not all(math.isfinite(value) for value in candle):
            raise ValueError("Non-finite execution calculus candle")
        normalized.append(candle)
    return normalized


def _scale_in_calculus_gate(calculus, side):
    """Fail closed on unavailable momentum/probability evidence; retain thresholds."""
    unavailable = (False, math.nan, math.nan)
    if not isinstance(calculus, dict) or calculus.get("valid") is not True:
        return unavailable
    probability = calculus.get("probability_theory")
    # The aggregate calculator does not currently emit a nested valid flag.
    if not isinstance(probability, dict) or probability.get("valid", True) is not True:
        return unavailable
    if side not in ("long", "short"):
        return unavailable
    key = "continuation_prob_pct" if side == "long" else "breakdown_prob_pct"
    try:
        acceleration = calculus["acceleration"]
        chance = probability[key]
        if isinstance(acceleration, bool) or isinstance(chance, bool):
            return unavailable
        acceleration, chance = float(acceleration), float(chance)
    except (KeyError, TypeError, ValueError, OverflowError):
        return unavailable
    if not math.isfinite(acceleration) or not math.isfinite(chance) or not 0 <= chance <= 100:
        return unavailable
    momentum_ok = acceleration >= -0.25 if side == "long" else acceleration <= 0.25
    return momentum_ok and chance >= 40.0, acceleration, chance


def fetch_single_instrument_data(item, all_positions, usdt_available):
    inst_id = item["instId"]
    name = item["name"]
    asset_type = item["type"]
    base_sz = item["base_sz"]

    f = {
        "instId": inst_id,
        "name": name,
        "type": asset_type,
        "base_sz": base_sz,
        "sz": base_sz,
        "precision": item["precision"],
        "tickSz": item.get("tickSz"),
        "ctVal": item["ctVal"],
        "risk_per_trade_usd": item.get("risk_per_trade_usd", 15.0),
        "price": 0.0,
        "change24h": 0.0,
        "vol24h": 0.0,
        "rsi": 50.0,
        "rsi_7": 50.0,
        "ema9": 0.0,
        "ema21": 0.0,
        "ema55": 0.0,
        "ema21_slope_pct": 0.0,
        "vwap": 0.0,
        "vwap_bias": 0.0,
        "macd_hist": 0.0,
        "macd_accel": 0.0,
        "obv_flow": "NEUTRAL",
        "bb_bandwidth": 0.0,
        "bb_squeeze": False,
        "atr": 0.0,
        "atr_pct": 0.0,
        "vol_15m": 0.0,
        "vol_ma20": 0.0,
        "vol_ratio": 1.0,
        "is_bull_candle_15m": False,
        "is_bear_candle_15m": False,
        "lower_wick_ratio": 0.0,
        "upper_wick_ratio": 0.0,
        "market_regime": "CHOP",
        "structure_1h": "CHOP",
        "trend_1h_bullish": True,
        "trend_4h_bullish": True,
        "trend_1h_bearish": False,
        "trend_4h_bearish": False,
        "sentiment_score": 0.0,
        "position": None,
        "market_data_valid": False,
        "usdtAvailable": usdt_available
    }

    # Match existing position
    for p in all_positions:
        if p.get("instId") == inst_id:
            pos_val = float(p.get("pos", 0))
            if pos_val != 0:
                f["position"] = {
                    "instId": inst_id,
                    "name": name,
                    "posId": p.get("posId"), "cTime": p.get("cTime"),
                    "side": p.get("posSide", p.get("side", "")),
                    "pos": pos_val,
                    "avgPx": float(p.get("avgPx", 0)),
                    "markPx": float(p.get("markPx", p.get("last", 0)) or 0),
                    "upl": float(p.get("upl", 0)),
                    "uplRatio": float(p.get("uplRatio", 0) or 0),
                    "lever": p.get("lever", "3")
                }
                break

    # Collect the complete frame before any arithmetic. A missing/short 15M
    # frame used to leave price=0 while 1H ATR divided by it, aborting every
    # instrument in the portfolio. Incomplete data is a per-symbol WAIT.
    raw_15m = fetch_signal_candles(inst_id, "15m", 45)
    raw_1h = fetch_signal_candles(inst_id, "1H", 35)
    raw_4h = fetch_signal_candles(inst_id, "4H", 25)
    f["calculus"] = {"valid": False}
    try:
        for rows, minimum in ((raw_15m, 30), (raw_1h, 20), (raw_4h, 20)):
            if not isinstance(rows, list) or len(rows) < minimum:
                raise ValueError("Missing or insufficient closed candles")
            for row in rows:
                o, h, l, c, v = [float(row[i]) for i in range(1, 6)]
                if (not all(math.isfinite(x) for x in (o,h,l,c,v))
                        or not 0 < l <= min(o,c) <= max(o,c) <= h or v < 0):
                    raise ValueError("Invalid OHLCV geometry")
    except (TypeError, ValueError, IndexError, OverflowError):
        f["sz"] = 0
        f["data_quality_reason"] = "Closed candle frame unavailable or invalid"
        return f

    # 1. Compute 15M factors from verified candles.
    if raw_15m:
        candles_15m = list(reversed(raw_15m))
        closes = [float(c[4]) for c in candles_15m]
        vols = [float(c[5]) if len(c) > 5 else 1.0 for c in candles_15m]
        
        f["price"] = closes[-1]
        f["rsi"] = calc_rsi(closes, 14)
        f["rsi_7"] = calc_rsi(closes, 7)
        f["ema9"] = calc_ema(closes, 9)
        f["ema21"] = calc_ema(closes, 21)
        f["ema55"] = calc_ema(closes, 55)
        
        # Calculate EMA21 Slope over last 3 bars
        if len(closes) >= 5:
            prev_e21 = calc_ema(closes[:-3], 21)
            f["ema21_slope_pct"] = ((f["ema21"] - prev_e21) / prev_e21 * 100.0) if prev_e21 > 0 else 0.0

        f["atr"] = calc_atr(candles_15m, 14)
        if f["price"] > 0:
            f["atr_pct"] = (f["atr"] / f["price"]) * 100.0

        # MACD Acceleration
        m_line, m_sig, m_hist, m_accel = calc_macd_histogram_acceleration(closes)
        f["macd_hist"] = round(m_hist, 4)
        f["macd_accel"] = round(m_accel, 4)

        # OBV Flow
        _, obv_flow = calc_obv_trend(closes, vols)
        f["obv_flow"] = obv_flow

        # Bollinger Bands & Squeeze
        bw, std_d, is_sq = calc_bollinger_squeeze(closes)
        f["bb_bandwidth"] = round(bw, 2)
        f["bb_squeeze"] = is_sq

        # Multi-scale VWAP & Bias
        cum_pv = sum([closes[i] * vols[i] for i in range(-15, 0)])
        cum_v = sum(vols[-15:])
        f["vwap"] = (cum_pv / cum_v) if cum_v > 0 else f["price"]
        if f["vwap"] > 0:
            f["vwap_bias"] = ((f["price"] - f["vwap"]) / f["vwap"]) * 100.0
        
        # Latest 15M Candle Geometry
        last_c = candles_15m[-1]
        c_open, c_high, c_low, c_close = float(last_c[1]), float(last_c[2]), float(last_c[3]), float(last_c[4])
        f["signal_close"] = f["price"]
        f["bidPx"] = 0.0
        f["askPx"] = 0.0
        f["quote_as_of_ms"] = 0
        # Fetch Real-time Orderbook Ticker BBO (Best Bid & Ask) for Precision Limit Placement
        try:
            d_t = market.get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}")
            if d_t.get("code") == "0" and "data" in d_t and len(d_t["data"]) > 0:
                t_item = d_t["data"][0]
                last, bid, ask = [float(t_item.get(key) or 0) for key in ("last", "bidPx", "askPx")]
                stamp = int(t_item.get("ts") or 0)
                if (not all(math.isfinite(x) and x > 0 for x in (last,bid,ask))
                        or ask < bid or not 0 <= time.time()*1000-stamp <= 15000):
                    raise ValueError("Invalid execution quote")
                f["quote_as_of_ms"] = stamp
                f["price"], f["bidPx"], f["askPx"] = last, bid, ask
        except Exception:
            pass

        f["is_bull_candle_15m"] = (c_close > c_open)
        f["is_bear_candle_15m"] = (c_close < c_open)
        
        total_len = max(c_high - c_low, f["price"] * 0.0001)
        lower_wick = min(c_open, c_close) - c_low
        upper_wick = c_high - max(c_open, c_close)
        f["lower_wick_ratio"] = lower_wick / total_len
        f["upper_wick_ratio"] = upper_wick / total_len
        
        f["vol_15m"] = vols[-1]
        f["vol_ma20"] = sum(vols[-20:]) / min(len(vols), 20) if vols else 1.0
        f["vol_ratio"] = round(f["vol_15m"] / f["vol_ma20"], 2) if f["vol_ma20"] > 0 else 1.0

    # 2. Fetch 1H & 4H Trend Confluence
    if raw_1h:
        c_1h = list(reversed(raw_1h))
        closes_1h = [float(c[4]) for c in c_1h]
        highs_1h = [float(c[2]) for c in c_1h]
        lows_1h = [float(c[3]) for c in c_1h]
        
        # 1H ATR 14 for Macro Swing Protection
        f["atr_1h"] = calc_atr(c_1h, 14)
        f["atr_15m"] = f["atr"]
        f["atr"] = max(f["atr_1h"], f["atr_15m"] * 1.5, f["price"] * 0.012)
        f["atr_pct"] = (f["atr"] / f["price"]) * 100.0
        
        e9_1h = calc_ema(closes_1h, 9)
        e21_1h = calc_ema(closes_1h, 21)
        e55_1h = calc_ema(closes_1h, 55)
        
        f["trend_1h_bullish"] = (e9_1h >= e21_1h and closes_1h[-1] >= e21_1h * 0.996)
        f["trend_1h_bearish"] = (e9_1h <= e21_1h and closes_1h[-1] <= e21_1h * 1.004)

        recent_low = min(lows_1h[-5:])
        prev_low = min(lows_1h[-15:-5])
        recent_high = max(highs_1h[-5:])
        prev_high = max(highs_1h[-15:-5])

        if recent_low < prev_low and recent_high < prev_high:
            f["structure_1h"] = "LH_LL"
        elif recent_high > prev_high and recent_low > prev_low:
            f["structure_1h"] = "HH_HL"
        else:
            f["structure_1h"] = "CHOP"
    
    if raw_4h:
        c_4h = list(reversed(raw_4h))
        closes_4h = [float(c[4]) for c in c_4h]
        e9_4h = calc_ema(closes_4h, 9)
        e21_4h = calc_ema(closes_4h, 21)
        f["trend_4h_bullish"] = (e9_4h >= e21_4h)
        f["trend_4h_bearish"] = (e9_4h <= e21_4h)

    # 3. Dynamic Multi-Wave Regime Classification (Strict 15M + 1H + 4H Real-Time Alignment)
    # Anti-Inertia Fix: Never classify as BEAR_TREND if short-term 15M is actively reversing upwards (EMA9 > EMA21) or price > 15M EMA21/55
    is_15m_bullish = (f["ema9"] >= f["ema21"] and f["price"] >= f["ema21"] * 0.998)
    is_15m_bearish = (f["ema9"] <= f["ema21"] and f["price"] <= f["ema21"] * 1.002)

    if f["trend_1h_bullish"] and is_15m_bullish and (f["structure_1h"] == "HH_HL" or f["price"] >= f["ema21"]):
        f["market_regime"] = "BULL_TREND"
    elif f["trend_1h_bearish"] and is_15m_bearish and (f["structure_1h"] == "LH_LL" or f["price"] <= f["ema21"]):
        f["market_regime"] = "BEAR_TREND"
    else:
        # If 1H says bearish but 15M is rebounding upwards (e.g. V-reversal), strictly lock into CHOP / TRANSITION
        f["market_regime"] = "CHOP"

    # 4. Load Real-time News Sentiment
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            from scripts.news_connection import load_strategy_snapshot
            coin = name.split('-')[0]
            snapshot = load_strategy_snapshot(NEWS_SENTIMENT_FILE, [coin])
            f['news_context'] = {
                'fresh': bool(snapshot and snapshot.get('connection_status') in {'fresh', 'partial'}),
                'macro_sentiment': (snapshot or {}).get('macro_sentiment', 'UNKNOWN'),
                'updated_at': (snapshot or {}).get('captured_at'),
                'sentiment_fresh': bool((snapshot or {}).get('sentiment_fresh')),
                'article_count': len((snapshot or {}).get('items') or []),
                'digest': (snapshot or {}).get('digest'),
            }
            row = ((snapshot or {}).get('coins_sentiment') or {}).get(coin) or {}
            if row.get('available') is True and snapshot.get('sentiment_fresh'):
                f['sentiment_score'] = float(row.get('sentiment_factor_score'))
            else:
                f.pop('sentiment_score', None)
        except Exception:
            f['news_context'] = {'fresh': False, 'macro_sentiment': 'UNKNOWN', 'sentiment_fresh': False}
            f.pop('sentiment_score', None)

    # 5. Causal Multi-Timeframe Calculus Dynamics
    f["calculus"] = {"valid": False, "regime": "RANGE_LOW_VELOCITY", "velocity": 0.0, "acceleration": 0.0, "impulse": 0.0, "max_abs_jerk": 0.0, "quality": 0.0}
    try:
        from calculus_engine import calculate_multi_timeframe
        f["calculus"] = calculate_multi_timeframe({
            "15M": _execution_calculus_candles(raw_15m),
            "1H": _execution_calculus_candles(raw_1h),
            "4H": _execution_calculus_candles(raw_4h)
        })
    except Exception:
        pass

    # 6. Dynamic Equal-Risk Position Sizing with AI Self-Evolution Kelly Multipliers
    adaptive_cfg = load_adaptive_config()
    pos_multipliers = adaptive_cfg.get("position_size_multipliers", {})
    pos_mult = float(pos_multipliers.get(f["name"], 1.0))

    sl_mult = ASSET_CLASS_PROFILES.get(asset_type, {}).get("sl_atr_mult", 1.3)
    atr_val = max(f["atr"], f["price"] * 0.005)
    if f["ctVal"] > 0 and atr_val > 0:
        raw_dyn_sz = (f["risk_per_trade_usd"] * pos_mult) / (f["ctVal"] * atr_val * sl_mult)
        f["sz"] = max(1, int(round(raw_dyn_sz))) if pos_mult > 0 else 0
    else:
        f["sz"] = int(round(base_sz * pos_mult)) if pos_mult > 0 else 0

    market_data_valid = (
        len(raw_15m) >= 30
        and len(raw_1h) >= 20
        and len(raw_4h) >= 20
        and f["price"] > 0
        and f["atr"] > 0
        and 0 <= time.time()*1000 - f.get("quote_as_of_ms", 0) <= 15000
        and f.get("bidPx", 0) > 0
        and f.get("askPx", 0) >= f.get("bidPx", 0)
    )
    f["market_data_valid"] = market_data_valid
    if not market_data_valid:
        f["sz"] = 0

    return f

# =============================================================================
# Trailing Stop & Risk Management
# =============================================================================
def manage_position_tp_and_trailing(f, curr_pos, trackers, timestamp_full, executed_actions):
    if not f.get("market_data_valid"):
        executed_actions.append(f"[{f['name']}] 行情数据不完整，保留云端保护并跳过本地移动止盈")
        return False, "行情无效"
    inst_id = f["instId"]
    name = f["name"]
    cur_px = f["price"]
    asset_type = f.get("type", "crypto")
    profile = ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"])
    atr = max(f["atr"], cur_px * 0.005)
    prec = f["precision"]
    ct_val = f["ctVal"]
    
    pos_sz = float(curr_pos["pos"])
    is_long = "long" in curr_pos["side"].lower()
    entry_px = float(curr_pos["avgPx"])
    pos_key = f"{inst_id}_{curr_pos['side']}"

    from scripts.position_lifecycle import reconcile as reconcile_lifecycle, identity as position_identity
    lifecycle=reconcile_lifecycle(trackers,pos_key,curr_pos,market._selected().identity)
    if lifecycle=='unknown':
        from scripts.initial_protection import verify as verify_initial_protection
        initial=verify_initial_protection(market._selected(),inst_id,'long' if is_long else 'short',pos_sz,curr_pos,query_positions)
        if initial['status'] not in ('verified','flat','changed'):
            closed,detail=close_position_confirmed(inst_id,'long' if is_long else 'short',pos_sz,exit_reason='oco_unverified',position=curr_pos)
            executed_actions.append(f"[{name}] 持仓身份与云端保护未确认，安全退出确认={closed}；{detail}")
            return closed, '初始保护核验未知'
        executed_actions.append(f"[{name}] 持仓生命周期身份缺失，未修改云端保护，跳过本地动态止损")
        return False, '持仓身份待核验'
    now_ts = int(time.time())
    entry_item={}
    if pos_key not in trackers:
        # Completed but insufficient snapshots get one fresh recheck; read errors
        # have already exhausted their reader budget. No guessed repair or write retry.
        from scripts.initial_protection import verify as verify_initial_protection
        side_name = 'long' if is_long else 'short'
        initial = verify_initial_protection(market._selected(), inst_id, side_name, pos_sz, curr_pos, query_positions)
        if initial['status'] == 'flat':
            executed_actions.append(f"[{name}] 新持仓复查已归零，未发送平仓指令；等待账本回执")
            return True, '持仓已归零'
        if initial['status'] == 'changed':
            executed_actions.append(f"[{name}] 新持仓身份或数量已变化，保留云端保护并等待新快照")
            return False, '持仓快照已变化'
        if initial['status'] != 'verified':
            closed, detail = close_position_confirmed(inst_id, side_name, pos_sz, exit_reason='oco_unverified', position=curr_pos)
            executed_actions.append(f"[{name}] 新持仓保护经有界复查仍未确认（{initial['detail']}），安全退出确认={closed}；{detail}")
            return closed, '初始保护核验未知'
        rows = initial['orders']
        if initial.get('current_position'):
            curr_pos={**curr_pos,**initial['current_position']}
            pos_sz=float(curr_pos['pos']);entry_px=float(curr_pos['avgPx'])
        adopted_stop = (min if is_long else max)(float(o['slTriggerPx']) for o in rows)
        adopted_tp = float(rows[0]['tpTriggerPx'])
        strategy_evidence.best_effort(market._selected().identity,'position_protection_adoption',{
            'identity':position_identity(curr_pos,market._selected().identity),'orders':rows,
            'adopted_stop':adopted_stop,'source':'trader','cloud_stop_changed':False})
        score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
        entry_item = load_horizon_intents().get(pos_key) or {}
        entry_mode = entry_item.get("mode")
        horizon_intent = consume_horizon_intent(inst_id, side_name)
        trackers[pos_key] = {
            "horizon": horizon_intent,
            "mode": entry_mode or __import__("scripts.strategy_modes",fromlist=["mode_for"]).mode_for(horizon_intent),
            "setup": entry_item.get('setup') or 'unknown',
            "instId": inst_id,
            "name": name,
            "side": curr_pos["side"],
            "strategy_tag": "多头持仓" if is_long else "空头持仓",
            "positionIdentity": position_identity(curr_pos,market._selected().identity),
            "entryPx": entry_px,
            "entryTs": int(float(curr_pos.get("cTime") or now_ts*1000)/1000),
            "entryTime": timestamp_full,
            "initialSz": pos_sz,
            "currentSz": pos_sz,
            "highWaterMark": cur_px,
            "lowWaterMark": cur_px,
            "trailingStopPx": adopted_stop,
            "exchangeStopPx": adopted_stop,
            "initialRiskStopPx": adopted_stop,
            "takeProfitPx": adopted_tp,
            "stage_desc": "持有监控中"
        }

    t = trackers[pos_key]
    from scripts.scalp_management import adopt as adopt_entry_context
    adopt_entry_context(t,entry_item or load_horizon_intents().get(pos_key) or {},curr_pos,market._selected().identity)
    # The independent guard may create a tracker before the 15M trader sees it.
    # Adopt the frozen submission mode only within this exact new position's entry window.
    saved=load_horizon_intents().get(pos_key) or {}
    created=float(curr_pos.get('cTime') or 0)/1000
    if not t.get('mode') and saved.get('mode') and 0 <= created-float(saved.get('ts',0)) <= 300:
        t['mode']=saved['mode']; t['horizon']=saved['horizon'];t['decision_id']=saved.get('decision_id')
    if t.get('mode',{}).get('engine') == 'demo_scalp_v2':
        from scripts.minute_exit import enrich_volatility
        enrich_volatility(f,inst_id,market)
    global CURRENT_HORIZON
    CURRENT_HORIZON = str(t.get('horizon','swing')).lower()
    # Exit volatility must follow the persisted position mode. Without this
    # explicit propagation, scalp positions silently fall back to ATR(15M).
    f['horizon'] = CURRENT_HORIZON
    f['mode'] = t.get('mode') or __import__('scripts.strategy_modes',fromlist=['mode_for']).mode_for(CURRENT_HORIZON)
    ex = _exit_preset(t, executed_actions, name)
    setup=str(t.get('setup') or 'unknown')
    volatility = exit_policy.volatility(f)
    exit_atr = volatility['value']
    t['exitVolatility'] = {**volatility, 'observed_at': now_ts}
    t["currentSz"] = pos_sz
    if "entryTs" not in t:
        t["entryTs"] = now_ts

    # Peak Profit Tracking
    if is_long:
        t["highWaterMark"] = max(t.get("highWaterMark", cur_px), cur_px)
        cur_profit_px = cur_px - entry_px
        peak_profit_px = t["highWaterMark"] - entry_px
    else:
        t["lowWaterMark"] = min(t.get("lowWaterMark", cur_px), cur_px)
        cur_profit_px = entry_px - cur_px
        peak_profit_px = entry_px - t["lowWaterMark"]

    # One activation gate for cost floor, tier floors and kinetic exits.
    from scripts.profit_protection import floor_plan
    from scripts.risk_policy import load_policy
    try: policy=load_policy()
    except (ValueError, OSError, TypeError): policy=None
    if not t.get('initialRiskStopPx'):
        original=float(t.get('exchangeStopPx') or t.get('trailingStopPx') or 0)
        if original>0 and ((is_long and original<entry_px) or (not is_long and original>entry_px)):
            t['initialRiskStopPx']=original
    protection=floor_plan('long' if is_long else 'short',entry_px,cur_px,
        t['highWaterMark'] if is_long else t['lowWaterMark'],t.get('initialRiskStopPx'),
        exit_atr,taker_fee=policy.taker_fee,slippage=policy.slippage,thresholds=ex) if policy is not None else {'active':False,'reason':'cost_policy_unavailable'}
    # Only a frozen, lifecycle-bound minute context selects v2 management.
    # Unknown/manual/legacy 15M positions retain their existing protective exits.
    management={}
    if policy is not None and t.get('mode',{}).get('engine')=='demo_scalp_v2':
        from scripts.scalp_management import evaluate as evaluate_scalp
        management=evaluate_scalp(t,f,entry=entry_px,current=cur_px,side='long' if is_long else 'short',
                                  now=now_ts,policy=policy,tick=f.get('tickSz'))
        t['scalpManagement']=management
        if management.get('enabled'):
            protection=management['protection']
            t['stage_desc']={'INITIAL_CONFIRMATION':'等待收盘结构确认','FOLLOW_THROUGH':'结构延续',
                             'COST_PROTECTED':'费用后保本保护','PROFIT_LOCK':'浮盈锁定','RISK_REDUCED':'风险已收缩（未净保本）','FAILED':'收盘结构失效'}.get(management['state'],'持有监控中')
    t['exitEvaluation'] = {**protection, 'management_version':management.get('version') if management.get('enabled') else None, 'policy': dict(t['exitPolicyStatus']), 'atr': dict(t['exitVolatility'])}
    if protection.get('active'):
        desired=protection['stop'];old=float(t.get('trailingStopPx') or 0)
        if not old or (desired>old if is_long else desired<old):
            t['trailingStopPx']=desired;t['localTrailingStopPx']=desired
            t['profitProtection']=protection
            t['stage_desc']='风险收缩，尚未覆盖全部成本' if protection.get('kind')=='risk_reduction' else '成本覆盖后的浮盈保护'

    # 1. Hard Stop Loss (loss protection is independent of profit-lock activation).
    # The tracker stop is the exchange-protection source of truth; if a legacy or
    # partially migrated position has no live cloud OCO, the local 15-minute
    # fail-safe still closes it once the stop is breached.
    hard_stop_px = float(t.get("trailingStopPx", 0.0) or 0.0)
    hard_stop_hit = hard_stop_px > 0 and ((is_long and cur_px <= hard_stop_px) or (not is_long and cur_px >= hard_stop_px))
    if hard_stop_hit:
        reducing=t.get('profitProtection',{}).get('active') and t.get('profitProtection',{}).get('kind')=='risk_reduction'
        protection_label='风险收缩' if reducing else '浮盈保护' if t.get('profitProtection',{}).get('active') else '硬止损'
        exit_code='risk_reduction_exit' if reducing else 'profit_lock' if t.get('profitProtection',{}).get('active') else 'hard_stop'
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz, exit_reason=exit_code, position=curr_pos)
        if not closed:
            executed_actions.append(f"[{name}] 硬止损平仓失败，仓位仍保留: {close_detail}")
            return False, "硬止损平仓失败"
        close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
        pnl_val = curr_pos["upl"]
        executed_actions.append(f"[{name}] 触发{protection_label} {hard_stop_px} 并确认平仓 (平仓前浮盈参考: {pnl_val:+.2f}U，结算以账本为准)")
        record_trade({
            "is_trade": True,
            "time": timestamp_full,
            "inst": name,
            "name": name,
            "action": "平仓",
            "action_type": protection_label,
            "direction": f"平{'多' if is_long else '空'}",
            "side": f"{'多' if is_long else '空'}单硬止损",
            "size": pos_sz,
            "sz": pos_sz,
            "price": cur_px,
            "fee": close_fee,
            "pnl": pnl_val,
            "remark": f"价格 {cur_px} 触及保护止损 {hard_stop_px}，交易所确认平仓"
        })
        if not t.get('profitProtection',{}).get('active'):
            add_stop_cooldown(inst_id, "long" if is_long else "short", "硬止损")
        if notify_trade_close:
            notify_trade_close(inst=name, pnl=pnl_val, stage="硬止损平仓", exit_px=cur_px)
        trackers.pop(pos_key, None)
        return True, "已" + protection_label

    if management.get('exit'):
        t['lastExitAttempt']={**management,'rule':'setup_failure_exit'}
        detail_event={'instId':inst_id,'position_identity':t.get('positionIdentity'),'decision_id':t.get('decision_id'),
                      'entry_context':t.get('entry_context'),'evaluation':management}
        strategy_evidence.best_effort(market._selected().identity,'scalp_exit_evaluation',detail_event)
        closed,detail=close_position_confirmed(inst_id,'long' if is_long else 'short',pos_sz,exit_reason='strategy_failure_exit',position=curr_pos)
        strategy_evidence.best_effort(market._selected().identity,'scalp_exit_outcome',{**detail_event,'closed':closed})
        if not closed:
            executed_actions.append(f'[{name}] 收盘结构失效，主动退出待确认：{detail}')
            return False,'结构退出待确认'
        executed_actions.append(f'[{name}] {setup} 连续收盘结构失效，策略主动退出；不追加开仓冷却')
        trackers.pop(pos_key,None)
        return True,'收盘结构失效退出'

    default_tp_dist = max(atr * profile["tp_atr_mult"], entry_px * profile["min_profit_ratio"])
    if not _float_or_zero(t.get("takeProfitPx")):
        t["takeProfitPx"] = round(entry_px + default_tp_dist if is_long else entry_px - default_tp_dist, prec)
    protected, protection_detail = ensure_cloud_position_protection(
        inst_id, "long" if is_long else "short", pos_sz, float(t["takeProfitPx"]), hard_stop_px
    )
    if not protected and protection_detail.startswith('UNKNOWN:'):
        # A stale/transitioning algo snapshot is not an immediate close command.
        from scripts.initial_protection import verify as verify_protection_again
        fresh=verify_protection_again(market._selected(),inst_id,'long' if is_long else 'short',pos_sz,curr_pos,query_positions)
        if fresh['status']=='verified':
            protected=True
        elif fresh['status'] in ('flat','changed'):
            executed_actions.append(f'[{name}] 保护复查时持仓已变化，未复用旧快照平仓')
            return False,'等待持仓同步'
    if not protected:
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz, exit_reason='oco_unverified', position=curr_pos)
        if not closed:
            executed_actions.append(f"[{name}] 🚨 云端 OCO 核验未通过且安全退出失败: {protection_detail}; {close_detail}")
            return False, "保护与退出均失败"
        pnl_val = curr_pos["upl"]
        check_status = "insufficient" if protection_detail.startswith("INSUFFICIENT:") else "unknown"
        check_label = "确认保护不足" if check_status == "insufficient" else "保护核验未知"
        executed_actions.append(f"[{name}] 🧯 {check_label}，已按安全策略确认平仓: {protection_detail}")
        record_trade({
            "is_trade": True, "time": timestamp_full, "inst": name, "name": name,
            "action": "平仓", "action_type": f"{check_label}退出", "protection_check_status": check_status,
            "direction": f"平{'多' if is_long else '空'}", "side": f"{'多' if is_long else '空'}单{check_label}退出",
            "size": pos_sz, "sz": pos_sz, "price": cur_px,
            "fee": (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE, "pnl": pnl_val,
            "remark": f"未能确认云端 OCO 全仓覆盖，按安全策略确认平仓：{protection_detail}"
        })
        add_stop_cooldown(inst_id, "long" if is_long else "short", "云端保护核验失败")
        if notify_trade_close:
            notify_trade_close(inst=name, pnl=pnl_val, stage=f"云端{check_label}退出", exit_px=cur_px)
        trackers.pop(pos_key, None)
        return True, "保护核验安全退出"
    t["cloudProtection"] = {"verifiedAt": timestamp_full, "detail": protection_detail}

    # 2. Time exit: signed price profit below the selected ATR allowance.
    # This includes losses and small gains, not necessarily sideways/no volatility.
    hold_duration_sec = now_ts - t["entryTs"]
    minute_timeout = t.get('mode',{}).get('engine') == 'demo_scalp_v2' and hold_duration_sec >= 3600
    if minute_timeout or (exit_atr > 0 and hold_duration_sec > ex['time_stop_seconds'] and cur_profit_px < ex['time_stop_profit_atr'] * exit_atr):
        exit_evidence = {'rule': 'time_exit', 'policy': dict(t['exitPolicyStatus']),
                         'hold_seconds': hold_duration_sec, 'threshold_seconds': ex['time_stop_seconds'],
                         'profit_price': cur_profit_px, 'profit_atr': cur_profit_px / exit_atr if exit_atr > 0 else None,
                         'profit_threshold_atr': ex['time_stop_profit_atr'], 'atr': dict(t['exitVolatility'])}
        time_reason = (f"预设 {t['exitPolicyStatus']['preset_id']}：持仓 {hold_duration_sec/3600:.2f}h > {ex['time_stop_seconds']/3600:g}h，"
                       f"价格浮盈 {cur_profit_px/exit_atr if exit_atr>0 else 0:.3f} ATR < {ex['time_stop_profit_atr']:g} ATR，时间退出")
        if minute_timeout: time_reason = "程序短线持仓已满60分钟，执行时间退出"
        t['lastExitAttempt'] = exit_evidence
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz, exit_reason='time_exit', position=curr_pos)
        if not closed:
            executed_actions.append(f"[{name}] {time_reason}，平仓失败，仓位仍保留: {close_detail}")
            return False, "平仓失败"
        close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
        executed_actions.append(f"[{name}] ⌛ {time_reason}，交易所确认平仓")
        record_trade({
            "is_trade": True,
            "time": timestamp_full,
            "inst": name,
            "name": name,
            "action": "平仓",
            "action_type": "时间止损",
            "direction": f"平{'多' if is_long else '空'}",
            "side": f"{'多' if is_long else '空'}单时间退出",
            "size": pos_sz,
            "sz": pos_sz,
            "price": cur_px,
            "fee": close_fee,
            "pnl": curr_pos["upl"],
            "remark": time_reason,
            "exit_evidence": exit_evidence
        })
        if notify_trade_close:
            notify_trade_close(inst=name, pnl=float(curr_pos.get("upl", 0.0) or 0.0), stage="时间止损平仓", exit_px=cur_px)
        if pos_key in trackers: del trackers[pos_key]
        return True, "时间止损"

    # 3. Kinetic exit uses the same preset, ATR and cost activation as the
    # unified floor above. Hard/previously tightened stops always take priority.
    if protection.get('kinetic_exit'):
        exit_evidence = {**t['exitEvaluation'], 'rule': 'trailing_exit'}
        t['lastExitAttempt'] = exit_evidence
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz, exit_reason='trailing_exit', position=curr_pos)
        if not closed:
            executed_actions.append(f"[{name}] 动能回撤止盈失败，仓位仍保留: {close_detail}")
            return False, "平仓失败"
        close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
        pnl_val = curr_pos["upl"]
        reason = (f"预设 {t['exitPolicyStatus']['preset_id']}：峰值浮盈 {peak_profit_px/exit_atr:.3f} ATR，"
                  f"回撤 {protection['pullback']/exit_atr:.3f} ATR >= {ex['kinetic_pullback_atr']:g} ATR")
        executed_actions.append(f"[{name}] 🎯 {reason}，动能止盈已确认 (平仓前浮盈参考 {pnl_val:+.2f}U，结算以账本为准)")
        record_trade({
            "is_trade": True, "time": timestamp_full, "inst": name, "name": name,
            "action": "平仓", "action_type": "移动止盈",
            "direction": f"平{'多' if is_long else '空'}", "side": f"{'多' if is_long else '空'}单动能回撤止盈",
            "size": pos_sz, "sz": pos_sz, "price": cur_px, "fee": close_fee, "pnl": pnl_val,
            "remark": reason, "exit_evidence": exit_evidence,
        })
        if notify_trade_close:
            notify_trade_close(inst=name, pnl=pnl_val, stage="移动止盈", exit_px=cur_px)
        trackers.pop(pos_key, None)
        return True, "已移动止盈"

    return False, "持仓监控中"

def execute_ai_position_management(real_pos_dict, trackers, timestamp_full, executed_actions):
    """Execute only fresh, high-confidence and risk-reducing AI position instructions."""
    from scripts.protection_policy import oco_coverage, positive, rounded_stop
    if not os.path.exists(AI_POSITION_MANAGEMENT_FILE):
        return
    try:
        with open(AI_POSITION_MANAGEMENT_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if int(time.time()) - int(payload.get("timestamp", 0) or 0) > 300:
            executed_actions.append("AI持仓指令已过期，未执行")
            return
    except Exception as e:
        executed_actions.append(f"AI持仓指令读取失败: {e}")
        return

    for instruction in payload.get("instructions", []):
        inst_id = str(instruction.get("instId", ""))
        action = str(instruction.get("action", "HOLD")).upper()
        confidence = float(instruction.get("confidence", 0) or 0)
        reason = str(instruction.get("reason", "AI持仓管理"))[:120]
        position = real_pos_dict.get(inst_id)
        if not position or action == "HOLD":
            continue

        pos_side = str(position.get("posSide", "net")).lower()
        current_px = positive(position.get("markPx")) or positive(position.get("last")) or 0
        avg_px = positive(position.get("avgPx")) or 0
        name = inst_id.replace("-USDT-SWAP", "")

        if action == "CLOSE_MARKET":
            if confidence < 85:
                executed_actions.append(f"[{name}] AI平仓置信度{confidence:.0f}<85，拒绝执行")
                continue
            closed, close_detail = close_position_confirmed(inst_id, pos_side, float(position.get("pos", 0) or 0), exit_reason='ai_exit', position=position)
            if closed:
                executed_actions.append(f"[{name}] AI高置信度整仓退出: {reason}")
                trackers.pop(f"{inst_id}_{pos_side}", None)
            else:
                executed_actions.append(f"[{name}] AI平仓请求未获交易所确认，仓位保持不变: {close_detail}")

        elif action == "UPDATE_SL":
            new_sl = positive(instruction.get("suggested_sl_price")) or 0
            if positive(current_px) is None or positive(avg_px) is None:
                executed_actions.append(f"[{name}] UNKNOWN: current or entry price unavailable; amendment not sent")
                continue
            tracker = trackers.get(f"{inst_id}_{pos_side}")
            if tracker is None:
                executed_actions.append(f"[{name}] 缺少持仓保护状态，未发送止盈改单，原云端保护保持不变")
                continue
            ex = _exit_preset(tracker, executed_actions, name)
            volatility = tracker.get('exitVolatility') or {}
            observed_at = positive(volatility.get('observed_at')) or 0
            atr_val = positive(volatility.get('value')) or 0
            if not atr_val or not 0 <= time.time() - observed_at <= 300:
                executed_actions.append(f"[{name}] 退出ATR缺失或过期，未发送止盈改单，原云端保护保持不变")
                continue
            
            from scripts.profit_protection import allow_ai_tightening
            from scripts.risk_policy import load_policy
            try: policy=load_policy()
            except (ValueError, OSError, TypeError):
                executed_actions.append(f"[{name}] Cost policy unavailable; retain existing cloud stop")
                continue
            tightens_risk=allow_ai_tightening(pos_side,avg_px,current_px,new_sl,atr_val,
                taker_fee=policy.taker_fee,slippage=policy.slippage,thresholds=ex)

            if not tightens_risk:
                executed_actions.append(f"[{name}] 浮盈空间不足或与现价缓冲过近({current_px} vs 拟调SL {new_sl})，未满足预设浮盈启动、成本保本或最小行情缓冲")
                continue
            try:
                algo_orders = algo_reader.orders_for_instrument(
                    algo_reader.read_algo_orders(market._selected(), priority="risk", force=True), inst_id)
            except Exception as exc:
                detail = str(exc) if isinstance(exc, algo_reader.AlgoReadError) else type(exc).__name__
                executed_actions.append(f"[{name}] 止损单核验暂不可用，未发送改单，原保护单保持不变: {detail}")
                continue
            snapshot = oco_coverage(algo_orders, pos_side, current_px, avg_px)
            position_size = positive(position.get('pos'))
            tracker = trackers.get(f"{inst_id}_{pos_side}")
            if snapshot.unknown or position_size is None or snapshot.size < position_size or tracker is None:
                executed_actions.append(f"[{name}] UNKNOWN: full cloud coverage or local verification state unavailable; amendment not sent")
                continue
            if tracker.get('pendingStopAmendment') or tracker.get('cloudProtection', {}).get('status') == 'unknown':
                executed_actions.append(f"[{name}] UNKNOWN: previous stop amendment unresolved; no blind retry")
                continue
            try:
                metadata = market.get_json(
                    f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst_id}",
                    simulated=market._selected().simulated)['data']
                ticks = [r.get('tickSz') for r in metadata if r.get('instId') == inst_id]
                if len(ticks) != 1 or positive(ticks[0]) is None:
                    raise ValueError('Missing tick size')
                tick = ticks[0]
            except Exception:
                executed_actions.append(f"[{name}] UNKNOWN: tick metadata unavailable; amendment not sent")
                continue
            # Include every segment; an already tighter segment must never be loosened.
            planned = [(row, rounded_stop(pos_side, new_sl, row['slTriggerPx'], current_px, tick))
                       for row in snapshot.orders]
            planned = [(row, target) for row, target in planned if target is not None]
            if not planned:
                executed_actions.append(f"[{name}] Stop unchanged: would loosen, repeat or cross current price after tick rounding")
                continue
            all_verified = True
            for live_algo, target in planned:
                try:
                    position_ok, positions, _ = query_positions()
                    matching = [p for p in positions if p.get('instId') == inst_id and p.get('posSide') == pos_side]
                    if not position_ok or len(matching) != 1 or positive(matching[0].get('pos')) != position_size:
                        raise ValueError('Position changed')
                    current_px = positive(matching[0].get('markPx'))
                    if current_px is None or oco_coverage(list(snapshot.orders), pos_side, current_px, avg_px).unknown:
                        raise ValueError('Protection may already be triggering')
                    live_algo = next(o for o in snapshot.orders if o['algoId'] == live_algo['algoId'])
                    target = rounded_stop(pos_side, new_sl, live_algo['slTriggerPx'], current_px, tick)
                    if target is None:
                        # A concurrent tightening is safe to retain, but a crossed market is not.
                        if ((new_sl >= current_px) if pos_side == 'long' else (new_sl <= current_px)):
                            raise ValueError('Suggested stop now crosses market')
                        continue
                except Exception:
                    tracker['cloudProtection'] = {'status': 'unknown', 'detail': 'position/protection changed during amendment'}
                    executed_actions.append(f"[{name}] UNKNOWN: position or current price changed; remaining amendments stopped")
                    all_verified = False
                    break
                old_sl = float(live_algo['slTriggerPx'])
                # Retain uncertainty before writing; acknowledgement is not verification.
                tracker['pendingStopAmendment'] = {'algoId': live_algo['algoId'], 'requestedStop': target}
                tracker['cloudProtection'] = {'status': 'unknown', **tracker['pendingStopAmendment'],
                    'detail': 'stop amendment awaiting read-back'}
                try:
                    result = run_cmd_result(okx_private_command(
                        f"okx swap algo amend --instId {inst_id} --algoId {live_algo['algoId']} "
                        f"--newSlTriggerPx {target} --newSlOrdPx=-1 --json"))
                    transport_ok = result['ok']
                except Exception:
                    transport_ok = False
                # Reconcile each individual write before attempting the next segment.
                try:
                    fresh = algo_reader.orders_for_instrument(algo_reader.read_algo_orders(
                        market._selected(), priority='risk', force=True, timeout=6), inst_id)
                    position_ok, positions, _ = query_positions()
                    matching = [p for p in positions if p.get('instId') == inst_id and p.get('posSide') == pos_side]
                    if not position_ok or len(matching) != 1 or positive(matching[0].get('pos')) != position_size:
                        raise ValueError('Position changed after amendment')
                    current_px = positive(matching[0].get('markPx'))
                    if current_px is None:
                        raise ValueError('Current mark unavailable after amendment')
                    fresh_snapshot = oco_coverage(fresh, pos_side, current_px, avg_px)
                    confirmed = next((o for o in fresh_snapshot.orders if o['algoId'] == live_algo['algoId']), None)
                    actual = positive(confirmed.get('slTriggerPx')) if confirmed else None
                    verified = (not fresh_snapshot.unknown and fresh_snapshot.size >= position_size
                                and {o['algoId'] for o in fresh_snapshot.orders} == {o['algoId'] for o in snapshot.orders}
                                and confirmed is not None and positive(confirmed['sz']) == positive(live_algo['sz'])
                                and actual is not None and ((float(target) <= actual < current_px)
                                    if pos_side == 'long' else (current_px < actual <= float(target))))
                    # Concurrent removal, resizing or loosening of another segment is unknown.
                    for previous in snapshot.orders:
                        observed = next((o for o in fresh_snapshot.orders if o['algoId'] == previous['algoId']), None)
                        if (observed is None or positive(observed['sz']) != positive(previous['sz'])
                                or (float(observed['slTriggerPx']) < float(previous['slTriggerPx']) if pos_side == 'long'
                                    else float(observed['slTriggerPx']) > float(previous['slTriggerPx']))):
                            verified = False
                except Exception:
                    verified = False
                strategy_evidence.best_effort(market._selected().identity, 'stop_amendment', {
                    'instrument': inst_id, 'algo_id': live_algo['algoId'], 'old_stop': old_sl,
                    'requested_stop': float(target), 'verified': verified, 'transport_ok': transport_ok})
                if not verified:
                    executed_actions.append(f"[{name}] UNKNOWN: amendment outcome uncertain; batch stopped without retry or local confirmation")
                    all_verified = False
                    break
                snapshot = fresh_snapshot
                tracker.pop('pendingStopAmendment', None)
            if all_verified:
                actual = (min if pos_side == 'long' else max)(float(o['slTriggerPx']) for o in snapshot.orders)
                tracker['trailingStopPx'] = actual
                tracker['cloudProtection'] = {'status': 'verified', 'verifiedAt': timestamp_full,
                    'detail': 'all stop segments read-back confirmed'}
                executed_actions.append(f"[{name}] All stop segments read-back confirmed; weakest protection {actual}: {reason}")

# =============================================================================
# 🧠 OKXQuant v0.1.0 Multi-Factor Scoring & Strategy Setup Classifier
# =============================================================================
def evaluate_asset_signal(f):
    """
    Continuous Multi-Factor Quantitative Scoring Engine (-5.0 ~ +5.0).
    Uses trend, volume, mean-reversion and sentiment sub-scores.
    """
    if not f.get("market_data_valid"):
        return 0.0, "HOLD", ["关键行情数据缺失"], "⚪ 观望", "行情数据不完整，禁止生成交易信号"
    inst_id = f["instId"]
    inst_name = f["name"]
    asset_type = f.get("type", "crypto")
    profile = ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"])
    
    # 1. Hot-reload AI Evolution Config
    adaptive_cfg = load_adaptive_config()
    cooldown_assets = adaptive_cfg.get("cooldown_assets", [])
    strat_weights = adaptive_cfg.get("strategy_weights", {})
    strat_enabled = adaptive_cfg.get("strategy_enabled", {})
    entry_threshold = float(adaptive_cfg.get("entry_threshold", profile.get("entry_threshold", 2.2)))

    # Intervene 1: Cooldown Blacklist
    if inst_name in cooldown_assets or inst_id in cooldown_assets:
        return 0.0, "HOLD", ["⛔ 标的处于AI避险冷却池中，自进化系统禁止开仓"], "⚪ 避险冷却", f"【自进化干预】{inst_name} 胜率不足或连续止损，已被自动关入冷却池避险"

    px = f["price"]
    ema9, ema21, ema55 = f["ema9"], f["ema21"], f["ema55"]
    e21_slope = f.get("ema21_slope_pct", 0.0)
    rsi = f["rsi"]
    rsi_7 = f.get("rsi_7", 50.0)
    vwap_bias = f.get("vwap_bias", 0.0)
    macd_hist = f.get("macd_hist", 0.0)
    macd_accel = f.get("macd_accel", 0.0)
    obv_flow = f.get("obv_flow", "NEUTRAL")
    bb_squeeze = f.get("bb_squeeze", False)
    vol_ratio = f.get("vol_ratio", 1.0)
    regime = f.get("market_regime", "CHOP")
    struct_1h = f.get("structure_1h", "CHOP")

    is_bull_c = f.get("is_bull_candle_15m", False)
    is_bear_c = f.get("is_bear_candle_15m", False)
    lower_wick = f.get("lower_wick_ratio", 0.0)
    upper_wick = f.get("upper_wick_ratio", 0.0)

    cooldown_long = is_in_stop_cooldown(inst_id, "long")
    cooldown_short = is_in_stop_cooldown(inst_id, "short")

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 1: Trend & Slope Momentum (-1.5 ~ +1.5)
    # -------------------------------------------------------------------------
    score_trend = 0.0
    if regime == "BULL_TREND" and e21_slope > 0.02:
        score_trend = 1.2 + (0.3 if struct_1h == "HH_HL" else 0.0)
    elif regime == "BEAR_TREND" and e21_slope < -0.02:
        score_trend = -1.2 - (0.3 if struct_1h == "LH_LL" else 0.0)
    elif ema9 > ema21 > ema55:
        score_trend = 0.6
    elif ema9 < ema21 < ema55:
        score_trend = -0.6

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 2: Volume & MACD Acceleration (-1.5 ~ +1.5)
    # -------------------------------------------------------------------------
    score_vol = 0.0
    if macd_accel > 0 and macd_hist > 0:
        score_vol += 0.6
    elif macd_accel < 0 and macd_hist < 0:
        score_vol -= 0.6

    if obv_flow in ["BULL_FLOW", "BULL_ACCUMULATION"]:
        score_vol += 0.5
    elif obv_flow in ["BEAR_FLOW", "BEAR_DISTRIBUTION"]:
        score_vol -= 0.5

    if vol_ratio >= 1.25 and is_bull_c:
        score_vol += 0.4
    elif vol_ratio >= 1.25 and is_bear_c:
        score_vol -= 0.4

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 3: Mean Reversion & RSI Extremes (-1.2 ~ +1.2)
    # -------------------------------------------------------------------------
    score_mr = 0.0
    if vwap_bias <= -0.75 and rsi <= 35.0:
        score_mr = 1.2 # 超跌反弹多
    elif vwap_bias >= 0.75 and rsi >= 65.0:
        score_mr = -1.2 # 超买冲高空
    elif 40.0 <= rsi <= 55.0 and regime == "BULL_TREND":
        score_mr = 0.5 # 顺势健康区间
    elif 45.0 <= rsi <= 60.0 and regime == "BEAR_TREND":
        score_mr = -0.5 # 顺势空头区间

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 4: News & Sentiment (-0.8 ~ +0.8)
    # -------------------------------------------------------------------------
    sent_score = f.get("sentiment_score", 0.0)
    score_sent = max(-0.8, min(0.8, sent_score * 1.5))

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 5: Causal Calculus, Definite Integrals & Probability (-1.5 ~ +1.5)
    # -------------------------------------------------------------------------
    score_calc = 0.0
    c_dyn = f.get("calculus", {})
    c_v = float(c_dyn.get("velocity", 0.0) or 0.0)
    c_a = float(c_dyn.get("acceleration", 0.0) or 0.0)
    c_i = float(c_dyn.get("impulse", 0.0) or 0.0)
    c_j = abs(float(c_dyn.get("max_abs_jerk", 0.0) or 0.0))
    c_regime = c_dyn.get("regime", "")

    # 1. Calculus Dynamics
    if c_regime == "BULL_ACCELERATING" or (c_v > 0.2 and c_a > 0.1 and c_i > 0):
        score_calc += 0.6
    elif c_regime == "BULL_DECELERATING" or (c_v > 0.2 and c_a < -0.3):
        score_calc -= 0.5 # Anti-FOMO deceleration penalty
    elif c_regime == "BEAR_ACCELERATING" or (c_v < -0.2 and c_a < -0.1 and c_i < 0):
        score_calc -= 0.6
    elif c_regime == "BEAR_DECELERATING" or (c_v < -0.2 and c_a > 0.3):
        score_calc += 0.5 # Anti-bottom chasing penalty

    # 2. Definite Integrals (Energy & Area Accumulation)
    d_int = c_dyn.get("definite_integrals", {})
    e_int = float(d_int.get("energy_integral", 0.0) or 0.0)
    dev_area = float(d_int.get("deviation_area_integral", 0.0) or 0.0)
    if e_int > 1.0 and dev_area > 0.6:
        score_calc += 0.4 # Net positive kinetic work done
    elif e_int < -1.0 and dev_area < -0.6:
        score_calc -= 0.4 # Net negative depletion

    # 3. Probability Theory & Stochastic Risk
    p_th = c_dyn.get("probability_theory", {})
    p_cont = float(p_th.get("continuation_prob_pct", 50.0) or 50.0)
    p_break = float(p_th.get("breakdown_prob_pct", 50.0) or 50.0)
    if p_cont >= 70.0:
        score_calc += 0.4
    elif p_break >= 70.0:
        score_calc -= 0.4

    score_calc = max(-1.5, min(1.5, score_calc))

    # -------------------------------------------------------------------------
    # 🎯 Continuous Synthesis Multi-Factor Alpha Score
    # -------------------------------------------------------------------------
    raw_alpha_score = round(score_trend + score_vol + score_mr + score_sent + score_calc, 2)
    
    # -------------------------------------------------------------------------
    # 🏆 6 Institutional Quant Setups Recognition
    # -------------------------------------------------------------------------
    strategy_tag = "⚪ 观望"
    strategy_desc = "因子分布中性，无高置信度共振信号"
    reasons = []

    # High Jerk Shock Filter: Shock market dampens high-risk breakout setups
    is_high_jerk_shock = (c_j >= 1.8 or c_regime == "SHOCK_HIGH_JERK")

    # Setup 1: 🌊 顺势机构回踩 (Institutional Pullback)
    if regime == "BULL_TREND" and (px <= ema21 * 1.008 and px >= ema55 * 0.994) and (38.0 <= rsi <= 56.0) and (is_bull_c or lower_wick >= 0.20) and not cooldown_long and not is_high_jerk_shock:
        strategy_tag = "🌊 顺势回踩"
        raw_alpha_score = max(raw_alpha_score, 2.4)
        strategy_desc = f"【1H机构顺势】回踩EMA21/55价值中枢止跌收阳(RSI={rsi:.1f}, 微积分速度={c_v:+.2f})，顺势低吸做多"
        reasons = ["1H单边主升结构", "EMA价值区放量承接", "微积分动能企稳"]

    # Setup 2: ⚡ 阻力抛压做空 (Resistance Exhaustion)
    elif regime == "BEAR_TREND" and (px >= ema21 * 0.992 and px <= ema55 * 1.006) and (44.0 <= rsi <= 62.0) and (is_bear_c or upper_wick >= 0.20) and not cooldown_short and not is_high_jerk_shock:
        strategy_tag = "⚡ 阻力抛压"
        raw_alpha_score = min(raw_alpha_score, -2.4)
        strategy_desc = f"【1H机构顺势】反弹测试EMA21/55阻力带右侧收阴遇阻(RSI={rsi:.1f}, 微积分速度={c_v:+.2f})，顺势做空"
        reasons = ["1H单边主跌结构", "EMA阻力带量能衰竭遇阻", "微积分动能向下发散"]

    # Setup 3: 🚀 动量挤压突破 (Momentum Squeeze Breakout)
    elif (px > ema9) and (55.0 <= rsi <= 74.0) and vol_ratio >= 1.3 and macd_accel > 0 and is_bull_c and not cooldown_long and (c_a >= -0.2) and not is_high_jerk_shock:
        strategy_tag = "🚀 动量突破"
        raw_alpha_score = max(raw_alpha_score, 2.5)
        strategy_desc = f"【动量爆发】放量突破前高动能发散(量能={vol_ratio}x, 微积分加速度={c_a:+.2f})，顺势追涨"
        reasons = ["动量主升放量突破", f"成交量放大 {vol_ratio} 倍", "微积分正加速度扩张"]

    # Setup 4: 🌪️ 破位放量追空 (Breakdown Acceleration)
    elif (px < ema9) and (26.0 <= rsi <= 45.0) and vol_ratio >= 1.3 and macd_accel < 0 and is_bear_c and not cooldown_short and (c_a <= 0.2) and not is_high_jerk_shock:
        strategy_tag = "🌪️ 破位追空"
        raw_alpha_score = min(raw_alpha_score, -2.5)
        strategy_desc = f"【空头加速】击穿前低关键支撑放量下泄(量能={vol_ratio}x, 微积分加速度={c_a:+.2f})，顺势破位做空"
        reasons = ["空头破位下泄加速", f"放量破位 (量能 {vol_ratio}x)", "微积分负加速度下泄"]

    # Setup 5: 💎 极值均值回归 (Extreme Mean Reversion)
    elif vwap_bias <= -0.85 and rsi <= 30.0 and (is_bull_c or lower_wick >= 0.28) and not cooldown_long:
        strategy_tag = "💎 极值回归"
        raw_alpha_score = max(raw_alpha_score, 2.3)
        strategy_desc = f"【VWAP极值偏离】量价严重负乖离({vwap_bias:+.2f}%)且RSI超卖({rsi:.1f})，微积分减速企稳收阳"
        reasons = [f"VWAP严重负偏离 ({vwap_bias:+.2f}%)", "RSI极值超卖区间", "下引线止跌确认"]

    # Setup 6: 🛡️ 流动性猎杀反转 (Liquidity Sweep Reversal)
    elif vwap_bias >= 0.85 and rsi >= 70.0 and (is_bear_c or upper_wick >= 0.28) and not cooldown_short:
        strategy_tag = "🛡️ 冲高反转"
        raw_alpha_score = min(raw_alpha_score, -2.3)
        strategy_desc = f"【冲高衰竭】刺破正乖离极值区({vwap_bias:+.2f}%)受阻长上影线回落(RSI={rsi:.1f})，微积分动能钝化反转"
        reasons = [f"VWAP严重正偏离 ({vwap_bias:+.2f}%)", "RSI严重超买动能钝化", "上引线受阻承压"]

    # Adaptive strategy enablement and bounded weighting are applied after classification.
    if strategy_tag != "⚪ 观望":
        if strat_enabled.get(strategy_tag, True) is False:
            return 0.0, "HOLD", ["自进化配置已停用该策略"], "⚪ 观望", f"【自进化干预】{strategy_tag} 当前已停用"
        strategy_weight = clamp(strat_weights.get(strategy_tag, 1.0), 0.7, 1.3, 1.0)
        raw_alpha_score *= strategy_weight

    final_score = round(raw_alpha_score, 1)

    # Action Decision based on Adaptive Entry Threshold
    action = "HOLD"
    if final_score >= entry_threshold and not cooldown_long:
        action = "BUY_LONG"
    elif final_score <= -entry_threshold and not cooldown_short:
        action = "SELL_SHORT"

    return final_score, action, reasons, strategy_tag, strategy_desc

def single_trader_cycle(func):
    """Prevent cron/manual overlap across the complete order-management cycle."""
    def wrapped(*args, **kwargs):
        os.makedirs(DATA_DIR, exist_ok=True)
        lock_handle = open(TRADER_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            print("[Trader] Skip: another portfolio cycle is still running")
            return None
        try:
            now_slot = int(time.time()) // 900
            if os.path.exists(TRADER_SLOT_FILE):
                try:
                    with open(TRADER_SLOT_FILE, "r", encoding="utf-8") as f:
                        slot_state = json.load(f)
                    same_slot = int(slot_state.get("slot", -1)) == now_slot
                    recently_started = int(time.time()) - int(slot_state.get("started_at", 0) or 0) < 120
                    if same_slot and recently_started:
                        print("[Trader] Skip: duplicate trigger detected in this 15-minute slot")
                        return None
                except Exception:
                    pass
            with open(TRADER_SLOT_FILE, "w", encoding="utf-8") as f:
                json.dump({"slot": now_slot, "started_at": int(time.time()), "pid": os.getpid()}, f)
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write(str(os.getpid()))
            lock_handle.flush()
            cycle_environment = freeze_okx_environment()
            print(f"[Trader] OKX environment frozen for cycle: {cycle_environment.mode.upper()} / {cycle_environment.identity}")
            return func(*args, **kwargs)
        finally:
            unfreeze_okx_environment()
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    return wrapped


# =============================================================================
# Master Portfolio Execution Loop
# =============================================================================
@single_trader_cycle
@trade_lock.serialized
def execute_portfolio():
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_bj)
    timestamp_full = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 0. Clean Stale Open Orders & Harvest Real-time News Sentiment
    orders_ok, orders_error = clean_stale_open_orders()
    if not orders_ok:
        print(f"[Trader] Abort: unable to verify/cancel stale open orders: {orders_error}")
        return None
    try:
        harvester_script = os.path.join(WORKSPACE_DIR, "scripts", "news_sentiment_harvester.py")
        if os.path.exists(harvester_script):
            subprocess.run(f"python3 {harvester_script}", shell=True, capture_output=True, text=True, timeout=12)
    except Exception as e:
        print(f"News Harvester sync warning: {e}")

    # 1. Fetch Real Positions. A failed account query aborts the complete cycle.
    positions_ok, all_positions, positions_error = query_positions()
    if not positions_ok:
        print(f"[Trader] Abort: unable to verify exchange positions: {positions_error}")
        return None
    real_pos_dict = {}
    real_long_count = 0
    real_short_count = 0

    if isinstance(all_positions, list):
        for p in all_positions:
            pos_sz = float(p.get("pos", 0) or 0)
            if pos_sz > 0:
                side = p.get("posSide", "net").lower()
                inst_id = p.get("instId")
                if inst_id in real_pos_dict:
                    print(f"[Trader] Abort: simultaneous long/short positions for {inst_id} are not supported")
                    return None
                real_pos_dict[inst_id] = p
                if "long" in side:
                    real_long_count += 1
                elif "short" in side:
                    real_short_count += 1

    active_pos_count = len(real_pos_dict)
    long_count = real_long_count
    short_count = real_short_count
    limits = execution_limits()
    max_active_positions = limits['max_positions']
    max_same_direction = limits['max_same_direction']
    single_asset_margin = limits['single_asset_margin']

    pending_result = run_cmd_result(okx_private_command("okx swap orders --json"), timeout=20)
    if not pending_result["ok"] or not isinstance(pending_result.get("data"), list):
        print(f"[Trader] Abort: unable to verify pending orders: {pending_result['stderr'] or pending_result['stdout']}")
        return None
    pending_orders = pending_result["data"]
    pending_inst_ids = set()
    pending_long_count = 0
    pending_short_count = 0
    if isinstance(pending_orders, list):
        for order in pending_orders:
            if str(order.get("state", "live")).lower() not in {"live", "partially_filled"}:
                continue
            inst_id = str(order.get("instId", ""))
            if inst_id:
                pending_inst_ids.add(inst_id)
            pos_side = str(order.get("posSide", "")).lower()
            if pos_side == "long":
                pending_long_count += 1
            elif pos_side == "short":
                pending_short_count += 1
    reserved_slot_count = active_pos_count + len(pending_inst_ids)
    reserved_long_count = long_count + pending_long_count
    reserved_short_count = short_count + pending_short_count
    # One correlated crypto direction per 15M execution cycle. This is a
    # portfolio concentration guard, not a signal veto: the first accepted
    # candidate claims the group; later same-direction candidates are rejected.
    related_cycle_claims = set()

    bal_res = run_json_cmd(okx_private_command("okx account balance --json"))
    if not isinstance(bal_res, list):
        print("[Trader] Abort: unable to verify account balance")
        return None
    usdt_available = 0.0
    if bal_res and isinstance(bal_res, list) and len(bal_res) > 0:
        for d in bal_res[0].get("details", []):
            if d.get("ccy") == "USDT":
                usdt_available = float(d.get("availBal", 0.0))
                break

    market.begin_signal_frame()
    # 2. Parallel fetch for the configured crypto universe
    with ThreadPoolExecutor(max_workers=len(TARGET_INSTRUMENTS)) as executor:
        all_factors = list(executor.map(lambda item: fetch_single_instrument_data(item, all_positions, usdt_available), TARGET_INSTRUMENTS))

    # 3. Process Positions & Dynamic Trailing Exits
    executed_actions = []
    environment_notices = []
    trackers = load_trackers()
    stale_tracker_count = prune_trackers(trackers, real_pos_dict)
    reconcile_scale_trackers(trackers, real_pos_dict)
    if stale_tracker_count:
        executed_actions.append(f"清理 {stale_tracker_count} 条已失效持仓追踪记录")
    for f in all_factors:
        curr_pos = f["position"]
        if curr_pos:
            manage_position_tp_and_trailing(f, curr_pos, trackers, timestamp_full, executed_actions)
    save_trackers(trackers)

    # 4. Check Circuit Breaker & Batch AI Brain Scan (Including Active Positions Detail)
    cb_active, cb_reason = is_circuit_breaker_active()

    brain_cache = {}
    # One LLM call covers the full six-instrument universe and all active positions.
    if not cb_active and execute_batch_ai_brain_cycle:
        try:
            pos_desc = f"当前系统总持仓 {active_pos_count}/{MAX_CONCURRENT_POSITIONS} (多{long_count}/空{short_count})"
            active_pos_list = []
            for f in all_factors:
                position = f.get("position")
                if not position:
                    continue
                position_payload = dict(position)
                tracker = trackers.get(f"{f['instId']}_{position.get('side', '')}", {})
                position_payload["trailingStopPx"] = tracker.get("trailingStopPx")
                active_pos_list.append(position_payload)
            with trade_lock.inference_window():
                brain_cache = execute_batch_ai_brain_cycle(pos_desc, active_pos_list, usdt_available=usdt_available) or {}
            # The independent guard may have changed positions while the model ran.
            trackers = load_trackers()
            if brain_cache:
                refreshed_ok, refreshed_positions, refreshed_error = query_positions()
                if not refreshed_ok:
                    executed_actions.append(f"AI持仓管理跳过：无法刷新真实仓位 ({refreshed_error})")
                else:
                    refreshed_pos_dict = {
                        p.get("instId"): p for p in refreshed_positions
                        if float(p.get("pos", 0) or 0) > 0
                    }
                    all_positions = refreshed_positions
                    for item in all_factors:
                        raw = refreshed_pos_dict.get(item['instId'])
                        item['position'] = ({**raw, 'side': raw.get('posSide'), 'pos': float(raw['pos']),
                                             'avgPx': float(raw.get('avgPx') or 0), 'upl': float(raw.get('upl') or 0),
                                             'uplRatio': float(raw.get('uplRatio') or 0)} if raw else None)
                    execute_ai_position_management(refreshed_pos_dict, trackers, timestamp_full, executed_actions)
                    save_trackers(trackers)
            else:
                reason = get_last_inference_error() or "未取得有效新鲜AI决策"
                executed_actions.append(f"本轮AI未就绪：{reason}；禁止复用旧持仓指令")
        except Exception as e:
            print(f"[AI Brain Batch Scan Warning] {e}")

    if not cb_active:
        availability = support.pool_support(TARGET_INSTRUMENTS, market._selected().mode, refresh=True)
        for f in all_factors:
            state = availability["items"][f["instId"]]
            if not state["can_open"]:
                environment_notices.append(f"{f['name']}：{state['label']}，仅观察")
                continue
            if not f.get("market_data_valid"):
                environment_notices.append(f"{f['name']}：执行行情不可用，本轮不新增风险")
                continue
            asset_type = f.get("type", "crypto")
            if not is_tradfi_market_liquid(asset_type):
                continue

            score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
            inst_id = f["instId"]
            curr_pos = f["position"]
            prec = f["precision"]
            ct_val = f["ctVal"]
            profile = ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"])
            
            adaptive_cfg = load_adaptive_config()
            tp_mult = adaptive_cfg.get("tp_atr_mult", profile.get("tp_atr_mult", 2.2))
            sl_mult = adaptive_cfg.get("sl_atr_mult", profile.get("sl_atr_mult", 1.3))

            atr = max(f["atr"], f["price"] * 0.005)
            min_prof = adaptive_cfg.get("min_profit_ratio", profile.get("min_profit_ratio", 0.008))
            tp_dist = max(atr * tp_mult, f["price"] * min_prof)
            sl_dist = atr * sl_mult

            # Gate 1: LLM AI Brain Full Execution Authority
            # The model proposes direction/geometry. Final price, quantity, account risk and exchange leverage are revalidated by entry_gateway.
            ai_info = brain_cache.get(inst_id) if isinstance(brain_cache, dict) else None
            if not ai_info or "decision" not in ai_info:
                print(f"[AI Brain 全权拦截] {f['name']} 本轮无有效新鲜 AI 决策，禁止开仓")
                continue

            ai_decision = ai_info["decision"]
            ai_act = str(ai_decision.get("action", "WAIT")).upper()
            ai_conf = float(ai_decision.get("confidence", 0) or 0)
            ai_reason = ai_decision.get("summary_reason", "")

            f["ai_thought"] = ai_info.get("thought_process", {})
            f["ai_reason"] = ai_reason
            f["ai_confidence"] = ai_conf

            # Direct Action Assignment from LLM
            if ai_act in ["BUY_LONG", "SELL_SHORT"]:
                action = ai_act
                strat_tag = f"🧠 AI大脑({ai_act})"
                strat_desc = f"【AI全权决策】{ai_reason}"
                print(f"[AI Brain 全权指令] {f['name']} AI 直接指示 {action} (置信度={ai_conf}%, 理由: {ai_reason})")
            else:
                action = "HOLD"
                strat_tag = "🤖 AI观望"
                strat_desc = f"AI大脑判定当前无高确定性机会({ai_reason})"
                continue

            # Requested quantity only; final quantity is floored by the deterministic risk gateway.
            actual_sz = f["sz"]
            ai_margin = float(ai_decision.get("margin_usdt", 0.0) or 0.0)
            horizon = str(ai_decision.get("horizon", "swing")).lower()
            # The model may suggest a value, but the default is horizon-aware:
            # short trades use capital more efficiently; swing trades leave more
            # room for noise. Final risk is still calculated from stop distance.
            from scripts.strategy_modes import leverage_for
            default_leverage = leverage_for(horizon)
            ai_lever = leverage_for(horizon, ai_decision.get("leverage") if ai_decision.get("leverage") is not None else None, ai_conf)
            
            # If AI planned margin & leverage, calculate custom contract size
            if ai_margin > 0 and ai_lever >= 1.0 and f["price"] > 0 and ct_val > 0:
                planned_notional = ai_margin * ai_lever
                calculated_sz = int(round(planned_notional / (f["price"] * ct_val)))
                if calculated_sz > 0:
                    # Bounded risk clamp: Between 0.5x and 2.0x base size to prevent extreme outliers
                    min_allowed_sz = max(1, int(f["sz"] * 0.5))
                    max_allowed_sz = max(1, int(f["sz"] * 2.0))
                    actual_sz = max(min_allowed_sz, min(max_allowed_sz, calculated_sz))

            if actual_sz <= 0:
                continue

            # Long Execution (Initial Entry or Strict Pyramiding Scale-In)
            if action == "BUY_LONG":
                is_scale_in = False
                allow_entry = False

                # Case A: Standard Initial Entry (No existing position & slot available)
                related_key = 'correlated_crypto_long' if inst_id.split('-')[0].upper() in {'BTC','ETH','SOL','DOGE','SUI','XRP'} else f"{inst_id.split('-')[0].upper()}_long"
                if not curr_pos and inst_id not in pending_inst_ids and related_key not in related_cycle_claims and reserved_slot_count < max_active_positions and reserved_long_count < max_same_direction:
                    allow_entry = True  # Evidence/geometry and account risk, never model score.

                # Case B: Strict Pyramiding Scale-In (Existing long position in profit/breakeven)
                elif curr_pos and str(curr_pos.get("side", "")).lower() == "long" and inst_id not in pending_inst_ids:
                    pos_upl = float(curr_pos.get("upl", 0.0) or 0.0)
                    pos_upl_ratio = float(curr_pos.get("uplRatio", 0.0) or 0.0)
                    pos_avg_px = float(curr_pos.get("avgPx", 0.0) or 0.0)
                    curr_margin = float(curr_pos.get("margin", 0.0) or 0.0)
                    tracker = trackers.get(f"{inst_id}_long", {})
                    scale_count = int(tracker.get("scale_count", 0))
                    trailing_sl = float(tracker.get("trailingStopPx", 0.0) or 0.0)

                    # Ironclad Pyramiding Rules:
                    # 1. Base position must be in profit (ROI >= +0.8%) OR stop-loss already moved to/above avg entry px (No-risk trade).
                    # 2. Maximum 1 scale-in per position to prevent overconcentration.
                    # 3. Combined margin must not exceed MAX_SINGLE_ASSET_MARGIN.
                    # 4. Model score is diagnostic only; final account risk is authoritative.
                    # 5. Calculus Momentum & Probability Gateway: Acceleration a >= -0.25 and Continuation Prob >= 40%
                    calculus_accel_ok, c_accel, p_cont = _scale_in_calculus_gate(f.get("calculus"), "long")

                    is_profit_or_breakeven = (pos_upl > 0 and pos_upl_ratio >= MIN_SCALE_IN_PROFIT_RATIO) or (trailing_sl > 0 and trailing_sl >= pos_avg_px)
                    planned_margin = ai_margin if ai_margin > 0 else (actual_sz * ct_val * f["price"] / max(1.0, ai_lever))
                    within_margin_cap = (curr_margin + planned_margin) <= single_asset_margin

                    if is_profit_or_breakeven and scale_count < MAX_SCALE_IN_COUNT and within_margin_cap and calculus_accel_ok:
                        allow_entry = True
                        is_scale_in = True
                        print(f"[Pyramiding] {f['name']} 满足顺势浮盈加多条件: 底仓浮盈={pos_upl:+.2f}U ({pos_upl_ratio*100:+.1f}%), 已加仓{scale_count}次, 微积分加速度={c_accel:+.2f}, 延续概率={p_cont:.1f}%, 计划加仓{actual_sz}张")
                    else:
                        if not is_profit_or_breakeven:
                            print(f"[Pyramiding 拦截] {f['name']} 底仓未达浮盈保本门禁 (浮盈={pos_upl:+.2f}U ROI={pos_upl_ratio*100:+.1f}%), 严禁逆势加仓")
                        elif scale_count >= MAX_SCALE_IN_COUNT:
                            print(f"[Pyramiding 拦截] {f['name']} 已达最大加仓次数 ({scale_count}/{MAX_SCALE_IN_COUNT})")
                        elif not within_margin_cap:
                            print(f"[Pyramiding 拦截] {f['name']} 加仓后总保证金将超限 ({curr_margin + planned_margin:.1f} > {single_asset_margin}U)")
                        elif not calculus_accel_ok:
                            print(f"[Pyramiding 拦截] {f['name']} 数理数据无效、动能衰竭或延续概率偏低 (加速度={c_accel:+.2f}, 概率={p_cont:.1f}%)，禁止追多加仓")

                if not allow_entry:
                    record_entry_rejection(inst_id, ai_info, "entry_gate_blocked", active_position=bool(curr_pos), pending_order=inst_id in pending_inst_ids, slots_full=reserved_slot_count >= max_active_positions, correlated_cycle_claim=related_key in related_cycle_claims, action=action)
                if not allow_entry:
                    record_entry_rejection(inst_id, ai_info, "entry_gate_blocked", active_position=bool(curr_pos), pending_order=inst_id in pending_inst_ids, slots_full=reserved_slot_count >= max_active_positions, action=action)
                if allow_entry:
                    limit_px = float(ai_decision.get("entry_price") or f.get("bidPx") or f["price"])
                    tp_px = float(ai_decision.get("take_profit_price") or (limit_px + tp_dist))
                    sl_px = float(ai_decision.get("stop_loss_price") or (limit_px - sl_dist))

                    candidate_id = ai_decision.get('candidate_id')
                    # Program-selected plans may be rounded to ticks, never silently repaired into another setup.
                    if candidate_id and not 0 < sl_px < limit_px < tp_px:
                        executed_actions.append(f"[{f['name']}] 程序候选取整后价格结构失效，拒绝重写止损或目标")
                        continue
                    # Model-independent proposals may be repaired to a valid
                    # side geometry; immutable program candidates may not.
                    if not candidate_id and sl_px >= limit_px:
                        sl_px = round(limit_px - max(sl_dist, f["price"] * 0.012), prec)
                    if not candidate_id and tp_px <= limit_px:
                        tp_px = round(limit_px + max(tp_dist, f["price"] * 0.024), prec)

                    accepted, order_ref = submit_protected_limit_order(inst_id, "buy", "long", actual_sz, limit_px, tp_px, sl_px, risk_budget_usdt=f["risk_per_trade_usd"], decision_id=ai_info.get("decision_id"), decision_at=ai_info.get("data_as_of"), allow_demo_translation=not bool(ai_decision.get('candidate_id')), horizon=horizon, setup=ai_decision.get('setup') or ai_decision.get('strategy_type'))
                    if accepted:
                        actual_sz = LAST_ENTRY_PLAN['size']
                        limit_px, sl_px, tp_px = LAST_ENTRY_PLAN['entry'], LAST_ENTRY_PLAN['stop'], LAST_ENTRY_PLAN['take_profit']
                        if is_scale_in:
                            tracker = trackers.get(f"{inst_id}_long", {})
                            pending = tracker.setdefault("pending_scale_orders", [])
                            if str(order_ref) not in {str(item.get("order_id")) for item in pending if isinstance(item, dict)}:
                                pending.append({"order_id": str(order_ref), "requested_size": actual_sz, "submitted_at": time.time()})
                            save_trackers(trackers)
                            executed_actions.append(f"[{f['name']}] 🚀 AI顺势浮盈金字塔加多委托已提交，等待成交确认后计入加仓次数（数量经最终风险预算裁剪）@{limit_px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
                            if notify_trade_open:
                                notify_trade_open(
                                    inst=f["name"],
                                    side="多 (顺势加多)",
                                    sz=actual_sz,
                                    px=limit_px,
                                    strategy="🚀 顺势金字塔加多",
                                    reason=str(ai_reason),
                                    tp_px=tp_px,
                                    sl_px=sl_px,
                                    leverage=LAST_ENTRY_PLAN.get("leverage", 3),
                                )
                        else:
                            executed_actions.append(f"[{f['name']}] AI限价多单已提交待成交（数量经最终风险预算裁剪）@{limit_px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
                            pending_inst_ids.add(inst_id)
                            related_cycle_claims.add(related_key)
                            reserved_slot_count += 1
                            reserved_long_count += 1
                            if notify_trade_open:
                                notify_trade_open(
                                    inst=f["name"],
                                    side="多",
                                    sz=actual_sz,
                                    px=limit_px,
                                    strategy=strat_tag,
                                    reason=str(ai_reason),
                                    tp_px=tp_px,
                                    sl_px=sl_px,
                                    leverage=LAST_ENTRY_PLAN.get("leverage", 3),
                                )
                    else:
                        executed_actions.append(f"[{f['name']}] AI限价多单提交失败: {order_ref}")

            # Short Execution (Initial Entry or Strict Pyramiding Scale-In)
            elif action == "SELL_SHORT":
                is_scale_in = False
                allow_entry = False

                # Case A: Standard Initial Entry
                related_key = 'correlated_crypto_short' if inst_id.split('-')[0].upper() in {'BTC','ETH','SOL','DOGE','SUI','XRP'} else f"{inst_id.split('-')[0].upper()}_short"
                if not curr_pos and inst_id not in pending_inst_ids and related_key not in related_cycle_claims and reserved_slot_count < max_active_positions and reserved_short_count < max_same_direction:
                    allow_entry = True  # Evidence/geometry and account risk, never model score.

                # Case B: Strict Pyramiding Scale-In (Existing short position in profit/breakeven)
                elif curr_pos and str(curr_pos.get("side", "")).lower() == "short" and inst_id not in pending_inst_ids:
                    pos_upl = float(curr_pos.get("upl", 0.0) or 0.0)
                    pos_upl_ratio = float(curr_pos.get("uplRatio", 0.0) or 0.0)
                    pos_avg_px = float(curr_pos.get("avgPx", 0.0) or 0.0)
                    curr_margin = float(curr_pos.get("margin", 0.0) or 0.0)
                    tracker = trackers.get(f"{inst_id}_short", {})
                    scale_count = int(tracker.get("scale_count", 0))
                    trailing_sl = float(tracker.get("trailingStopPx", 0.0) or 0.0)

                    is_profit_or_breakeven = (pos_upl > 0 and pos_upl_ratio >= MIN_SCALE_IN_PROFIT_RATIO) or (trailing_sl > 0 and trailing_sl <= pos_avg_px)
                    planned_margin = ai_margin if ai_margin > 0 else (actual_sz * ct_val * f["price"] / max(1.0, ai_lever))
                    within_margin_cap = (curr_margin + planned_margin) <= single_asset_margin

                    calculus_accel_ok, c_accel, p_break = _scale_in_calculus_gate(f.get("calculus"), "short")

                    if is_profit_or_breakeven and scale_count < MAX_SCALE_IN_COUNT and within_margin_cap and calculus_accel_ok:
                        allow_entry = True
                        is_scale_in = True
                        print(f"[Pyramiding] {f['name']} 满足顺势浮盈加空条件: 底仓浮盈={pos_upl:+.2f}U ({pos_upl_ratio*100:+.1f}%), 已加仓{scale_count}次, 微积分加速度={c_accel:+.2f}, 击穿概率={p_break:.1f}%, 计划加仓{actual_sz}张")
                    else:
                        if not is_profit_or_breakeven:
                            print(f"[Pyramiding 拦截] {f['name']} 底仓未达浮盈保本门禁 (浮盈={pos_upl:+.2f}U ROI={pos_upl_ratio*100:+.1f}%), 严禁逆势加仓")
                        elif scale_count >= MAX_SCALE_IN_COUNT:
                            print(f"[Pyramiding 拦截] {f['name']} 已达最大加仓次数 ({scale_count}/{MAX_SCALE_IN_COUNT})")
                        elif not within_margin_cap:
                            print(f"[Pyramiding 拦截] {f['name']} 加仓后总保证金将超限 ({curr_margin + planned_margin:.1f} > {single_asset_margin}U)")
                        elif not calculus_accel_ok:
                            print(f"[Pyramiding 拦截] {f['name']} 数理数据无效、动能失速企稳或击穿概率偏低 (加速度={c_accel:+.2f}, 概率={p_break:.1f}%)，禁止追空加仓")

                if allow_entry:
                    limit_px = float(ai_decision.get("entry_price") or f.get("askPx") or f["price"])
                    tp_px = float(ai_decision.get("take_profit_price") or (limit_px - tp_dist))
                    sl_px = float(ai_decision.get("stop_loss_price") or (limit_px + sl_dist))

                    candidate_id = ai_decision.get("candidate_id")
                    # Hard check: For SELL SHORT, OKX strictly requires tp_px < limit_px < sl_px
                    if candidate_id and not 0 < tp_px < limit_px < sl_px:
                        executed_actions.append(f"[{f['name']}] 程序候选取整后价格结构失效，拒绝重写止损或目标")
                        continue
                    # Model-independent proposals may be repaired to a valid
                    # side geometry; immutable program candidates may not.
                    if not candidate_id and sl_px <= limit_px:
                        sl_px = round(limit_px + max(sl_dist, f["price"] * 0.012), prec)
                    if not candidate_id and tp_px >= limit_px:
                        tp_px = round(limit_px - max(tp_dist, f["price"] * 0.024), prec)

                    accepted, order_ref = submit_protected_limit_order(inst_id, "sell", "short", actual_sz, limit_px, tp_px, sl_px, risk_budget_usdt=f["risk_per_trade_usd"], decision_id=ai_info.get("decision_id"), decision_at=ai_info.get("data_as_of"), allow_demo_translation=not bool(ai_decision.get("candidate_id")), horizon=horizon)
                    if accepted:
                        actual_sz = LAST_ENTRY_PLAN['size']
                        limit_px, sl_px, tp_px = LAST_ENTRY_PLAN['entry'], LAST_ENTRY_PLAN['stop'], LAST_ENTRY_PLAN['take_profit']
                        if is_scale_in:
                            tracker = trackers.get(f"{inst_id}_short", {})
                            pending = tracker.setdefault("pending_scale_orders", [])
                            if str(order_ref) not in {str(item.get("order_id")) for item in pending if isinstance(item, dict)}:
                                pending.append({"order_id": str(order_ref), "requested_size": actual_sz, "submitted_at": time.time()})
                            save_trackers(trackers)
                            executed_actions.append(f"[{f['name']}] 🌪️ AI顺势浮盈金字塔加空委托已提交，等待成交确认后计入加仓次数（数量经最终风险预算裁剪）@{limit_px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
                            if notify_trade_open:
                                notify_trade_open(
                                    inst=f["name"],
                                    side="空 (顺势加空)",
                                    sz=actual_sz,
                                    px=limit_px,
                                    strategy="🌪️ 顺势金字塔加空",
                                    reason=str(ai_reason),
                                    tp_px=tp_px,
                                    sl_px=sl_px,
                                    leverage=LAST_ENTRY_PLAN.get("leverage", 3),
                                )
                        else:
                            executed_actions.append(f"[{f['name']}] AI限价空单已提交待成交（数量经最终风险预算裁剪）@{limit_px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
                            pending_inst_ids.add(inst_id)
                            reserved_slot_count += 1
                            reserved_short_count += 1
                            if notify_trade_open:
                                notify_trade_open(
                                    inst=f["name"],
                                    side="空",
                                    sz=actual_sz,
                                    px=limit_px,
                                    strategy=strat_tag,
                                    reason=str(ai_reason),
                                    tp_px=tp_px,
                                    sl_px=sl_px,
                                    leverage=LAST_ENTRY_PLAN.get("leverage", 3),
                                )
                    else:
                        executed_actions.append(f"[{f['name']}] AI限价空单提交失败: {order_ref}")

    from scripts.decision_reporting import summarize, format_summary, format_actions
    decision_cycle = summarize(brain_cache, environment_notices,
        unavailable_reason=cb_reason if cb_active else get_last_inference_error() if not brain_cache else '',
        circuit_breaker=cb_active)
    decision_cycle['timestamp'] = timestamp_full
    decision_cycle['executed_actions'] = list(executed_actions)
    from scripts.wait_audit import public_status as wait_status
    latest_wait_status = wait_status(market._selected().identity)
    if brain_cache:
        decision_cycle['no_entry_candidate_streak'] = latest_wait_status.get('no_entry_candidate_streak', 0)
        decision_cycle['wait_alert'] = latest_wait_status.get('alert', False)
        decision_cycle['wait_diagnostics'] = latest_wait_status.get('diagnostics')
    # 5. Persist Latest State for Web Monitoring Dashboard
    state_payload = {
        "timestamp": timestamp_full,
        "active_positions_count": active_pos_count,
        "max_positions": max_active_positions,
        "long_count": long_count,
        "short_count": short_count,
        "circuit_breaker": {"active": cb_active, "reason": cb_reason},
        "executed_actions": executed_actions,
        "decision_cycle": decision_cycle,
        "environment_notices": environment_notices,
        "instruments": []
    }

    for f in all_factors:
        score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
        state_payload["instruments"].append({
            "name": f["name"],
            "instId": f["instId"],
            "type": f["type"],
            "price": f["price"],
            "rsi": round(f["rsi"], 1),
            "rsi_7": round(f.get("rsi_7", 50.0), 1),
            "vwap_bias": round(f.get("vwap_bias", 0.0), 2),
            "macd_hist": f.get("macd_hist", 0.0),
            "macd_accel": f.get("macd_accel", 0.0),
            "obv_flow": f.get("obv_flow", "NEUTRAL"),
            "bb_bandwidth": f.get("bb_bandwidth", 0.0),
            "vol_ratio": f.get("vol_ratio", 1.0),
            "market_regime": f.get("market_regime", "CHOP"),
            "structure_1h": f.get("structure_1h", "CHOP"),
            "trend_1h": "多头" if f.get("trend_1h_bullish") else "空头",
            "trend_4h": "多头" if f.get("trend_4h_bullish") else "空头",
            "score": score,
            "action": action,
            "strategy": strat_tag,
            "desc": strat_desc,
            "position": f["position"]
        })

    with open(os.path.join(DATA_DIR, "trading_state.json"), "w", encoding="utf-8") as f:
        json.dump(state_payload, f, ensure_ascii=False, indent=2)

    strategy_evidence.best_effort(market._selected().identity, 'execution_cycle',
        {'timestamp':timestamp_full,'actions':executed_actions,'decision_cycle':decision_cycle})

    # 6. Always Sync Full Lifecycle Ledger and SQLite DB in Realtime
    try:
        sync_script = os.path.join(WORKSPACE_DIR, "scripts", "sync_full_ledger.py")
        if os.path.exists(sync_script):
            subprocess.run(f"python3 {sync_script}", shell=True, capture_output=True, text=True, timeout=15)
        db_script = os.path.join(WORKSPACE_DIR, "scripts", "db_manager.py")
        if os.path.exists(db_script):
            subprocess.run(f"python3 {db_script}", shell=True, capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"[Ledger Sync Warning] {e}")

    log_entry = f"[{timestamp_full}] 巡检完成 | 持仓 {active_pos_count}/{MAX_CONCURRENT_POSITIONS} (多{long_count}/空{short_count}) | 动作: {format_actions(executed_actions)} | 决策: {format_summary(decision_cycle)}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
    print(log_entry.strip())
    return {"completed": True, "timestamp": timestamp_full, "actions": executed_actions}

if __name__ == "__main__":
    # Account/quote aborts and lock skips must not be reported as completed work.
    # No exchange write is retried here; the next normal schedule may re-read.
    if execute_portfolio() is None:
        raise SystemExit(1)
