"""Bounded read-only adoption of a newly discovered position's cloud protection.

No placement, amendment, cancellation or close is performed here. Only completed
but insufficient/ambiguous snapshots get a second read. A reader error already
has its own retry budget and must not cause an outer retry storm.
"""
import time
import json
from scripts import algo_reader, strategy_evidence
from scripts.protection_policy import oco_coverage, positive

FIELDS = ('algoId','instId','ordType','state','posSide','side','reduceOnly','sz',
          'tpTriggerPx','slTriggerPx','actualSz')


def owned_entry(env, inst_id, side, position):
    """Only a recent, scoped, journaled entry may use the fill-settlement grace."""
    created=positive(position.get('cTime'))
    if not getattr(env,'configured',False) or not created or not 0<=time.time()-created/1000<=90:
        return None
    try:
        with strategy_evidence.connection() as db:
            rows=db.execute('SELECT id,at,payload FROM intents WHERE scope=? AND inst_id=? AND at>=? AND at<=?',
                (env.identity,inst_id,created/1000-10,created/1000+1)).fetchall()
        matches=[]
        for client,at,raw in rows:
            plan=json.loads(raw)
            if plan.get('side')==side and positive(plan.get('size')) and positive(plan.get('stop')):
                matches.append({'client_id':client,**plan})
        return matches[0] if len(matches)==1 else None
    except Exception:
        return None


def verify(env, inst_id, side, size, position, read_positions, *, timeout=6., read_orders=None):
    reader = read_orders or algo_reader.read_algo_orders
    owned=owned_entry(env,inst_id,side,position)
    budget=min(12.,max(.1,timeout*2)) if owned else min(6.,max(.1,timeout))
    deadline = time.monotonic() + budget
    last = {'status': 'unverified', 'orders': [], 'detail': 'initial protection deadline exceeded', 'attempts': 0}
    for attempt in range(1, 11 if owned else 3):
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
            elif len(current) != 1 or (positive(current[0].get('pos')) != positive(size) and not (owned and positive(size) <= (positive(current[0].get('pos')) or 0) <= positive(owned['size']))) or any(
                    position.get(k) and str(position[k]) != str(current[0].get(k) or '') for k in ('posId','cTime')):
                last = {'status': 'changed', 'orders': [], 'detail': 'position identity or size changed; do not close a stale observation', 'attempts': attempt}
            else:
                actual_size=positive(current[0].get('pos'))
                if owned:
                    mark=positive(current[0].get('markPx'))
                    if mark is None or (mark<=owned['stop'] if side=='long' else mark>=owned['stop']):
                        return {'status':'unverified','orders':[],'detail':'entry stop breached or price unknown; no settlement grace','attempts':attempt}
                snapshot = oco_coverage(orders, side, current[0].get('markPx'), current[0].get('avgPx'))
                verified = not snapshot.unknown and snapshot.size >= actual_size
                last = {'status': 'verified' if verified else 'unverified', 'orders': list(snapshot.orders) if verified else [],
                        'detail': 'full live OCO verified' if verified else 'full live OCO not proven', 'attempts': attempt,
                        'coverage': snapshot.size, 'ambiguous': snapshot.unknown, 'current_position':current[0],
                        'actual_size':actual_size,'entry_settlement':bool(owned)}
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
        if deadline - time.monotonic() > .55:
            time.sleep(.5 if owned else .3)
    return last
