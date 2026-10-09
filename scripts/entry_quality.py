"""Frozen entry quality, not position quotas or a profitability claim.
No network, state, orders, live-stop changes or hindsight-dependent thresholds.
"""
import math
import os
from scripts.execution_costs import from_policy

VERSION = 'entry-quality-v1'
MOMENTUM = {'scalp_breakout_1m', 'scalp_pullback_1m', 'scalp_reversal_1m'}


def finite(value):
    if isinstance(value, bool): return None
    try: value = float(value)
    except (ValueError, TypeError, OverflowError): return None
    return value if math.isfinite(value) else None


def evaluate(package, plan, policy, *, entry_price=None):
    """An extension beyond a frozen opposing structure is conditional, not RR evidence."""
    reasons = []
    result = {'version': VERSION, 'semantics': 'entry_quality_not_validated_profitability',
              'reasons': reasons, 'order_authorized': False}
    try:
        action = plan['action']
        if action not in {'BUY_LONG', 'SELL_SHORT'}: raise ValueError('invalid_direction')
        sign = 1 if action == 'BUY_LONG' else -1
        entry = finite(plan['entry_price'] if entry_price is None else entry_price)
        stop, target = finite(plan['stop_loss_price']), finite(plan['take_profit_price'])
        minimum = finite(policy['minimum_net_rr'])
        if None in (entry, stop, target, minimum) or min(entry, stop, target, minimum) <= 0:
            raise ValueError('invalid_geometry')
        risk, reward = sign * (entry - stop), sign * (target - entry)
        if min(risk, reward) <= 0: raise ValueError('invalid_geometry')
        spread = 0.0
        if package.get('bidPx') is not None and package.get('askPx') is not None:
            bid, ask = finite(package['bidPx']), finite(package['askPx'])
            if None in (bid, ask) or not 0 < bid <= ask: raise ValueError('invalid_quote')
            spread = ask - bid
        near = (plan.get('target_layers') or {}).get('near')
        if near is not None:
            if not isinstance(near, dict) or near.get('extrapolated') is not False:
                raise ValueError('near_structure_not_observed')
            price = finite(near.get('price'))
            if price is None or sign * (price - entry) <= 0:
                raise ValueError('near_structure_already_crossed')
            # Do not move a target/stop or invent market space to make this pass.
            barrier = min(sign * (price - entry), reward)
            cost = from_policy(entry, max(stop, price), policy, spread)['taker_taker_total']
            rr = (barrier - cost) / (risk + cost)
            result.update(first_observed_target=price, first_target_net_rr=rr,
                          minimum_net_rr=minimum, round_trip_cost=cost,
                          cost_basis='taker_taker_with_slippage')
            if rr < minimum: reasons.append('near_structure_net_rr_insufficient')
        setup = str(plan.get('setup') or '')
        if setup in MOMENTUM:
            macro = str(package.get('macro_4h') or '').split(' (', 1)[0].upper()
            opposed = ((sign == 1 and macro == '4H_MACRO_BEAR') or
                       (sign == -1 and macro == '4H_MACRO_BULL'))
            adx = finite(package.get('adx_1h'))
            if near is None and opposed and adx is not None and adx >= 25:
                reasons.append('countertrend_projection_without_observed_target')
            rsi = finite(package.get('rsi_15m'))
            if rsi is not None and ((sign == 1 and rsi >= 80) or (sign == -1 and rsi <= 20)):
                reasons.append('minute_momentum_tail_overextended')
        result['status'] = 'shadow_only' if reasons else 'admitted'
    except (ValueError, TypeError, KeyError, OverflowError):
        reasons.append('entry_quality_inputs_unavailable')
        result['status'] = 'shadow_only'
    return result


def enforcement_mode():
    mode=os.environ.get("OKXQUANT_ENTRY_QUALITY_MODE","tail_only").strip().lower()
    if mode not in {"observe","tail_only","enforce"}:raise ValueError("Invalid entry quality mode")
    return mode


def enforced_reasons(result):
    mode=enforcement_mode()
    reasons=result.get("reasons") or []
    if mode=="enforce":return list(reasons)
    if mode=="tail_only":return [r for r in reasons if r=="minute_momentum_tail_overextended"]
    return []


def attach(package, plan, policy):
    """Keep rejected drafts observable; selection can never authorize their orders."""
    result = evaluate(package, plan, policy)
    active=enforced_reasons(result)
    result.update(enforcement_mode=enforcement_mode(),veto=bool(active),enforced_reasons=active)
    plan['entry_quality'] = result
    if active:
        plan['shadow_only'] = True
        if not plan.get('shadow_reason'):
            plan['shadow_reason'] = active[0]
    return result
