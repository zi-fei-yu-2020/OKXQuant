"""
OKXQuant 物理拦截插件规范
====================
id: 02_confidence_gatekeeper
name: 证据评分格式核验（非开仓分数线）
version: 2.0.0
author: OKXQuant Official
description: confidence 仅为未校准的研究评分，不是胜率。核验数值有效性，不再按 75/80 分拒绝候选；数据、证据契约、方向、成本后盈亏比和最终风险预算仍独立校验。
tags: 证据评分, 非概率, 官方预设
"""
import math


def check_risk(package: dict, decision: dict, context: dict) -> tuple[bool, str]:
    if str(decision.get('action', 'WAIT')).upper() == 'WAIT':
        return True, ''
    raw = decision.get('confidence')
    try:
        score = float(raw)
    except (ValueError, TypeError):
        return False, '证据评分缺失或格式无效；这不是开仓分数不足。'
    if isinstance(raw, bool) or not math.isfinite(score) or not 0 <= score <= 100:
        return False, '证据评分必须为 0~100 的有限数值；不能将其解释为成功概率。'
    # A low, honest score cannot veto otherwise valid evidence; a high score cannot bypass other gates.
    return True, ''
