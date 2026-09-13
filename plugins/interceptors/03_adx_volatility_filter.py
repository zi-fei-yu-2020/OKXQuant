"""
OKXQuant 物理拦截插件规范
====================
id: 03_adx_volatility_filter
name: 1H ADX 趋势强度门禁
version: 1.1.0
author: OKXQuant Official
description: 低ADX的普通信号保持拦截；已收盘触发且价格与成本可重建的程序计划可继续最终风控，ADX不是胜率或单独的开仓授权。
tags: 震荡过滤, ADX, 官方预设
"""

def check_risk(package: dict, decision: dict, context: dict) -> tuple[bool, str]:
    action = str(decision.get("action", "WAIT")).upper()
    if action == "WAIT":
        return True, ""

    try:
        adx = float(package.get("adx_1h", 0) or 0)
    except (ValueError, TypeError):
        adx = 0.0

    if 0 < adx < 18.0:
        if decision.get('contract_valid') is True and decision.get('candidate_id'):
            try:
                from scripts.entry_candidates import catalog
                from scripts.risk_policy import load_policy
                plans=catalog(package,vars(load_policy()))['plans']
                if any(p['id']==decision['candidate_id'] and p['action']==action for p in plans):
                    return True, ''  # Still subject to geometry, freshness, portfolio and OCO gates.
            except Exception:
                pass
        return False, f"1H ADX 趋势强度仅 {adx:.1f}，处于无序震荡杂波市，安全降级为 WAIT。"

    return True, ""
