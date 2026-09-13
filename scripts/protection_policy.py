"""Fail-closed OCO coverage and tick-safe stop calculations (no I/O)."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import math


def positive(value):
    """Missing, boolean, negative and non-finite values are never evidence."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def trigger_geometry(side, tp, sl, mark_px=None, entry_px=None):
    tp, sl = positive(tp), positive(sl)
    if side not in ('long', 'short') or tp is None or sl is None:
        return False
    # A profitable trailing stop may cross entry. Current mark takes precedence.
    reference = positive(mark_px) or positive(entry_px)
    if reference is None:
        return sl < tp if side == 'long' else tp < sl
    return sl < reference < tp if side == 'long' else tp < reference < sl


@dataclass(frozen=True)
class Coverage:
    size: float
    orders: tuple
    unknown: bool


def oco_coverage(orders, side, mark_px=None, entry_px=None):
    """Return proven coverage separately from ambiguity that forbids repair.

    Conditional rows are not treated as OCO: two nonempty trigger fields alone
    do not establish that the exchange will actually execute both alternatives.
    Triggered/effective orders are ambiguous, NOT a confirmed protection gap.
    """
    if side not in ('long', 'short') or not isinstance(orders, (list, tuple)):
        return Coverage(0.0, (), True)
    close_side = 'sell' if side == 'long' else 'buy'
    opposite = 'short' if side == 'long' else 'long'
    candidates, seen, conflicts = [], {}, set()
    unknown = False
    for row in orders:
        if not isinstance(row, dict):
            unknown = True
            continue
        if row.get('posSide') == opposite:
            continue
        if row.get('posSide') == 'net' and row.get('side') == ('buy' if side == 'long' else 'sell'):
            continue
        if row.get('state') in ('canceled', 'order_failed'):
            continue
        identity = row.get('algoId')
        if not isinstance(identity, str) or not identity.strip():
            unknown = True
            continue
        if identity in seen:
            if row != seen[identity]:
                conflicts.add(identity)
                unknown = True
            continue
        seen[identity] = row
        size = positive(row.get('sz'))
        valid = (row.get('ordType') == 'oco' and row.get('state') == 'live'
                 and row.get('posSide') in (side, 'net')
                 and row.get('side') == close_side
                 and str(row.get('reduceOnly')).lower() in ('true', '1')
                 and size is not None
                 and trigger_geometry(side, row.get('tpTriggerPx'), row.get('slTriggerPx'), mark_px, entry_px)
                 and positive(row.get('actualSz')) is None)
        if not valid:
            unknown = True
            continue
        candidates.append(row)
    proven = tuple(row for row in candidates if row['algoId'] not in conflicts)
    try:
        total = math.fsum(positive(row['sz']) for row in proven)
    except OverflowError:
        return Coverage(0.0, (), True)
    return Coverage(total, proven, unknown)


def rounded_stop(side, proposed, old_stop, current, tick):
    """Round away from market, then require strict tightening and no crossing."""
    if side not in ('long', 'short') or any(positive(v) is None for v in (proposed, old_stop, current, tick)):
        return None
    try:
        proposed, old_stop, current, tick = map(lambda v: Decimal(str(v)), (proposed, old_stop, current, tick))
        rounding = ROUND_FLOOR if side == 'long' else ROUND_CEILING
        stop = (proposed / tick).to_integral_value(rounding=rounding) * tick
        valid = old_stop < stop < current if side == 'long' else current < stop < old_stop
        return format(stop, 'f') if valid and stop > 0 else None
    except (InvalidOperation, OverflowError):
        return None
