"""One cost-aware, preset-aware profit exit policy; never widens a live stop.

Thresholds are engineering rules, not a return forecast. Hard stops and cloud
coverage verification remain independent. All dynamic exits use the same ATR.
"""
import math
from scripts import exit_policy


def finite(value, default=0.):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def activation_distance(entry, atr, thresholds, *, maker_fee=.0002, taker_fee=.0005, slippage=.001):
    costs = entry * (maker_fee + taker_fee + slippage)
    # No min(initial_risk * .8, ...) shortcut: it bypassed both preset gates.
    return max(costs * 1.5, atr * thresholds['tier1_breakeven_atr'])


def floor_plan(side, entry, current, peak, initial_stop, atr, *, maker_fee=.0002,
               taker_fee=.0005, slippage=.001, thresholds=None):
    ex = thresholds if thresholds is not None else exit_policy.thresholds('standard')
    entry, current, peak, initial_stop, atr = [finite(v) for v in (entry, current, peak, initial_stop, atr)]
    if side not in ('long', 'short') or min(entry, current, peak, atr) <= 0:
        return {'active': False, 'reason': 'invalid_price_or_atr', 'kinetic_exit': False}
    sign = 1 if side == 'long' else -1
    gain = sign * (peak - entry)
    risk = sign * (entry - initial_stop) if initial_stop > 0 else 0
    costs = entry * (maker_fee + taker_fee + slippage)
    activation = activation_distance(entry, atr, ex, maker_fee=maker_fee, taker_fee=taker_fee, slippage=slippage)
    if gain < activation:
        return {'active': False, 'reason': 'profit_below_preset_activation',
                'activation': activation, 'kinetic_exit': False}
    # The cost floor, retained-profit fraction and tier floors all share the
    # activation above. R changes retained fraction, never activation timing.
    fraction = .55 if risk > 0 and gain >= risk else .45
    tier = 2 if gain >= ex['tier2_lock_atr'] * atr else 1
    distance = max(costs, gain * fraction, ex[f'tier{tier}_floor_atr'] * atr)
    desired = entry + sign * distance
    buffer = max(entry * .001, min(atr * .25, entry * .003))
    crossed = sign * (current - desired) <= 0
    if not crossed and sign * (current - desired) < buffer:
        desired = current - sign * buffer
    if sign * (desired - entry) < costs:
        return {'active': False, 'reason': 'insufficient_cost_buffer',
                'activation': activation, 'kinetic_exit': False}
    pullback = sign * (peak - current)
    kinetic = (gain >= max(activation, ex['kinetic_peak_atr'] * atr)
               and pullback >= ex['kinetic_pullback_atr'] * atr)
    return {'active': True, 'stop': desired, 'crossed': crossed, 'activation': activation,
            'cost_buffer': costs, 'market_buffer': buffer, 'peak_gain': gain,
            'initial_risk': risk if risk > 0 else None, 'tier': tier,
            'retained_gain': sign * (desired - entry), 'kinetic_exit': kinetic,
            'pullback': pullback, 'pullback_threshold': ex['kinetic_pullback_atr'] * atr}


def allow_ai_tightening(side, entry, current, new_stop, atr, *, maker_fee=.0002,
                        taker_fee=.0005, slippage=.001, thresholds=None):
    ex = thresholds if thresholds is not None else exit_policy.thresholds('standard')
    entry, current, new_stop, atr = [finite(v) for v in (entry, current, new_stop, atr)]
    if side not in ('long', 'short') or min(entry, current, new_stop, atr) <= 0:
        return False
    sign = 1 if side == 'long' else -1
    costs = entry * (maker_fee + taker_fee + slippage)
    buffer = max(entry * .001, min(atr * .25, entry * .003))
    activation = activation_distance(entry, atr, ex, maker_fee=maker_fee, taker_fee=taker_fee, slippage=slippage)
    return (sign * (current - entry) >= activation
            and sign * (new_stop - entry) >= costs
            and sign * (current - new_stop) >= buffer)
