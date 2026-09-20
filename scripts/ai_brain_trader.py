#!/usr/bin/env python3
"""
OKXQuant AI Brain Six-Crypto Quantitative Trading Decision Engine (ai_brain_trader.py)
Batch ingests six crypto perpetuals into one macro-context LLM call.
Maintains a validated live decision cache and durable Web audit history.
"""

# Standalone scheduler children must not depend on an inherited PYTHONPATH.
import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[1])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)


import os
from okx_runtime import replace_cli_prefix as okx_private_command
import sys
import json
import time
import datetime
import urllib.request
import public_market as market
import instrument_support as support
import algo_reader
from scripts import trade_lock, strategy_evidence, trading_prompt, wait_audit, capital_pool
import subprocess
import tempfile
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from okxquant_backend.config import settings as standalone_settings
except ImportError:
    standalone_settings = None

WORKSPACE_DIR = PROJECT_ROOT
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
AI_DECISION_CACHE_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
AI_DECISION_HISTORY_FILE = os.path.join(DATA_DIR, "ai_brain_history.json")
AI_POSITION_MANAGEMENT_FILE = os.path.join(DATA_DIR, "ai_position_management.json")
AI_LAST_PROMPT_FILE = os.path.join(DATA_DIR, "ai_brain_last_prompt.txt")
FACTOR_LIBRARY_FILE = os.path.join(DATA_DIR, "factor_library_snapshot.json")
NEWS_SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
AI_MEMORY_MD_FILE = os.path.join(DATA_DIR, "AI_TRADING_MEMORY.md")
CALCULUS_SNAPSHOT_FILE = os.path.join(DATA_DIR, "calculus_snapshot.json")
AI_MEMORY_FILE = os.path.join(DATA_DIR, "ai_trading_memory.json")
PROMPT_OVERRIDE_FILE = os.path.join(DATA_DIR, "system_prompt_override.txt")
AI_BRAIN_LOCK_FILE = os.path.join(DATA_DIR, ".ai_brain_cycle.lock")
DECISION_MAX_AGE_SECONDS = 300
LAST_INFERENCE_ERROR = ""


def get_last_inference_error() -> str:
    """A safe reason for this process's most recent inference attempt."""
    return LAST_INFERENCE_ERROR

from instrument_pool import load_instruments
from prompt_library import active_profile
from okxquant_gateway.telemetry import ModelCallTelemetry

TARGET_INSTRUMENTS = load_instruments()

def atomic_write_json(path: str, payload: Any) -> None:
    """Replace JSON atomically so readers never observe a partial cache."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".ai-brain-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def single_brain_cycle(func):
    """Prevent overlapping cron runs from overwriting the shared decision cache."""
    def wrapped(*args, **kwargs):
        # Read-only prompt views are cross-platform; execution still requires the real POSIX lock.
        import fcntl
        global LAST_INFERENCE_ERROR
        LAST_INFERENCE_ERROR = ""
        os.makedirs(DATA_DIR, exist_ok=True)
        lock_handle = open(AI_BRAIN_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            LAST_INFERENCE_ERROR = "已有推理周期运行，本轮未取得执行锁"
            print("[AI Brain Batch] Skip: another inference cycle is still running")
            return None
        try:
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write(str(os.getpid()))
            lock_handle.flush()
            atomic_write_json(AI_DECISION_CACHE_FILE, {})
            atomic_write_json(AI_POSITION_MANAGEMENT_FILE, {'timestamp': 0, 'instructions': []})
            atomic_write_json(os.path.join(DATA_DIR, 'trading_output_validation.json'), {'status':'pending','contract_version':trading_prompt.VERSION})
            return func(*args, **kwargs)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    return wrapped


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else default
    except (TypeError, ValueError):
        return default


def is_same_direction_scale_request(position_side: str, action: str) -> bool:
    """Allow only same-direction scale-in requests to reach execution hard gateways."""
    side = str(position_side or "").lower()
    decision = str(action or "").upper()
    return (side == "long" and decision == "BUY_LONG") or (side == "short" and decision == "SELL_SHORT")


def get_cpa_client_config() -> Tuple[str, str]:
    """Resolve LLM credentials only from process environment or local .env."""
    try:
        from okxquant_backend.llm_manager import get_active_llm_runtime
        active_llm = get_active_llm_runtime()
        if active_llm.get("base_url"):
            return active_llm["base_url"], active_llm.get("api_key", "")
    except Exception:
        pass
    if standalone_settings:
        return standalone_settings.llm_base_url, standalone_settings.llm_api_key
    return (
        os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
    )

def get_effective_system_prompt() -> str:
    """Trusted base only. Saved preferences are composed separately in the user role."""
    return SYSTEM_PROMPT


def get_user_prompt_override() -> str:
    try:
        if os.path.exists(PROMPT_OVERRIDE_FILE):
            with open(PROMPT_OVERRIDE_FILE, encoding='utf-8') as handle:
                return handle.read()
    except OSError:
        raise trading_prompt.ContractError('Cannot inspect administrator preference layer')
    return ''


def normalize_smart_money(item=None) -> Dict[str, Any]:
    """Keep observed fields only; missing/malformed data is not neutral flow."""
    result = {"valid": False, **dict.fromkeys((
        "weighted_long_pct", "net_flow_usdt", "avg_long_entry",
        "avg_short_entry", "top_win_rate"), "UNKNOWN")}
    if not isinstance(item, dict) or item.get("valid", True) is not True:
        return result
    fields = (
        ("longShortRatio", "weightedLongRatio", "weighted_long_pct"),
        ("notional", "netNotionalUsdt", "net_flow_usdt"),
        ("notional", "smartMoneyLongAvgEntry", "avg_long_entry"),
        ("notional", "smartMoneyShortAvgEntry", "avg_short_entry"),
        ("winRate", "avgLongWinRate", "top_win_rate"),
    )
    for section, source, target in fields:
        data = item.get(section)
        if not isinstance(data, dict) or data.get("valid", True) is not True:
            continue
        try:
            value = trading_prompt.numeric(data.get(source))
        except trading_prompt.ContractError:
            continue
        if target in ("weighted_long_pct", "top_win_rate"):
            if not 0 <= value <= 1:
                continue
            value = round(value * 100, 1)
            result[target] = value if target == "weighted_long_pct" else f"多胜率{value}%"
        elif target == "net_flow_usdt":
            result[target] = f"{round(value / 1e4, 1)}万 U" if abs(value) >= 1e4 else f"{round(value, 0)} U"
        elif value > 0:
            result[target] = str(data[source])
    result["valid"] = any(value != "UNKNOWN" for key, value in result.items() if key != "valid")
    return result


def fetch_single_instrument_package(item: Dict[str, Any]) -> Dict[str, Any]:
    inst_id = item["instId"]
    name = item["name"]
    ccy = item.get("ccy", "")
    headers = {"User-Agent": "Mozilla/5.0"}

    pkg = {
        "data_as_of": market.signal_as_of(),
        "instId": inst_id,
        "name": name,
        "type": item["type"],
        "precision": item["precision"],
        "price": 0.0,
        "chg24h": 0.0,
        "bidPx": 0.0,
        "askPx": 0.0,
        "fundingRate": 0.0,
        "oiUsd": "N/A",
        "lsRatio": "N/A",
        "takerNetUsd": "N/A",
        "atr": 0.0,
        "rsi": 50.0,
        "vwap_bias": 0.0,
        "macd_hist": 0.0,
        "macd_accel": 0.0,
        "vol_ratio": 1.0,
        "obv_flow": "NEUTRAL",
        "adx_1h": 0.0,
        "smart_money": normalize_smart_money(),
        "entry_candles": {},
        "recent_15m": [],
        "recent_1h": [],
        "recent_4h": [],
        "calculus": {"valid": False, "regime": "DATA_UNRELIABLE", "quality": 0.0},
        "data_quality": "invalid"
    }

    # 1. Ticker
    try:
        d = market.get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}")
        if d.get("code") == "0" and d.get("data"):
            t = d["data"][0]
            pkg["price"] = float(t.get("last", 0))
            pkg["bidPx"] = float(t.get("bidPx", pkg["price"]) or pkg["price"])
            pkg["askPx"] = float(t.get("askPx", pkg["price"]) or pkg["price"])
            op = float(t.get("open24h", 0) or 0)
            pkg["chg24h"] = round(((pkg["price"] - op) / op * 100) if op > 0 else 0, 2)
    except Exception:
        pass

    # 1M/5M closed candles feed the independent scalp catalog.
    for tf, limit in (('1M', 80), ('5M', 48)):
        try:
            d = market.signal_json(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={tf.lower()}&limit={limit}")
            if d.get('code') == '0' and d.get('data'):
                from scripts.entry_candidates import seal_candles
                pkg['entry_candles'][tf] = seal_candles(d['data'], tf, int(pkg['data_as_of']*1000))
                pkg[f'recent_{tf.lower()}'] = [[float(c[1]),float(c[2]),float(c[3]),float(c[4]),round(float(c[5]),1)] for c in d['data'][:24]]
        except Exception:
            pass

    # 2. 15M Candles (recent 24, about 6 hours) & Technical Indicators Calculation
    try:
        d = market.signal_json(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=15m&limit=24")
        if d.get("code") == "0" and d.get("data"):
            raw_candles = d["data"]
            from scripts.entry_candidates import seal_candles
            pkg['entry_candles']['15M'] = seal_candles(raw_candles, '15M', int(pkg['data_as_of']*1000))
            pkg["recent_15m"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_candles[:12]]

            # Calculate 15M indicators
            if len(raw_candles) >= 15:
                closes = [float(c[4]) for c in reversed(raw_candles)]
                highs = [float(c[2]) for c in reversed(raw_candles)]
                lows = [float(c[3]) for c in reversed(raw_candles)]
                vols = [float(c[5]) for c in reversed(raw_candles)]

                # ATR 15M
                tr_list = []
                for i in range(1, len(closes)):
                    tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                    tr_list.append(tr)
                if len(tr_list) >= 14:
                    pkg["atr_15m"] = round(sum(tr_list[-14:]) / 14, 4)
                    pkg["atr"] = pkg["atr_15m"]

                # RSI 15M
                diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
                gains = [d if d > 0 else 0 for d in diffs]
                losses = [-d if d < 0 else 0 for d in diffs]
                if len(gains) >= 14:
                    avg_g = sum(gains[-14:]) / 14
                    avg_l = sum(losses[-14:]) / 14
                    rs = (avg_g / avg_l) if avg_l > 0 else 100.0
                    pkg["rsi"] = round(100.0 - (100.0 / (1.0 + rs)), 1)
                    pkg["rsi_15m"] = pkg["rsi"]

                # VWAP Bias
                pv_sum = sum(closes[i] * vols[i] for i in range(len(closes)))
                v_sum = sum(vols)
                if v_sum > 0:
                    vwap = pv_sum / v_sum
                    pkg["vwap_bias"] = round((pkg["price"] - vwap) / vwap * 100, 2)

                # Volume Ratio (Last vs MA5)
                if len(vols) >= 6:
                    avg_v5 = sum(vols[-6:-1]) / 5
                    if avg_v5 > 0:
                        pkg["vol_ratio"] = round(vols[-1] / avg_v5, 2)

                # OBV Flow
                obv = 0
                for i in range(1, len(closes)):
                    if closes[i] > closes[i-1]:
                        obv += vols[i]
                    elif closes[i] < closes[i-1]:
                        obv -= vols[i]
                pkg["obv_flow"] = "BULL_FLOW" if obv > 0 else ("BEAR_FLOW" if obv < 0 else "NEUTRAL")
    except Exception:
        pass

    # 3. 1H Candles (recent 24, about 24 hours) & 1H ATR / 1H RSI
    try:
        d = market.signal_json(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=1H&limit=24")
        if d.get("code") == "0" and d.get("data"):
            raw_1h = d["data"]
            from scripts.entry_candidates import seal_candles
            pkg['entry_candles']['1H'] = seal_candles(raw_1h, '1H', int(pkg['data_as_of']*1000))
            pkg["recent_1h"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_1h[:12]]
            if len(raw_1h) >= 15:
                closes_1h = [float(c[4]) for c in reversed(raw_1h)]
                highs_1h = [float(c[2]) for c in reversed(raw_1h)]
                lows_1h = [float(c[3]) for c in reversed(raw_1h)]

                tr_list_1h = []
                for i in range(1, len(closes_1h)):
                    tr = max(highs_1h[i] - lows_1h[i], abs(highs_1h[i] - closes_1h[i-1]), abs(lows_1h[i] - closes_1h[i-1]))
                    tr_list_1h.append(tr)
                if len(tr_list_1h) >= 14:
                    pkg["atr_1h"] = round(sum(tr_list_1h[-14:]) / 14, 4)
                    pkg["atr"] = pkg["atr_1h"]  # Elevate primary ATR to 1H

                diffs_1h = [closes_1h[i] - closes_1h[i-1] for i in range(1, len(closes_1h))]
                gains_1h = [d if d > 0 else 0 for d in diffs_1h]
                losses_1h = [-d if d < 0 else 0 for d in diffs_1h]
                if len(gains_1h) >= 14:
                    avg_g_1h = sum(gains_1h[-14:]) / 14
                    avg_l_1h = sum(losses_1h[-14:]) / 14
                    rs_1h = (avg_g_1h / avg_l_1h) if avg_l_1h > 0 else 100.0
                    pkg["rsi_1h"] = round(100.0 - (100.0 / (1.0 + rs_1h)), 1)

                # 1H Swing Structure
                if len(closes_1h) >= 10:
                    ma7_1h = sum(closes_1h[-7:]) / 7
                    ma20_1h = sum(closes_1h[-20:]) / min(len(closes_1h), 20)
                    if closes_1h[-1] > ma7_1h > ma20_1h:
                        pkg["structure_1h"] = "1H_SWING_BULL"
                    elif closes_1h[-1] < ma7_1h < ma20_1h:
                        pkg["structure_1h"] = "1H_SWING_BEAR"
                    else:
                        pkg["structure_1h"] = "1H_SWING_CHOP"
    except Exception:
        pass

    # 4. 4H Candles (recent 16, about 64 hours) & 4H Macro Structure
    try:
        d = market.signal_json(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=4H&limit=16")
        if d.get("code") == "0" and d.get("data"):
            raw_4h = d["data"]
            pkg["recent_4h"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_4h[:8]]
            if len(raw_4h) >= 8:
                closes_4h = [float(c[4]) for c in reversed(raw_4h)]
                ma5_4h = sum(closes_4h[-5:]) / 5
                ma12_4h = sum(closes_4h[-12:]) / min(len(closes_4h), 12)
                if closes_4h[-1] > ma5_4h > ma12_4h:
                    pkg["macro_4h"] = "4H_MACRO_BULL (大级别多头通道)"
                elif closes_4h[-1] < ma5_4h < ma12_4h:
                    pkg["macro_4h"] = "4H_MACRO_BEAR (大级别空头承压)"
                else:
                    pkg["macro_4h"] = "4H_MACRO_RANGE (大级别区间震荡)"
    except Exception:
        pass

    # 5. Funding Rate & OI
    if item["type"] == "crypto":
        try:
            d = market.get_json(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}")
            if d.get("code") == "0" and d.get("data"):
                pkg["fundingRate"] = round(float(d["data"][0].get("fundingRate", 0)) * 100, 4)
        except Exception:
            pass

        try:
            d = market.get_json(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}")
            if d.get("code") == "0" and d.get("data"):
                usd = float(d["data"][0].get("oiUsd", 0) or 0)
                pkg["oiUsd"] = f"{round(usd / 1e8, 2)}亿 U" if usd > 1e8 else f"{round(usd / 1e4, 1)}万 U"
        except Exception:
            pass

        if ccy:
            try:
                d = market.get_json(f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=5m")
                if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                    pkg["lsRatio"] = float(d["data"][0][1])
            except Exception:
                pass

            try:
                d = market.get_json(f"https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy={ccy}&instType=CONTRACTS&period=5m")
                if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                    b_vol = float(d["data"][0][1])
                    s_vol = float(d["data"][0][2])
                    net_diff = b_vol - s_vol
                    pkg["takerNetUsd"] = f"{round(net_diff / 1e4, 1)}万 U"
            except Exception:
                pass

        # 6. OKX ADX Trend Strength Indicator (1H)
        try:
            indicators = market.signal_indicators(inst_id)
            pkg["adx_1h"] = float(indicators["ADX"][0]["values"].get("adx", 0.0) or 0.0)
        except Exception:
            pass

    required_market_data = (
        pkg["price"] > 0
        and pkg["bidPx"] > 0
        and pkg["askPx"] >= pkg["bidPx"]
        and len(pkg["recent_15m"]) >= 12
        and len(pkg["recent_1h"]) >= 8
        and len(pkg["recent_4h"]) >= 6
    )
    try:
        from calculus_engine import calculate_multi_timeframe
        pkg["calculus"] = calculate_multi_timeframe({
            "15M": pkg["recent_15m"],
            "1H": pkg["recent_1h"],
            "4H": pkg["recent_4h"],
        })
    except Exception as exc:
        pkg["calculus"] = {"valid": False, "regime": "DATA_UNRELIABLE", "quality": 0.0, "error": str(exc)}
    pkg["data_quality"] = "valid" if required_market_data else "invalid"
    return pkg

SYSTEM_PROMPT = trading_prompt.BASE_SYSTEM

def construct_full_market_prompt(packages: List[Dict[str, Any]], pos_summary: str = "当前总持仓 0/6", active_positions_detail: List[Dict[str, Any]] = None, pending_orders_detail: List[Dict[str, Any]] = None, current_time_str: str = "", usdt_available: float = 0.0, *, profile=None, return_bundle=False, pending_verified=True):
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj_str = current_time_str or datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S (北京时间)")
    market_lines = []
    for p in packages:
        capability = p.get("environment_support")
        if capability and not capability["can_open"]:
            market_lines.append(f"【{p['instId']} 环境限制】{capability['label']}：只允许处理已有仓位，不得开仓或加仓。")
        k15 = p.get("recent_15m", [])
        k1h = p.get("recent_1h", [])
        k4h = p.get("recent_4h", [])
        quality = p.get("data_quality", "invalid")

        sm = p.get("smart_money") or {}
        if not isinstance(sm, dict) or sm.get("valid") is not True:
            sm = normalize_smart_money()
        adx_val = p.get("adx_1h", "--")
        calc = p.get("calculus", {})
        calc_tfs = calc.get("timeframes", {}) if isinstance(calc, dict) else {}
        d_int = calc.get("definite_integrals", {}) if isinstance(calc, dict) else {}
        p_th = calc.get("probability_theory", {}) if isinstance(calc, dict) else {}
        calc_1h = calc_tfs.get("1H", {}) if isinstance(calc_tfs.get("1H", {}), dict) else {}
        int_1h = calc_1h.get("definite_integrals", {}) if isinstance(calc_1h, dict) else {}
        prob_1h = calc_1h.get("probability_theory", {}) if isinstance(calc_1h, dict) else {}

        calc_line = (
            f"动力学态={calc.get('regime', 'DATA_UNRELIABLE')} | 速度={calc.get('velocity', '--')} "
            f"| 加速度={calc.get('acceleration', '--')} | 累计冲量={calc.get('impulse', '--')} "
            f"| 冲击变化={calc.get('max_abs_jerk', '--')} | 质量={calc.get('quality', 0)}"
        )
        integral_line = (
            f"多周期净做功积分={d_int.get('energy_integral', 'UNKNOWN')} | 路径偏离面积积分={d_int.get('deviation_area_integral', 'UNKNOWN')} "
            f"| 量价作用积分={d_int.get('volume_action_integral', 'UNKNOWN')} | 能量态={d_int.get('regime', 'UNKNOWN')}"
        )
        prob_line = (
            f"多头方向评分(未校准)={p_th.get('continuation_prob_pct', 'UNKNOWN')}% | 空头方向评分(未校准)={p_th.get('breakdown_prob_pct', 'UNKNOWN')}% "
            f"| 偏度S={p_th.get('skewness', 'UNKNOWN')} | 超额峰度K={p_th.get('kurtosis', 'UNKNOWN')} "
            f"| 95%VaR={p_th.get('var_95_pct', 'UNKNOWN')}% | 95%CVaR={p_th.get('cvar_95_pct', 'UNKNOWN')}% "
            f"| 尾部风险态={p_th.get('regime', 'UNKNOWN')}"
        )
        core_math_line = (
            f"1H:v={calc_1h.get('velocity', 'UNKNOWN')},a={calc_1h.get('acceleration', 'UNKNOWN')},"
            f"j={calc_1h.get('jerk', 'UNKNOWN')},I={calc_1h.get('impulse', 'UNKNOWN')},态={calc_1h.get('regime', 'UNKNOWN')} "
            f"| E={int_1h.get('energy_integral', 'UNKNOWN')},A={int_1h.get('deviation_area_integral', 'UNKNOWN')} "
            f"| P续={prob_1h.get('continuation_prob_pct', 'UNKNOWN')}%,P破={prob_1h.get('breakdown_prob_pct', 'UNKNOWN')}%,"
            f"VaR={prob_1h.get('var_95_pct', 'UNKNOWN')}%,CVaR={prob_1h.get('cvar_95_pct', 'UNKNOWN')}%"
        )
        calc_tf_line = "；".join(
            f"{tf}:v={v.get('velocity', '--')},a={v.get('acceleration', '--')},I={v.get('impulse', '--')},态={v.get('regime', '--')}"
            for tf, v in calc_tfs.items() if isinstance(v, dict)
        )
        info = f"""---------------------------------------------------------
【{p['name']} ({p['instId']})】| 数据质量: {quality} | 现价: {p['price']} | 24H涨跌: {p['chg24h']}% | 盘口买/卖: {p['bidPx']}/{p['askPx']}
- 🏛️ 三重滤网宏观结构: 4H宏观大势={p.get('macro_4h', '4H_MACRO_RANGE')} | 1H波段结构={p.get('structure_1h', '1H_SWING_CHOP')}
- 👑 官方聪明钱聚合 (SmartMoney): 加权做多占比={sm.get('weighted_long_pct', 'UNKNOWN')}% | 当前多空净名义敞口={sm.get('net_flow_usdt', '--')}（多头名义金额减空头名义金额，不是24H资金净流入） | 多头均价={sm.get('avg_long_entry', '--')} | 空头均价={sm.get('avg_short_entry', '--')} | {sm.get('top_win_rate', '')}
- 📐 1H核心波段指标: 1H ATR(14)={p.get('atr_1h', p.get('atr', '--'))} (波动参考，不替代失效条件) | 1H RSI(14)={p.get('rsi_1h', '--')} | 1H ADX趋势强度={adx_val} (趋势强度观测，不单独决定交易)
- ⚡ 15M微观执行参考: 15M ATR={p.get('atr_15m', '--')} | 15M RSI={p.get('rsi_15m', '--')} | VWAP乖离={p.get('vwap_bias', '--')}% | 15M量比={p.get('vol_ratio', '--')}x | OBV资金流={p.get('obv_flow', '--')}
- 📐 1H三大数理基石硬证据: {core_math_line}
- ∂ 多周期微积分动力学摘要: {calc_line}
- ∫ 定积分能量学: {integral_line}
- ⚅ 概率论与统计风险: {prob_line}
- ∂ 分周期速度/加速度/冲量: {calc_tf_line or 'UNKNOWN'}
- 衍生品博弈: 资金费率: {p['fundingRate']}% | OI未平仓: {p['oiUsd']} | 多空比: {p['lsRatio']} | 5M主动吃单净差: {p['takerNetUsd']}
- 15M K线(倒序12根 [O,H,L,C,V]): {k15}
- 1H K线(倒序12根 [O,H,L,C,V]): {k1h}
- 4H K线(倒序8根 [O,H,L,C,V]): {k4h}"""
        market_lines.append(info)

    all_market_str = "\n".join(market_lines)

    pos_lines = []
    if active_positions_detail and len(active_positions_detail) > 0:
        for p in active_positions_detail:
            pos_lines.append(
                f"- 标的: {p.get('name') or p.get('instId')} | 方向: {p.get('side')} {p.get('lever', 'UNKNOWN')}x | 开仓均价: {p.get('avgPx')} | 当前标记价: {p.get('markPx', p.get('lastPx'))} | 持仓量: {p.get('pos')}张 | 未结浮盈: {p.get('upl')} U (ROI: {round(safe_float(p.get('uplRatio')) * 100, 2)}%) | 动态止损线: {p.get('trailingStopPx', p.get('trailingSl', '--'))}"
            )
    else:
        pos_lines.append("本轮输入未列出活动合约持仓；不据此推断账户其他资产或现金比例")

    active_pos_text = "\n".join(pos_lines)

    # Format Pending Limit Orders
    pending_lines = []
    if pending_orders_detail and len(pending_orders_detail) > 0:
        for o in pending_orders_detail:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            side_raw = str(o.get("side", "")).lower()
            pos_side = str(o.get("posSide", "net")).lower()
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()

            if reduce_only:
                side_str = "市价平多" if (side_raw == "sell" and ord_type == "market") else ("限价平多" if side_raw == "sell" else ("市价平空" if ord_type == "market" else "限价平空"))
            else:
                side_str = "限价买多" if (side_raw == "buy" and ord_type != "market") else ("市价买多" if side_raw == "buy" else ("限价卖空" if ord_type != "market" else "市价卖空"))

            raw_px = str(o.get("px") or "").strip()
            px_val = raw_px if raw_px and raw_px != "0" else ("市价" if ord_type == "market" else "--")
            sz_val = str(o.get("sz", "--"))
            ord_id = str(o.get("ordId", ""))

            attach_list = o.get("attachAlgoOrds", [])
            tp_sl_info = ""
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_p = att.get("tpTriggerPx", "--")
                sl_p = att.get("slTriggerPx", "--")
                tp_sl_info = f" | 附带云端止盈: {tp_p} / 止损: {sl_p}"

            pending_lines.append(
                f"- [挂单ID: {ord_id}] {inst_id} | {side_str} {sz_val}张 @ {px_val} | 挂单时间: {c_time_str}{tp_sl_info}"
            )
    elif pending_verified:
        pending_lines.append("本轮已核验挂单列表为空")
    else:
        pending_lines.append("在途挂单快照不可用：UNKNOWN，不得推断挂单池为空")

    pending_orders_text = "\n".join(pending_lines)

    from scripts.memory_registry import view as memory_view
    memory_state=memory_view(DATA_DIR,scope=market._selected().identity,
        legacy_paths={'md':AI_MEMORY_MD_FILE,'json':AI_MEMORY_FILE})
    memory_lessons=memory_state['prompt_text']

    # Freeze once before inference: rendering, validation and evidence share this exact input.
    try:
        from scripts.news_connection import load_strategy_snapshot, render_strategy_snapshot
        news_snapshot = load_strategy_snapshot(NEWS_SENTIMENT_FILE,
            [p['instId'].split('-')[0] for p in packages])
        news_text = render_strategy_snapshot(news_snapshot)
    except Exception:
        news_snapshot = {'schema': 1, 'connection_status': 'unavailable', 'macro_sentiment': 'UNKNOWN',
                         'sentiment_fresh': False, 'items': [], 'coins_sentiment': {}, 'section_freshness': {}}
        news_text = '市场情报当前不可用；不得据此推断市场平稳或不存在事件风险'
    for package in packages:
        package['news_snapshot'] = news_snapshot

    avail_balance_str = f"{usdt_available:.2f} USDT" if usdt_available > 0 else "0 USDT；不得假设存在可用资金"

    selected = profile if profile is not None else active_profile()
    capital_context = capital_pool.status(market._selected())
    previous_wait_reviews = wait_audit.prepare(market._selected().identity, packages, active_positions_detail)
    runtime_vars = {
        'previous_wait_reviews': previous_wait_reviews,
        'capital_pool': capital_context,
        'execution_profile': __import__('scripts.execution_profiles',fromlist=['runtime']).runtime(selected),
        "pending_orders_status": "verified" if pending_verified else "unknown",
        "decision_timestamp": f"【推演基准时间】: {now_bj_str}",
        "account_balance": f"【交易所账户可用资金】: {avail_balance_str}" + (f"；策略资金池上限={capital_context.get('configured_cap', 'UNKNOWN')} USDT，风险净值={capital_context.get('risk_equity', '待初始化')}；不是额外现金，不能借用池外权益放大建议。" if capital_context.get("enabled") else ""),
        "account_positions": f"【账户持仓概况】: {pos_summary}\n【当前活动在途持仓明细】:\n{active_pos_text}",
        "pending_orders": f"【当前在途挂单列表】:\n{pending_orders_text}",
        "news_intelligence": news_text,
        "trading_memory": memory_lessons.strip(),
        "market_matrix": all_market_str,
    }
    from dataclasses import asdict
    from scripts.risk_policy import load_policy
    bundle = trading_prompt.compose(selected, runtime_vars, packages, override=get_user_prompt_override(),
        positions=active_positions_detail, pending=pending_orders_detail, risk_contract=asdict(load_policy()))
    bundle.manifest['memory_publication']={k:memory_state.get(k) for k in ('scope','active_version','prompt_hash')}
    return bundle if return_bundle else bundle.user

def validate_and_filter_decision(p: Dict[str, Any], d_item: Dict[str, Any], active_inst_ids: set, active_position_sides: Dict[str, str]) -> tuple[str, str, float]:
    """
    Fail-closed execution layer gatekeeper powered by pluggable interceptors.
    1. Base pre-checks: data completeness & opposing position collision
    2. Dynamic interceptor pipeline: runs all enabled Python interceptor plugins
    """
    context = {
        "active_inst_ids": active_inst_ids,
        "active_position_sides": active_position_sides,
    }
    try:
        from okxquant_backend.interceptor_manager import run_interceptor_pipeline
        return run_interceptor_pipeline(p, d_item, context)
    except Exception as exc:
        # Fail-closed fallback in case interceptor manager cannot be reached
        inst_id = p.get("instId", "")
        raw_action = str((d_item or {}).get("action", "WAIT")).upper()
        if raw_action not in {"BUY_LONG", "SELL_SHORT", "WAIT"}:
            raw_action = "WAIT"
        entry = safe_float((d_item or {}).get("entry_price"))
        take_profit = safe_float((d_item or {}).get("take_profit_price"))
        stop_loss = safe_float((d_item or {}).get("stop_loss_price"))
        rr = 0.0
        if raw_action == "BUY_LONG" and entry > stop_loss > 0 and take_profit > entry:
            rr = (take_profit - entry) / (entry - stop_loss)
        elif raw_action == "SELL_SHORT" and stop_loss > entry > take_profit > 0:
            rr = (entry - take_profit) / (stop_loss - entry)
        return "WAIT", f"拦截插件管线调用异常: {exc}，安全降级为 WAIT", rr

@single_brain_cycle
def execute_batch_ai_brain_cycle(pos_summary: str = "当前总持仓 0/6", active_positions_detail: List[Dict[str, Any]] = None, usdt_available: float = 0.0) -> Optional[Dict[str, Any]]:
    """Fetch all six crypto symbols, call the LLM once, then persist an auditable result."""
    global LAST_INFERENCE_ERROR
    base_url, api_key = get_cpa_client_config()
    if not api_key:
        LAST_INFERENCE_ERROR = "未配置模型调用凭据"
        atomic_write_json(os.path.join(DATA_DIR, 'trading_output_validation.json'), {'status':'unavailable','reason':LAST_INFERENCE_ERROR})
        print("[AI Brain Batch] Error: CPA API Key not found")
        return None

    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    time_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")

    cycle_environment = market._selected()
    cycle_as_of = time.time()
    market.begin_signal_frame(cycle_as_of)
    eligible, availability = support.trading_universe(TARGET_INSTRUMENTS, active_positions_detail, market._selected().mode)
    if not eligible:
        LAST_INFERENCE_ERROR = "当前环境无已核验可交易标的，仅保留行情观察"
        atomic_write_json(os.path.join(DATA_DIR, 'trading_output_validation.json'), {'status':'unavailable','reason':LAST_INFERENCE_ERROR})
        return None
    print(f"[AI Brain Batch] 并行获取 {len(eligible)} 个交易/持仓管理标的；其余标的仅作行情观察")
    with ThreadPoolExecutor(max_workers=8) as executor:
        packages = list(executor.map(market.bind_signal_frame(fetch_single_instrument_package), eligible))
    for package in packages:
        package["environment_support"] = availability["items"][package["instId"]]

    # Fetch OKX Smart Money Signals
    for p in packages:
        p["smart_money"] = normalize_smart_money()
    try:
        sm_data = market.smart_money_overview([p["name"] for p in packages])
        sm_dict = {item.get("ccy"): item for item in sm_data
                   if isinstance(item, dict) and item.get("ccy")}
        for p in packages:
            p["smart_money"] = normalize_smart_money(sm_dict.get(p["name"]))
    except Exception as e:
        print(f"[AI Brain Batch] SmartMoney fetch warning: {e}")

    active_positions_detail = active_positions_detail or []
    active_inst_ids = {
        str(p.get("instId", "")) for p in active_positions_detail if p.get("instId")
    }
    active_position_sides = {
        str(p.get("instId", "")): str(p.get("side", p.get("posSide", ""))).lower()
        for p in active_positions_detail if p.get("instId")
    }
    package_by_id = {p["instId"]: p for p in packages}

    # Automatically Update & Persist Comprehensive Factor Library Snapshot
    try:
        sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
        import factor_library
        factor_library.update_factor_library()
    except Exception as e:
        print(f"[AI Brain Batch] Factor Library update warning: {e}")

    # Fetch live pending limit orders from exchange
    pending_orders_list = []
    pending_verified = False
    try:
        ord_cmd = okx_private_command("okx swap orders --json 2>/dev/null")
        ord_res = subprocess.run(ord_cmd, shell=True, capture_output=True, text=True, timeout=8)
        if ord_res.returncode == 0 and ord_res.stdout:
            pending_orders_list = json.loads(ord_res.stdout)
            if not isinstance(pending_orders_list, list) or any(not isinstance(o,dict) or not o.get("instId") or not o.get("ordId") for o in pending_orders_list):
                pending_orders_list = []
            else:
                pending_verified = True
    except Exception as e:
        print(f"[AI Brain Batch] Pending orders fetch warning: {e}")

    try:
        calculus_snapshot = {
            "timestamp": time_str,
            "engine": "causal-calculus-v1",
            "instruments": [
                {"name": p.get("name"), "instId": p.get("instId"), "calculus": p.get("calculus", {})}
                for p in packages
            ],
        }
        tmp_calc = CALCULUS_SNAPSHOT_FILE + ".tmp"
        with open(tmp_calc, "w", encoding="utf-8") as f:
            json.dump(calculus_snapshot, f, ensure_ascii=False, indent=2)
        os.replace(tmp_calc, CALCULUS_SNAPSHOT_FILE)
    except Exception as exc:
        print(f"[AI Brain] Calculus snapshot warning: {exc}")

    try:
        profile = active_profile()  # One immutable profile selection for the entire inference.
        from scripts.execution_profiles import runtime as execution_runtime
        cycle_execution=execution_runtime(profile)
        prompt_bundle = construct_full_market_prompt(packages, pos_summary, active_positions_detail,
            pending_orders_detail=pending_orders_list, current_time_str=time_str, usdt_available=usdt_available,
            profile=profile, return_bundle=True, pending_verified=pending_verified)
        prompt = prompt_bundle.user
        effective_system_prompt = prompt_bundle.system
    except Exception as exc:
        LAST_INFERENCE_ERROR = '提示词组合失败：' + type(exc).__name__
        atomic_write_json(os.path.join(DATA_DIR, 'trading_output_validation.json'),
            {'status':'composition_rejected','contract_version':trading_prompt.VERSION,'reason':LAST_INFERENCE_ERROR})
        return None

    # Same frozen data, shadow only. A research failure never blocks risk management.
    try:
        from scripts.entry_opportunities import record_cycle
        record_cycle(market._selected().identity, packages, prompt_bundle.risk_contract, cycle_execution['signature'])
    except Exception as exc:
        print('[Entry Shadow] observation unavailable: ' + type(exc).__name__)

    # Save Realtime Prompt Snapshot for Web Transparent Inspection
    try:
        tmp_prompt = AI_LAST_PROMPT_FILE + ".tmp"
        with open(tmp_prompt, "w", encoding="utf-8") as f:
            f.write(f"【SYSTEM PROMPT】:\n{effective_system_prompt.strip()}\n\n{'='*70}\n【USER PROMPT ({time_str})】：\n{prompt.strip()}")
        os.replace(tmp_prompt, AI_LAST_PROMPT_FILE)
        atomic_write_json(os.path.join(DATA_DIR, "trading_prompt_manifest.json"), prompt_bundle.manifest)
    except Exception:
        pass

    if not prompt_bundle.allow_open:
        LAST_INFERENCE_ERROR = "提示词偏好冲突或账户输入未核验，本轮不调用模型；独立持仓保护继续"
        atomic_write_json(os.path.join(DATA_DIR, 'trading_output_validation.json'),
            {'status':'blocked','contract_version':trading_prompt.VERSION,'reason':LAST_INFERENCE_ERROR,'warnings':prompt_bundle.manifest['warnings']})
        return None

    model_name = os.environ.get("LLM_MODEL") or "gemini-3.8-flash-high"
    effort = os.environ.get("LLM_REASONING_EFFORT") or "high"
    api_format = "openai_chat"
    try:
        from okxquant_backend.llm_manager import get_active_llm_runtime, execute_llm_request
        active_llm = get_active_llm_runtime()
        model_name = os.environ.get("LLM_MODEL") or active_llm.get("model") or model_name
        effort = os.environ.get("LLM_REASONING_EFFORT") or active_llm.get("reasoning_effort") or effort
        api_format = active_llm.get("api_format", "openai_chat")
        base_url = active_llm.get("base_url") or base_url
        api_key = active_llm.get("api_key") or api_key
    except Exception:
        execute_llm_request = None

    json_report = {}
    telemetry = ModelCallTelemetry(
        "trading_brain", model_name, str(effort), effective_system_prompt, prompt
    )
    try:
        t0 = time.time()
        raw_res = None
        content = None  # Council returns structured output, not single-model text.
        brain_output = None

        # Transparent check: is Multi-Agent Council enabled?
        council_enabled = False
        try:
            from okxquant_backend.council_manager import load_council_config, execute_council_debate
            c_cfg = load_council_config()
            council_enabled = bool(c_cfg.get("enabled"))
        except Exception:
            council_enabled = False

        if council_enabled:
            print(f"[AI Brain Council] 🏛️ 多模型委员会已开启，正在启动各专家参谋现场辩论与首席仲裁...")
            try:
                brain_output, council_transcript = execute_council_debate(
                    market_prompt=prompt,
                    original_system_prompt=effective_system_prompt,
                    timeout=float(c_cfg.get("timeout_seconds", 60.0)),
                )
                print(f"[AI Brain Council] ✅ 委员会辩论与终审完成，耗时: {council_transcript.get('total_duration_ms', 0)}ms")
            except Exception as e:
                print(f"[AI Brain Council] ⚠️ 委员会决策超时或异常: {e}，自动降级为单模型极速决策！")
                brain_output = None

        if brain_output is None:
            print(f"[AI Brain Batch] 🚀 正在发起单次全市场大模型宏观决策推演 ({model_name} / {api_format})...")
            from scripts.model_json import decode_with_regeneration
            def initial_json():
                nonlocal content, raw_res
                if execute_llm_request:
                    content, _, usage_dict, _ = execute_llm_request(
                        messages=[
                            {"role": "system", "content": effective_system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        model=model_name,
                        base_url=base_url,
                        api_key=api_key,
                        api_format=api_format,
                        reasoning_effort=effort,
                        temperature=0.2,
                        response_format={"type": "json_object"},
                        timeout=160.0, attempt_timeout=75.0, max_attempts=2,
                        require_complete=True,
                    )
                    raw_res = {"usage": usage_dict} if isinstance(usage_dict, dict) else {}
                else:
                    payload = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": effective_system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"}
                    }
                    if effort not in ("none", "auto"):
                        payload["reasoning_effort"] = effort
                    req = urllib.request.Request(
                        f"{base_url}/chat/completions",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
                    )
                    with urllib.request.urlopen(req, timeout=50) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        from scripts.model_json import verify_completion, text_content
                        verify_completion(res, "openai_chat")
                        content = text_content(res["choices"][0]["message"]["content"])
                        raw_res = res
                return content
            def regenerate_json(*, timeout, max_attempts):
                nonlocal content
                messages = [{"role":"system", "content":effective_system_prompt},
                    {"role":"user", "content":prompt + "\n上一次响应不是完整合法JSON。请基于同一冻结输入重新输出完整JSON对象；不要输出代码围栏、解释、注释、尾逗号或省略字段。理由保持简洁，不改变风险契约。"}]
                meter = ModelCallTelemetry('trading_json_regeneration', model_name, str(effort), messages[0]['content'], messages[1]['content'])
                try:
                    content, _, usage, _ = execute_llm_request(messages=messages, model=model_name,
                        base_url=base_url, api_key=api_key, api_format=api_format, reasoning_effort=effort,
                        temperature=0.2, response_format={'type':'json_object'}, timeout=timeout,
                        max_attempts=max_attempts, require_complete=True)
                    meter.finish('success', {'usage':usage}, output_chars=len(content))
                    return content
                except Exception as exc:
                    meter.finish('failed', error=exc)
                    raise
            brain_output = decode_with_regeneration(initial_json,
                regenerate_json if execute_llm_request else None, report=json_report)
        original_brain_output = brain_output
        # Shared boundary for single-model and council output, before any model-directed write.
        brain_output = trading_prompt.validate_response(brain_output, packages,
            positions=active_positions_detail, pending=pending_orders_list,
            allow_open=prompt_bundle.allow_open and not brain_output.get('prompt_conflicts'),
            previous_wait_reviews=prompt_bundle.previous_wait_reviews, risk_contract=prompt_bundle.risk_contract)
        from scripts import wait_repair
        def request_wait_correction(*, messages, timeout, max_attempts):
            meter = ModelCallTelemetry('wait_audit_repair', model_name, str(effort), messages[0]['content'], messages[1]['content'])
            try:
                repaired_text, _, usage, _ = execute_llm_request(
                    messages=messages, model=model_name, base_url=base_url, api_key=api_key,
                    api_format=api_format, reasoning_effort=effort, temperature=0.2,
                    response_format={'type':'json_object'}, timeout=timeout, max_attempts=max_attempts, require_complete=True)
            except Exception as exc:
                meter.finish('failed', error=exc)
                raise
            meter.finish('success', {'usage':usage}, output_chars=len(repaired_text))
            return repaired_text
        brain_output, repair_report = wait_repair.attempt(original_brain_output, brain_output, packages,
            request=request_wait_correction if execute_llm_request and not json_report.get("attempted") else None, positions=active_positions_detail,
            previous_wait_reviews=prompt_bundle.previous_wait_reviews, risk_contract=prompt_bundle.risk_contract)
        if repair_report['targets']:
            strategy_evidence.best_effort(market._selected().identity, 'wait_audit_repair',
                {'frame_time':time_str, 'model':model_name, 'report':repair_report})
            print(f"[WAIT Audit] correction={repair_report['status']}; repaired={len(repair_report['corrected'])}/{len(repair_report['targets'])}; no trading actions authorized")
        brain_output['validation']['json_response'] = json_report
        if json_report.get('attempted'):
            strategy_evidence.best_effort(market._selected().identity, 'model_json_response', json_report)
        brain_output['validation']['wait_repair'] = wait_repair.public_report(repair_report)
        output_chars = len(content) if content is not None else len(trading_prompt.canonical(original_brain_output))
        atomic_write_json(os.path.join(DATA_DIR, 'trading_output_validation.json'), brain_output['validation'])
        decisions_dict = brain_output.get("decisions", {})
        pos_mgmt_list = brain_output.get("position_management", [])
        macro_summary = str(brain_output.get("macro_assessment", "宏观中性震荡")).strip()[:4000]
        if not isinstance(decisions_dict, dict):
            decisions_dict = {}
        if not isinstance(pos_mgmt_list, list):
            pos_mgmt_list = []

        validated_pos_mgmt = []
        seen_positions = set()
        for item in pos_mgmt_list:
            if not isinstance(item, dict):
                continue
            inst_id = str(item.get("instId", ""))
            if inst_id not in active_inst_ids or inst_id in seen_positions:
                continue
            seen_positions.add(inst_id)
            action = str(item.get("action", "HOLD")).upper()
            if action not in {"HOLD", "CLOSE_MARKET", "UPDATE_SL"}:
                action = "HOLD"
            confidence = max(0.0, min(100.0, safe_float(item.get("confidence"))))
            suggested_sl = safe_float(item.get("suggested_sl_price"))
            if action != "UPDATE_SL":
                suggested_sl = 0.0
            validated_pos_mgmt.append({
                "instId": inst_id,
                "action": action,
                "suggested_sl_price": suggested_sl,
                "confidence": confidence,
                "reason": str(item.get("reason", "模型未提供持仓理由"))[:120]
            })

        for inst_id in sorted(active_inst_ids - seen_positions):
            validated_pos_mgmt.append({
                "instId": inst_id,
                "action": "HOLD",
                "suggested_sl_price": 0.0,
                "confidence": 0.0,
                "reason": "模型遗漏该持仓，安全降级为 HOLD"
            })
        pos_mgmt_list = validated_pos_mgmt

        assert_cycle_current(cycle_environment, cycle_as_of)
        # Execute Pending Orders Cancellation if AI Brain decides CANCEL
        pending_mgmt_list = brain_output.get("pending_orders_management", [])
        strategy_evidence.best_effort(market._selected().identity, 'management_decision', {
            'model': model_name, 'features_as_of': market.signal_as_of(), 'generated_at': time.time(),
            'position_management': pos_mgmt_list, 'pending_management': pending_mgmt_list})
        known_pending = {(str(o.get('instId')), str(o.get('ordId'))) for o in pending_orders_list if isinstance(o, dict)}
        if isinstance(pending_mgmt_list, list):
            for p_order in pending_mgmt_list:
                if not isinstance(p_order, dict):
                    continue
                p_act = str(p_order.get("action", "")).upper()
                p_ord_id = str(p_order.get("ordId", ""))
                p_inst_id = str(p_order.get("instId", ""))
                p_reason = str(p_order.get("reason", "模型指示撤销该挂单"))
                import re
                if (p_act == "CANCEL" and (p_inst_id, p_ord_id) in known_pending
                        and re.fullmatch(r'[A-Z0-9]+-USDT-SWAP', p_inst_id)
                        and re.fullmatch(r'[0-9]{1,32}', p_ord_id)):
                    cxl_cmd = okx_private_command(f"okx swap cancel {p_inst_id} --ordId {p_ord_id} --json")
                    with trade_lock.writer(), algo_reader.command_barrier(cxl_cmd, cycle_environment):
                        assert_cycle_current(cycle_environment, cycle_as_of)
                        cxl_res = subprocess.run(cxl_cmd, shell=True, capture_output=True, text=True, timeout=10)
                    strategy_evidence.best_effort(market._selected().identity, 'cancel_submission',
                        {'instrument': p_inst_id, 'order_id': p_ord_id, 'transport_ok': cxl_res.returncode == 0})
                    print(f"[AI Brain Batch] 撤单已提交，待订单状态对账: {p_inst_id} (ordId={p_ord_id}, CLI状态={cxl_res.returncode})")

        standard_cache = {}
        for p in packages:
            inst_id = p["instId"]
            d_item = decisions_dict.get(inst_id, {})
            raw_proposal = original_brain_output.get('decisions', {}).get(inst_id)
            raw_proposal = raw_proposal if isinstance(raw_proposal, dict) else {}
            try:
                model_score = trading_prompt.numeric(raw_proposal.get('confidence'))
                if not 0 <= model_score <= 100: model_score = None
            except trading_prompt.ContractError:
                model_score = None
            if not isinstance(d_item, dict):
                d_item = {}
            from scripts.entry_candidates import catalog as entry_catalog
            plan_catalog = entry_catalog(p, prompt_bundle.risk_contract)
            selected_plan = next((x for x in plan_catalog.get('plans', []) if x.get('id') == d_item.get('candidate_id')), None)
            if selected_plan:
                d_item = {**d_item, 'horizon': selected_plan.get('horizon', 'swing')}
            # Smooth field alias normalization (support both standard contract and council desk outputs)
            entry = safe_float(d_item.get("entry_price") or d_item.get("limit_price"))
            take_profit = safe_float(d_item.get("take_profit_price") or d_item.get("take_profit"))
            stop_loss = safe_float(d_item.get("stop_loss_price") or d_item.get("stop_loss"))
            confidence = max(0.0, min(100.0, safe_float(d_item.get("confidence"))))
            ai_leverage = int(max(1, min(8, round(safe_float(d_item.get("leverage", 3))))))
            ai_margin = round(safe_float(d_item.get("margin_usdt") or d_item.get("margin_usd", 0.0)), 2)

            # Ensure normalized keys exist for downstream interceptors
            normalized_d_item = dict(d_item)
            normalized_d_item["entry_price"] = entry
            normalized_d_item["take_profit_price"] = take_profit
            normalized_d_item["stop_loss_price"] = stop_loss
            normalized_d_item["margin_usdt"] = ai_margin
            normalized_d_item["leverage"] = ai_leverage

            final_action, rejection_reason, rr = validate_and_filter_decision(
                p, normalized_d_item, active_inst_ids, active_position_sides
            )

            standard_cache[inst_id] = {
                "instId": inst_id,
                "name": p["name"],
                "timestamp": int(time.time()),
                "data_as_of": p.get("data_as_of", market.signal_as_of()),
                "execution_profile_signature": cycle_execution["signature"],
                "strategy_profile_hash": trading_prompt.profile_signature(profile),
                **account_basis(cycle_environment),
                "position_basis": {"side": active_position_sides.get(inst_id), "size": next((abs(safe_float(x.get("pos", x.get("size", 0)))) for x in (active_positions_detail or []) if x.get("instId") == inst_id), 0.0), **{k:next((x.get(k) for x in (active_positions_detail or []) if x.get("instId")==inst_id),None) for k in ("posId","cTime")}},
                "time_str": time_str,
                "macro_assessment": macro_summary,
                "thought_process": {
                    "market_structure": d_item.get("market_structure") or "；".join(e.get("interpretation", "") for e in d_item.get("supporting_evidence", [])) or "未建立候选，等待",
                    "calculus_dynamics": d_item.get("calculus_dynamics", "模型未提供具体微积分证据"),
                    "math_prob_rationale": d_item.get("math_prob_rationale", "模型未提供具体定积分与概率证据"),
                    "volume_and_oi": d_item.get("volume_and_oi", f"OI: {p['oiUsd']}, Taker: {p['takerNetUsd']}"),
                    "risk_reward_evaluation": f"基础净盈亏比门槛={prompt_bundle.risk_contract.get('minimum_net_rr', 'UNKNOWN')}；执行预设={cycle_execution['execution'].get('minimum_net_rr', '沿用基础策略')}；最终以执行器复核为准"
                },
                "smart_money": p.get("smart_money", {}),
                "adx_1h": p.get("adx_1h", "--"),
                "decision": {
                    "action": final_action,
                    "memory_publication": prompt_bundle.manifest.get("memory_publication"),
                    "strategy_profile_hash": trading_prompt.profile_signature(profile),
                    "model_action": str(raw_proposal.get("action", "MISSING")).upper()[:24],
                    "model_confidence": model_score,
                    "candidate_id": d_item.get("candidate_id"),
                    "horizon": d_item.get("horizon", "swing"),
                    "candidate_origin": d_item.get("candidate_origin"),
                    "entry_plans": plan_catalog,
                    "candidate_reviews": d_item.get("candidate_reviews", raw_proposal.get("candidate_reviews", [])),
                    "confidence_role": "uncalibrated_diagnostic",
                    "decision_status": d_item.get('decision_status','incomplete') if not rejection_reason else 'execution_rejected',
                    "wait_audit": d_item.get('wait_audit'),
                    "wait_repair": wait_repair.public_report(repair_report, inst_id),
                    "previous_wait_review": d_item.get('previous_wait_review',{}),
                    "validation_reason": rejection_reason or d_item.get('validation_reason'),
                    "model_reason": d_item.get('model_reason'),
                    "contract_version": trading_prompt.VERSION,
                    "contract_valid": bool(d_item.get('contract_valid')) and not bool(rejection_reason),
                    "supporting_evidence": d_item.get('supporting_evidence', []),
                    "counter_evidence": d_item.get('counter_evidence', []),
                    "counter_evidence_status": d_item.get('counter_evidence_status', 'not_applicable'),
                    "uncertainty": d_item.get('uncertainty', ''),
                    "invalidation": d_item.get('invalidation'),
                    "valid_for_seconds": d_item.get('valid_for_seconds', 0),
                    "valid_until": min(p.get('data_as_of', market.signal_as_of()) + 300,
                                       time.time() + d_item.get('valid_for_seconds', 0)),
                    "confidence": confidence,
                    "leverage": ai_leverage,
                    "margin_usdt": ai_margin,
                    "entry_price": entry,
                    "take_profit_price": take_profit,
                    "stop_loss_price": stop_loss,
                    "risk_reward_ratio": f"{rr:.2f} : 1" if rr > 0 else "--",
                    "summary_reason": rejection_reason or str(d_item.get("summary_reason", "全市场矩阵综合评估中"))[:120]
                },
                "data_quality": p.get("data_quality", "invalid"),
                "raw_ticker": {
                    "last": p["price"],
                    "bidPx": p["bidPx"],
                    "askPx": p["askPx"],
                    "chg24h": p["chg24h"]
                },
                "raw_funding_rate": f"{p['fundingRate']}%" if p.get('fundingRate') else "--",
                "raw_oi": p.get('oiUsd') or "--",
                "raw_taker_vol": p.get('takerNetUsd') or "--",
                "raw_ls_ratio": str(p.get('lsRatio')) if p.get('lsRatio') is not None else "--"
            }

        import hashlib
        # Use the immutable prompt-time snapshot for attribution.
        news_snapshot = dict(prompt_bundle.manifest.get('news_snapshot') or {})
        assert_cycle_current(cycle_environment, cycle_as_of)
        strategy_evidence.record_decisions(cycle_environment.identity, standard_cache, packages, model_name,
            hashlib.sha256(effective_system_prompt.encode()).hexdigest(), time.time(), news_snapshot=news_snapshot)
        try:
            audit_status = wait_audit.commit(market._selected().identity, standard_cache, packages,
                active_positions_detail, frame_id=time_str)
            brain_output['validation']['wait_audit_status'] = audit_status['status']
        except Exception as exc:
            # An audit persistence failure cannot authorize a trade or masquerade as a normal WAIT.
            brain_output['validation']['status'] = 'incomplete'
            brain_output['validation']['wait_audit_status'] = 'error'
            brain_output['validation']['reason'] = 'WAIT审计持久化失败：' + type(exc).__name__
            for row in standard_cache.values():
                if row['decision']['action']=='WAIT':
                    row['decision'].update(contract_valid=False,decision_status='incomplete',validation_reason='WAIT审计无法持久化')
        atomic_write_json(os.path.join(DATA_DIR,'trading_output_validation.json'),brain_output['validation'])
        assert_cycle_current(cycle_environment, cycle_as_of)
        atomic_write_json(AI_DECISION_CACHE_FILE, standard_cache)
        atomic_write_json(AI_POSITION_MANAGEMENT_FILE, {
            "timestamp": int(cycle_as_of),
            "generated_at": time.time(),
            **account_basis(cycle_environment),
            "execution_profile_signature": cycle_execution["signature"],
            "strategy_profile_hash": trading_prompt.profile_signature(profile),
            "position_basis": active_positions_detail,
            "time_str": time_str,
            "instructions": pos_mgmt_list
        })

        # Record durable history for Web Audit
        full_prompt_text = f"【SYSTEM PROMPT】：\n{effective_system_prompt.strip()}\n\n{'='*70}\n【USER PROMPT ({time_str})】：\n{prompt.strip()}"
        history_record = {
            "time": time_str,
            "macro_assessment": macro_summary,
            "prompt_composition": prompt_bundle.manifest,
            "output_validation": brain_output["validation"],
            "news_snapshot": news_snapshot,
            "ai_last_prompt": full_prompt_text,
            "position_management": pos_mgmt_list,
            "council_transcript": brain_output.get("council_transcript") if isinstance(brain_output, dict) else None,
            "top_opportunities": [
                {
                    "inst": p["name"],
                    "decision_status": standard_cache[p["instId"]]["decision"].get("decision_status"),
                    "wait_audit": standard_cache[p["instId"]]["decision"].get("wait_audit"),
                    "previous_wait_review": standard_cache[p["instId"]]["decision"].get("previous_wait_review"),
                    "action": standard_cache[p["instId"]]["decision"]["action"],
                    "confidence": standard_cache[p["instId"]]["decision"]["confidence"],
                    "leverage": standard_cache[p["instId"]]["decision"].get("leverage", 3),
                    "margin_usdt": standard_cache[p["instId"]]["decision"].get("margin_usdt", 0.0),
                    "risk_reward_ratio": standard_cache[p["instId"]]["decision"]["risk_reward_ratio"],
                    "data_quality": standard_cache[p["instId"]]["data_quality"],
                    "reason": standard_cache[p["instId"]]["decision"]["summary_reason"]
                }
                for p in packages
            ]
        }

        history_list = []
        if os.path.exists(AI_DECISION_HISTORY_FILE):
            try:
                with open(AI_DECISION_HISTORY_FILE, "r", encoding="utf-8") as f:
                    history_list = json.load(f)
            except Exception:
                pass

        history_list.insert(0, history_record)
        history_list = history_list[:50] # Keep recent 50 rounds

        atomic_write_json(AI_DECISION_HISTORY_FILE, history_list)

        latency = round(time.time() - t0, 2)
        telemetry.finish("success", raw_res, output_chars=output_chars)
        print(f"[AI Brain Batch] ✅ {len(packages)} 标的全景决策完成 (耗时 {latency}s, 宏观基调: {macro_summary})")
        return standard_cache

    except Exception as e:
        # A history/audit write can fail AFTER cache publication. A failed cycle
        # must not leave partially published instructions available to consumers.
        for path, empty in ((AI_DECISION_CACHE_FILE, {}),
                            (AI_POSITION_MANAGEMENT_FILE, {'timestamp': 0, 'instructions': []})):
            try:
                atomic_write_json(path, empty)
            except OSError:
                print('[AI Brain Batch] Failed to invalidate decision artifacts')
        from okxquant_backend.llm_transport import public_failure
        failure = public_failure(e)
        if failure:
            LAST_INFERENCE_ERROR = failure['message']
        else:
            code = getattr(e, 'status_code', None) or getattr(e, 'code', None)
            LAST_INFERENCE_ERROR = ('模型输出契约不合格：' + str(e)) if isinstance(e, trading_prompt.ContractError) else (f'模型接口 HTTP {code}' if code else type(e).__name__)
        # Persist AFTER building the full reason; UI, logs and audit see the same failure.
        atomic_write_json(os.path.join(DATA_DIR, 'trading_output_validation.json'),
            {'status':'rejected','contract_version':trading_prompt.VERSION,'reason':LAST_INFERENCE_ERROR,
             'model_failure':failure,'json_response':json_report,'updated_at':time.time()})
        if failure:
            strategy_evidence.best_effort(market._selected().identity, 'model_request_failure',
                {**failure,'caller':'trading_brain','frame_time':time_str,'model':model_name})
        if json_report:
            strategy_evidence.best_effort(market._selected().identity, 'model_json_response', json_report)
        telemetry.finish('failed', error=e)
        print(f'[AI Brain Batch] Error in batch inference: {LAST_INFERENCE_ERROR}')
        return None

def account_basis(environment):
    """Non-secret provenance shared by cache and management consumers."""
    return {'account_scope': environment.identity, 'connection_id': getattr(environment, 'connection_id', ''),
            'binding_version': getattr(environment, 'binding_version', 0)}


def assert_cycle_current(environment, as_of):
    """Do not cancel or publish actionable instructions from an obsolete frame."""
    age = time.time() - float(as_of)
    if not 0 <= age <= DECISION_MAX_AGE_SECONDS:
        raise trading_prompt.ContractError('Inference frame expired or invalid; instructions discarded')
    if account_basis(market._selected()) != account_basis(environment):
        raise trading_prompt.ContractError('Account changed during inference; instructions discarded')
    # Real OKXEnvironment always carries source/connection provenance. Lightweight
    # read-only market adapters may expose only mode + identity. Never skip a
    # registry check for a real legacy, managed, or unbound runtime environment.
    if hasattr(environment, 'source') or getattr(environment, 'connection_id', ''):
        from okxquant_backend.account_connections import assert_current
        assert_current(environment)


def get_latest_ai_decision(inst_id: str, max_age_seconds: int = DECISION_MAX_AGE_SECONDS) -> Optional[Dict[str, Any]]:
    """Read a validated decision only while its cache timestamp is fresh."""
    if os.path.exists(AI_DECISION_CACHE_FILE):
        try:
            with open(AI_DECISION_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            item = data.get(inst_id)
            if not isinstance(item, dict):
                return None
            environment = market._selected()
            if any(item.get(key) != value for key, value in account_basis(environment).items()):
                return None
            from okxquant_backend.account_connections import assert_current
            assert_current(environment)
            current_profile = active_profile()
            if item.get('strategy_profile_hash') != trading_prompt.profile_signature(current_profile):
                return None
            timestamp = int(item.get("timestamp", 0) or 0)
            if timestamp <= 0 or not 0 <= time.time() - timestamp <= max_age_seconds:
                return None
            decision = item.get('decision', {})
            if decision.get('action') in {'BUY_LONG','SELL_SHORT'} and (decision.get('contract_version') != trading_prompt.VERSION or not decision.get('contract_valid') or not time.time() < safe_float(decision.get('valid_until'))):
                return None
            return item
        except Exception:
            pass
    return None

if __name__ == "__main__":
    res = execute_batch_ai_brain_cycle("当前无持仓")
    if res:
        print("\n--- 示例标的 AI 决策结果 ---")
        for k in ["BTC-USDT-SWAP", "SOL-USDT-SWAP", "LINK-USDT-SWAP"]:
            if k in res:
                print(f"[{k}]", json.dumps(res[k]["decision"], ensure_ascii=False))
