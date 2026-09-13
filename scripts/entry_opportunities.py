"""Causal opportunity tracking in SHADOW ONLY; never consumed by the order gateway.
Three fixed setups on sealed 15M bars, nearest observed hourly target, unchanged
1.5 ATR floor and cost policy. No model/network calls, no automatic promotion.
"""
import hashlib
import json
import time
from collections import Counter
from scripts import entry_candidates as legacy
from scripts.risk_policy import Policy

VERSION = 'entry-opportunities-v1'
LIFETIME_SECONDS = 3600
TERMINAL = {'expired', 'invalidated', 'superseded'}


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()[:24]


def observed_target(hours, side, entry, trigger_ms):
    # ONLY candles already closed when the trigger happened. Never search farther
    # after seeing an inadequate R:R. Select nearest confirmed pivot first.
    rows = [b for b in hours if b['close_ms'] <= trigger_ms][-24:]
    field = 'high' if side == 'long' else 'low'
    sign = 1 if side == 'long' else -1
    pivots = []
    for a, b, c in zip(rows, rows[1:], rows[2:]):
        if (b[field]-a[field])*sign > 0 and (b[field]-c[field])*sign > 0 and (b[field]-entry)*sign > 0:
            pivots.append(b)
    if pivots:
        point = min(pivots, key=lambda b: abs(b[field]-entry))
        return {'price':point[field], 'basis':'nearest_confirmed_hourly_pivot',
                'close_ms':point['close_ms'], 'timeframe':'1H', 'extrapolated':False}
    if len(rows) < 12:
        return None
    point = (max if side == 'long' else min)(rows[-12:], key=lambda b:b[field])
    if (point[field]-entry)*sign <= 0:
        return None
    return {'price':point[field], 'basis':'prior_12_closed_hour_channel_boundary',
            'close_ms':point['close_ms'], 'timeframe':'1H', 'extrapolated':False}


def scan(package, policy=None):
    policy = policy or vars(Policy())
    result = {'version':VERSION, 'mode':'shadow', 'order_authorized':False,
              'instrument':package.get('instId'), 'as_of':package.get('data_as_of'), 'opportunities':[], 'checks':[]}
    try:
        if package.get('data_quality') != 'valid':
            raise ValueError('market_data_invalid')
        if not (package.get('environment_support') or {}).get('can_open'):
            raise ValueError('environment_not_verified_for_entry')
        bars = legacy.verified_bars(package, '15M'); hours = legacy.verified_bars(package, '1H')
        bid, ask = (legacy.number(package[k]) for k in ('bidPx','askPx'))
        if not 0 < bid <= ask: raise ValueError('invalid_quote')
        macro = str(package.get('macro_4h') or '')
        if not any(m in macro for m in ('4H_MACRO_BULL','4H_MACRO_BEAR','4H_MACRO_RANGE')):
            raise ValueError('macro_environment_missing')
        atr = sum(max(b['high']-b['low'], abs(b['high']-a['close']), abs(b['low']-a['close']))
                  for a,b in zip(bars[-15:-1], bars[-14:]))/14
        if atr <= 0: raise ValueError('zero_entry_volatility')
        last, prev = bars[-1], bars[-2]
        def reject(side, setup, reason, **extra):
            result['checks'].append(dict(side=side, setup=setup, reason=reason, **extra))
        for side, sign, entry in (('long',1,ask), ('short',-1,bid)):
            if (sign == 1 and '4H_MACRO_BEAR' in macro) or (sign == -1 and '4H_MACRO_BULL' in macro):
                reject(side,'all','existing_macro_direction_veto'); continue
            extremum = max if sign == 1 else min
            field = 'high' if sign == 1 else 'low'
            # A prior breakout remains a watch opportunity for four CLOSED bars.
            # Volume is from the same closed candle, not a mutable current ticker.
            breakouts = []
            for i in range(max(8,len(bars)-5),len(bars)):
                b=bars[i]; window=bars[i-8:i]; level=extremum(x[field] for x in window)
                volume=sum(x['volume'] for x in window)/8
                if (b['close']-level)*sign>0 and (b['open']-level)*sign<=0 and volume>0 and b['volume']/volume>=1.1:
                    breakouts.append((i, level))
            level=extremum(x[field] for x in bars[-9:-1])
            reclaim=(last['close']-last['open'])*sign>0 and (last['close']-prev['close'])*sign>0
            touch=last['low']<=prev['low']+atr*.25 if sign==1 else last['high']>=prev['high']-atr*.25
            trend=legacy.hourly_trend(package)==side
            specs=[('trend_pullback_reclaim',len(bars)-1,prev['close'],reclaim and touch and trend)]
            recent = breakouts[-1] if breakouts else None
            if recent:
                i, level=recent
                after=bars[i+1:]
                held=all((b['close']-level)*sign>0 for b in after)
                touched=bool(after) and (last['low']<=level+atr*.25 if sign==1 else last['high']>=level-atr*.25)
                specs.append(('closed_range_breakout',i,level,i==len(bars)-1))
                if after:
                    specs.append(('breakout_retest',i,level,held and touched and (last['close']-last['open'])*sign>0))
            else:
                specs.append(('closed_range_breakout',len(bars)-1,level,False))
            for setup, index, level, triggered in specs:
                origin=bars[index]
                oid=identity([VERSION,package['instId'],side,setup,origin['close_ms'],level])
                op={'id':oid,'side':side,'setup':setup,'origin_close_ms':origin['close_ms'],
                    'created_at':origin['close_ms']/1000,'expires_at':origin['close_ms']/1000+LIFETIME_SECONDS,
                    'trigger_level':level,'state':'observing','reason':'closed_candle_trigger_not_met',
                    'order_authorized':False,'triggered':bool(triggered),'entry_price':entry}
                structural=(min(b['low'] for b in bars[max(0,index-2):])-atr*.1 if sign==1
                            else max(b['high'] for b in bars[max(0,index-2):])+atr*.1)
                # Route B: stop basis is differentiated by setup type. A pullback/retest
                # entry sits near a defined support/resistance level, so its stop anchors
                # to that retest structure (tight) — risk is small, so net RR can clear 2
                # against the same observed target. A breakout-chase entry anchors to the
                # 1.5xATR volatility floor (wide), since post-breakout dispersion is larger.
                if setup in ('trend_pullback_reclaim','breakout_retest'):
                    retest_structural=(min(b['low'] for b in bars[index:index+3] or [last])-atr*.1) if sign==1 \
                        else (max(b['high'] for b in bars[index:index+3] or [last])+atr*.1)
                    stop=retest_structural if sign==1 else retest_structural
                    stop_basis='retest_structure_at_trigger_level'
                else:
                    stop=min(structural,entry-atr*1.5) if sign==1 else max(structural,entry+atr*1.5)
                    stop_basis='max_structural_and_1.5x_closed_atr'
                op.update(stop_loss_price=stop,stop_basis=stop_basis,atr=atr)
                target=observed_target(hours,side,entry,origin['close_ms'])
                op['target_observation']=target
                if not triggered:
                    if recent and setup=='closed_range_breakout' and index<len(bars)-1:
                        op.update(state='awaiting_retest',reason='breakout_waiting_for_retest')
                    if recent and setup=='breakout_retest' and not held:
                        op.update(state='invalidated',reason='closed_trigger_invalidated_by_quote')
                elif (entry-level)*sign<=0:
                    op.update(state='invalidated',reason='closed_trigger_invalidated_by_quote')
                elif (entry-last['close'])*sign>atr*.25:
                    op.update(state='awaiting_retest',reason='quote_moved_beyond_closed_trigger')
                elif target is None:
                    op.update(state='blocked',reason='no_observed_target')
                elif not (0<stop<entry<target['price'] if sign==1 else 0<target['price']<entry<stop):
                    op.update(state='blocked',reason='invalid_geometry')
                else:
                    # Realistic cost: limit entry pays maker fee, OCO stop pays taker fee once, slippage on stop.
                    cost=entry*policy['maker_fee']+max(stop,target['price'])*policy['taker_fee']+entry*policy['slippage']
                    rr=(abs(target['price']-entry)-cost)/(abs(entry-stop)+cost)
                    op.update(net_rr=rr,take_profit_price=target['price'],state='ready' if rr>=policy['minimum_net_rr'] else 'blocked',
                              reason='shadow_candidate' if rr>=policy['minimum_net_rr'] else 'net_rr_below_policy')
                if package['data_as_of']>=op['expires_at']:
                    op.update(state='expired',reason='opportunity_expired')
                result['opportunities'].append(op)
                reject(side,setup,op['reason'])
        result['ready_count']=sum(o['state']=='ready' for o in result['opportunities'])
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        result.update(error=str(exc), opportunities=[], ready_count=0)
    return result


def advance(previous, observations, as_of):
    """Idempotent state tracking; old ready states cannot authorize later orders."""
    current={o['id']:dict(o) for o in observations}
    for old in previous:
        item=current.get(old['id'])
        if item and old['state'] in TERMINAL:
            current[old['id']]=dict(old)  # A dead signal cannot resurrect.
        elif not item:
            item=dict(old)
            item.update(state='expired' if as_of>=old['expires_at'] else 'superseded',
                        reason='opportunity_expired' if as_of>=old['expires_at'] else 'new_closed_structure')
            current[old['id']]=item
    return sorted(current.values(),key=lambda o:(o['created_at'],o['id']),reverse=True)[:60]


def record_cycle(scope, packages, policy, signature):
    """One transaction, account/version/policy isolated; no changes to active catalog."""
    from scripts import strategy_evidence as evidence
    namespace=identity([VERSION,signature,policy])
    now=time.time(); records=[]
    with evidence.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS opportunity_shadow(scope TEXT PRIMARY KEY, namespace TEXT NOT NULL, payload TEXT NOT NULL)')
        row=db.execute('SELECT namespace,payload FROM opportunity_shadow WHERE scope=?',(scope,)).fetchone()
        previous=json.loads(row[1]) if row and row[0]==namespace else {}
        previous_by={r['instrument']:r for r in previous.get('items',[])}
        for p in packages:
            result=scan(p,policy); baseline=legacy.catalog(p,policy)
            result['baseline_plans']=len(baseline['plans'])
            result['baseline_checks']=baseline.get('checks',[])
            result['baseline_error']=baseline.get('error')
            old=previous_by.get(p['instId'],{})
            # Ignore out-of-order frames rather than rolling state backwards.
            if old.get('as_of',0)>p.get('data_as_of',0):
                result=old
            else:
                result['tracked']=advance(old.get('tracked',[]),result['opportunities'],p.get('data_as_of',now))
            records.append(result)
        payload={'version':VERSION,'mode':'shadow','order_authorized':False,'scope':scope,'namespace':namespace,
                 'updated_at':now,'items':records,'auto_promote':False}
        db.execute('INSERT OR REPLACE INTO opportunity_shadow VALUES (?,?,?)',(scope,namespace,evidence.canonical(payload)))
        # Frozen features are retained even if the subsequent model JSON fails.
        event={'comparison':payload,'frozen_packages':packages,'risk_policy':policy,'execution_signature':signature}
        eid=identity([scope,namespace,[(p.get('instId'),p.get('data_as_of')) for p in packages]])
        raw=evidence.canonical(evidence._scrub(event));digest=hashlib.sha256(raw.encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',(eid,scope,'entry_opportunity_shadow',now,raw,digest))
    return payload


def public_status(scope):
    import sqlite3
    from scripts.strategy_evidence import DB_PATH
    try:
        if not DB_PATH.exists():return {'mode':'shadow','status':'pending','items':[]}
        with sqlite3.connect(DB_PATH.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunity_shadow'").fetchone():
                return {'mode':'shadow','status':'pending','items':[]}
            row=db.execute('SELECT payload FROM opportunity_shadow WHERE scope=?',(scope,)).fetchone()
        if not row:return {'mode':'shadow','status':'pending','items':[]}
        result=json.loads(row[0]); result.pop('namespace',None);result.pop('scope',None)
        result['stale']=time.time()-result['updated_at']>1200
        # Keep public payload small; detailed transitions and source candles stay archived.
        for item in result['items']:
            item.pop('tracked',None);item.pop('baseline_checks',None)
        return result
    except (OSError,ValueError,sqlite3.Error):
        return {'mode':'shadow','status':'unavailable','items':[]}
