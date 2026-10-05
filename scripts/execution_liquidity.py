"""Fail-closed order-book liquidity admission for final entry execution.

All quantities are observed exchange contracts. Notional conversion assumes linear
USDT swaps and uses the exchange ctVal supplied by instrument metadata.
"""
from __future__ import annotations

import math
import time

VERSION = 'execution-liquidity-v1'
BANDS_BPS = (5, 10, 25)


class LiquidityRejected(ValueError):
    pass


def _number(value, *, positive=False):
    if isinstance(value, bool):
        raise LiquidityRejected('invalid_orderbook_number')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise LiquidityRejected('invalid_orderbook_number') from None
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise LiquidityRejected('invalid_orderbook_number')
    return result


def _levels(rows, *, reverse):
    if not isinstance(rows, list) or not rows:
        raise LiquidityRejected('orderbook_side_unavailable')
    parsed = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise LiquidityRejected('invalid_orderbook_level')
        price = _number(row[0], positive=True)
        size = _number(row[1], positive=True)
        parsed.append((price, size))
    return sorted(parsed, key=lambda item: item[0], reverse=reverse)


def snapshot(payload, *, ct_val, now_ms=None):
    """Normalize one OKX books response into auditable depth/freshness metrics."""
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise LiquidityRejected('orderbook_snapshot_unavailable')
    raw = rows[0]
    bids = _levels(raw.get('bids'), reverse=True)
    asks = _levels(raw.get('asks'), reverse=False)
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid >= best_ask:
        raise LiquidityRejected('crossed_or_locked_orderbook')
    mid = (best_bid + best_ask) / 2
    contract_base_units = _number(ct_val, positive=True)
    exchange_ts = _number(raw.get('ts'), positive=True)
    received_at = time.time() * 1000 if now_ms is None else _number(now_ms, positive=True)
    age_ms = received_at - exchange_ts
    if age_ms < -1000:
        raise LiquidityRejected('orderbook_timestamp_in_future')
    spread = best_ask - best_bid
    depth = {}
    for band in BANDS_BPS:
        bid_floor = mid * (1 - band / 10000)
        ask_ceiling = mid * (1 + band / 10000)
        bid_notional = sum(price * size * contract_base_units for price, size in bids if price >= bid_floor)
        ask_notional = sum(price * size * contract_base_units for price, size in asks if price <= ask_ceiling)
        depth[str(band)] = {'bid_usdt': bid_notional, 'ask_usdt': ask_notional}
    ten = depth['10']
    total_ten = ten['bid_usdt'] + ten['ask_usdt']
    imbalance = (ten['bid_usdt'] - ten['ask_usdt']) / total_ten if total_ten else None

    def slope(levels, direction):
        total = sum(size * contract_base_units for _, size in levels)
        if total <= 0:
            return None
        weighted_bps = sum(abs(price - mid) / mid * 10000 * size * contract_base_units for price, size in levels) / total
        return direction * weighted_bps / total

    return {
        'version': VERSION,
        'source': 'okx_public_books',
        'exchange_ts': int(exchange_ts),
        'received_at_ms': int(received_at),
        'age_ms': max(0.0, age_ms),
        'best_bid': best_bid,
        'best_ask': best_ask,
        'mid': mid,
        'spread': spread,
        'spread_bps': spread / mid * 10000,
        'depth': depth,
        'imbalance_10bps': imbalance,
        'bid_slope': slope(bids, -1),
        'ask_slope': slope(asks, 1),
        'contract_base_units': contract_base_units,
        '_bids': bids,
        '_asks': asks,
    }


def fetch(inst_id, *, ct_val, simulated=False, getter=None, now_ms=None):
    if getter is None:
        from scripts import public_market
        getter = public_market.get_json
    payload = getter(
        'https://www.okx.com/api/v5/market/books?instId=' + str(inst_id) + '&sz=50',
        simulated=simulated,
    )
    return snapshot(payload, ct_val=ct_val, now_ms=now_ms)


def _walk(levels, requested_contracts):
    remaining = _number(requested_contracts, positive=True)
    total_quantity = 0.0
    total_value = 0.0
    worst = None
    for price, available in levels:
        take = min(remaining, available)
        total_quantity += take
        total_value += take * price
        remaining -= take
        if take:
            worst = price
        if remaining <= 1e-12:
            break
    if remaining > max(1e-12, requested_contracts * 1e-9):
        raise LiquidityRejected('orderbook_cannot_fill_requested_size')
    return total_value / total_quantity, worst


def public_snapshot(value):
    return {key: item for key, item in value.items() if not key.startswith('_')}


def admit(value, *, side, order_size, entry, take_profit, policy, cost_model):
    """Apply final spread, depth, impact and edge-after-cost constraints."""
    if side not in {'long', 'short'}:
        raise LiquidityRejected('invalid_liquidity_side')
    p = policy if isinstance(policy, dict) else vars(policy)
    age_limit = _number(p['orderbook_max_age_ms'], positive=True)
    spread_limit = _number(p['max_entry_spread_bps'], positive=True)
    depth_multiple_limit = _number(p['minimum_depth_multiple'], positive=True)
    edge_multiple_limit = _number(p['minimum_cost_edge_multiple'], positive=True)
    if _number(value.get('age_ms')) > age_limit:
        raise LiquidityRejected('orderbook_snapshot_stale')
    if _number(value.get('spread_bps')) > spread_limit:
        raise LiquidityRejected('entry_spread_above_policy')
    size = _number(order_size, positive=True)
    entry = _number(entry, positive=True)
    take_profit = _number(take_profit, positive=True)
    ct_val = _number(value.get('contract_base_units'), positive=True)
    order_notional = size * ct_val * entry
    side_key = 'ask_usdt' if side == 'long' else 'bid_usdt'
    available_depth = _number(value['depth']['10'][side_key])
    depth_multiple = available_depth / order_notional if order_notional else 0
    if depth_multiple < depth_multiple_limit:
        raise LiquidityRejected('orderbook_depth_below_policy')
    levels = value['_asks'] if side == 'long' else value['_bids']
    expected_vwap, worst_price = _walk(levels, size)
    impact_bps = abs(expected_vwap - value['mid']) / value['mid'] * 10000
    conservative_cost = _number(cost_model.get('taker_taker_total'), positive=True)
    gross_edge = abs(take_profit - entry)
    cost_edge_multiple = gross_edge / conservative_cost
    if cost_edge_multiple < edge_multiple_limit:
        raise LiquidityRejected('target_edge_below_conservative_cost_multiple')
    return {
        'status': 'accepted',
        'version': VERSION,
        'side': side,
        'order_size_contracts': size,
        'order_notional_usdt': order_notional,
        'depth_band_bps': 10,
        'available_depth_usdt': available_depth,
        'depth_multiple': depth_multiple,
        'expected_vwap': expected_vwap,
        'worst_fill_price': worst_price,
        'expected_impact_bps': impact_bps,
        'gross_target_distance': gross_edge,
        'conservative_cost_distance': conservative_cost,
        'cost_edge_multiple': cost_edge_multiple,
        'thresholds': {
            'max_age_ms': age_limit,
            'max_spread_bps': spread_limit,
            'minimum_depth_multiple': depth_multiple_limit,
            'minimum_cost_edge_multiple': edge_multiple_limit,
        },
    }
