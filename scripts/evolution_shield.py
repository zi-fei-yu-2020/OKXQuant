#!/usr/bin/env python3
"""
OKXQuant Evolution Shield & Anti-Poisoning Cognitive Guardian (evolution_shield.py)
-------------------------------------------------------------------------------
Ensures AI Self-Evolution DOES NOT become a double-edged sword:
1. Anti-Single-Event Bias / Outlier Rejection:
   Single flash-crash or anomalous spikes cannot dictate long-term strategy.
2. Constitution Red-Lines (Non-negotiable Rules):
   - Prohibits "Never go Long" or "Never go Short" biases.
   - Prohibits widening stop losses to bag-hold losses.
   - Prohibits aggressive revenge betting or Martingale sizing.
3. Structured White-Box Lesson Schema:
   - id, category, rule_text, health_score, enabled, created_at, ttl_days, sample_size
4. Cognitive Decay & Health Score:
   - Lessons lose health score if they contradict recent positive performance.
   - Decayed lessons auto-archive, preventing cognitive poisoning.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = WORKSPACE_DIR / "data"
STRUCTURED_MEMORY_FILE = DATA_DIR / "structured_trading_memory.json"
AI_MEMORY_MD_FILE = DATA_DIR / "AI_TRADING_MEMORY.md"

# 官方不可逾越的基准心法库 (Baseline Golden Lessons)
BASELINE_LESSONS = [
    {
        "id": "lesson_trend_pullback",
        "category": "TREND_FOLLOWING",
        "rule_text": "【顺势回踩低吸做多与反弹承压高抛做空】绝对禁止在 4H/1H 多头通道逆势摸顶开空，空单只在宏观空头反弹受阻时限价挂单；箱体震荡边界双向高抛低吸。",
        "health_score": 98.0,
        "enabled": True,
        "created_at": "2026-09-01 00:00:00",
        "ttl_days": 14,
        "sample_size": 42,
        "is_baseline": True,
        "shield_status": "PASSED",
    },
    {
        "id": "lesson_wide_atr_stop",
        "category": "RISK_CONTROL",
        "rule_text": "【宽止损抗噪杜绝随意割肉】止损必须设在结构外 1.8x~2.2x 1H ATR 以外，给足波动呼吸空间，从物理上隔绝 15M/5M 杂波插针洗损。",
        "health_score": 95.0,
        "enabled": True,
        "created_at": "2026-09-01 00:00:00",
        "ttl_days": 14,
        "sample_size": 38,
        "is_baseline": True,
        "shield_status": "PASSED",
    },
    {
        "id": "lesson_breakeven_lock",
        "category": "WIN_RATE_LOCK",
        "rule_text": "【浮盈0.8R坚决保本锁死胜率】持仓浮盈达到 0.8R~1.0R 坚决执行保本移损 (UPDATE_SL)，将潜在亏损彻底消除为零风险平仓，锁死胜率下限，杜绝盈利变割肉。",
        "health_score": 99.0,
        "enabled": True,
        "created_at": "2026-09-01 00:00:00",
        "ttl_days": 14,
        "sample_size": 50,
        "is_baseline": True,
        "shield_status": "PASSED",
    },
    {
        "id": "lesson_anti_correlation_rush",
        "category": "PORTFOLIO_DIVERSIFICATION",
        "rule_text": "【严禁跨标的同向共振堆叠单边敞口】无论阻力高空还是顺势做多，严禁在多相关标的（BTC/ETH/SOL/DOGE）上同向无节制开仓，必须对总同向在手仓位施加硬性约束，防范系统性 Beta 踩踏。",
        "health_score": 92.0,
        "enabled": True,
        "created_at": "2026-09-04 12:00:00",
        "ttl_days": 7,
        "sample_size": 15,
        "is_baseline": True,
        "shield_status": "PASSED",
    },
]

# 宪法红线规则（任何大模型总结出的心法如果触碰以下词汇或逻辑，直接物理阻断）：
POISON_PATTERNS = [
    (r"(永远不|绝对不|严禁|彻底禁止).*(做多|开多|买入)", "EXTREME_DIRECTIONAL_BIAS (极端做多偏见阻断)"),
    (r"(永远不|绝对不|严禁|彻底禁止).*(做空|开空|卖出)", "EXTREME_DIRECTIONAL_BIAS (极端做空偏见阻断)"),
    (r"(扩大|放宽|取消|不设).*(止损|SL)", "RISK_EXPANSION_VIOLATION (违规抗单放大止损)"),
    (r"(加倍|翻倍|加仓|重仓|梭哈).*(亏损|抗单|摊平)", "MARTINGALE_POISONING (马丁格尔赌徒加仓倾向)"),
    (r"(忽视|不看|废弃).*(4H|宏观|ATR|风控|拦截器)", "GOVERNANCE_OVERRIDE (企图推翻硬风控拦截器)"),
]


def audit_proposed_lesson(rule_text: str, sample_size: int = 1) -> Tuple[bool, str]:
    """
    Applies the Constitution Linter to verify whether a proposed lesson is safe.
    Returns (is_passed, reason).
    """
    if not rule_text or len(rule_text.strip()) < 10:
        return False, "心法文本过短，缺乏明确可复用的交易情境依据"

    # 1. Check Constitution Red-Lines
    for pattern, reason in POISON_PATTERNS:
        if re.search(pattern, rule_text):
            return False, f"触发宪法红线拦截: {reason}"

    # 2. Outlier / Single-Event Rejection Gate
    if sample_size < 2:
        return False, "样本量不足 (单笔偶发事件或极端插针噪点，拒绝写入长期心法)"

    return True, "PASSED"


def load_structured_memory() -> List[Dict[str, Any]]:
    """Compatibility read: published rules only. Never initialize built-in lessons on a read."""
    from scripts.memory_registry import view
    state=view(DATA_DIR)
    return [{**r,'rule_text':r['text'],'category':r.get('source','reviewed')} for r in state['rules']]


def save_structured_memory(lessons):
    raise ValueError('Direct memory writes disabled; stage a candidate and publish an audited version')


def toggle_lesson(lesson_id):
    raise ValueError('Direct memory toggles disabled; use a versioned DEACTIVATE/ADD proposal')


def rollback_to_baseline():
    raise ValueError('Automatic baseline restoration disabled; choose an actual published historical version')


def add_safe_lesson(rule_text, category='TACTICAL', sample_size=3):
    from scripts.memory_registry import propose
    result=propose({'action':'ADD','text':rule_text,'rationale':'Legacy caller: claimed sample count is not verified evidence'},data_dir=DATA_DIR)
    return False,'Staged for review only; not published',result
