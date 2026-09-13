"""Bounded read-only adoption of a newly discovered position's cloud protection.

No placement, amendment, cancellation or close is performed here. Only completed
but insufficient/ambiguous snapshots get a second read. A reader error already
has its own retry budget and must not cause an outer retry storm.
"""
import time
from scripts import algo_reader, strategy_evidence
from scripts.protection_policy import oco_coverage, positive

FIELDS = ('algoId','instId','ordType','state','posSide','side','reduceOnly','sz',
          'tpTriggerPx','slTriggerPx','actualSz')


def verify(env, inst_id, side, size, position, read_positions, *, timeout=6., read_orders=None):
    reader = read_orders or algo_reader.read_algo_orders
    deadline = time.monotonic() + min(6., max(.1, timeout))
    last = {'status': 'unverified', 'orders': [], 'detail': 'initial protection deadline exceeded', 'attempts': 0}
    for attempt in range(1, 3):
        remaining = deadline - time.monotonic()
        if remaining <= .05:
            break
        try:
            orders = algo_reader.orders_for_instrument(reader(env, priority='risk', force=True, timeout=remaining), inst_id)
            remaining = deadline - time.monotonic()
            if remaining <= .05:
                break
            ok, positions, _ = read_positions(timeout=min(3., remaining))
            if not ok:
                raise ValueError('current_position_snapshot_unavailable')
            current = [p for p in positions if p.get('instId') == inst_id and p.get('posSide') == side and abs(float(p.get('pos') or 0)) > 0]
            if not current:
                last = {'status': 'flat', 'orders': [], 'detail': 'position already flat; no close request needed', 'attempts': attempt}
            elif len(current) != 1 or positive(current[0].get('pos')) != positive(size) or any(
                    position.get(k) and str(position[k]) != str(current[0].get(k) or '') for k in ('posId','cTime')):
                last = {'status': 'changed', 'orders': [], 'detail': 'position identity or size changed; do not close a stale observation', 'attempts': attempt}
            else:
                snapshot = oco_coverage(orders, side, current[0].get('markPx'), current[0].get('avgPx'))
                verified = not snapshot.unknown and snapshot.size >= size
                last = {'status': 'verified' if verified else 'unverified', 'orders': list(snapshot.orders) if verified else [],
                        'detail': 'full live OCO verified' if verified else 'full live OCO not proven', 'attempts': attempt,
                        'coverage': snapshot.size, 'ambiguous': snapshot.unknown}
            strategy_evidence.best_effort(env.identity, 'initial_protection_check', {
                'instId': inst_id, 'posSide': side, 'size': size, 'position_id': position.get('posId'),
                **{k:v for k,v in last.items() if k != 'orders'},
                'observations': [{k:row.get(k) for k in FIELDS} for row in orders],
            })
            if last['status'] != 'unverified':
                return last
        except Exception as exc:
            detail = str(exc) if isinstance(exc, algo_reader.AlgoReadError) else type(exc).__name__
            last = {'status': 'unverified', 'orders': [], 'detail': f'initial protection read unavailable: {detail}', 'attempts': attempt}
            strategy_evidence.best_effort(env.identity, 'initial_protection_check', {
                'instId': inst_id, 'posSide': side, 'size': size, **last,
            })
            break
        if attempt == 1 and deadline - time.monotonic() > .35:
            time.sleep(.3)
    return last
