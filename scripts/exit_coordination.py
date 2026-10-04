"""Pure normal-profit decision before an armed cloud floor; no stop relaxation."""
from scripts.structure_targets import finite

def complete_bars(rows, created, now, width):
    created=finite(created);now=finite(now)
    if created is None or now is None:return []
    usable=[]
    for row in rows:
        stamp=finite(row.get('close_ms'));closed=finite(row.get('close'));opened=finite(row.get('open'))
        if stamp is None or closed is None or opened is None or min(closed,opened)<=0:return []
        if stamp-width>=created:usable.append(row)
    if not usable or not 0<=now*1000-float(usable[-1]['close_ms'])<=width*1.5:return []
    if any(float(b['close_ms'])-float(a['close_ms'])!=width for a,b in zip(usable,usable[1:])):return []
    return usable

def normal_profit(*,side,entry,current,peak,atr,tick,costs,minimum_net,stop,targets,bars):
    result={'eligible':False,'reason':'normal_profit_not_ready'}
    values=[finite(v) for v in (entry,current,peak,atr,tick,costs,minimum_net)]
    if side not in ('long','short') or any(v is None or v<=0 for v in values):return result
    entry,current,peak,atr,tick,costs,minimum_net=values
    sign=1 if side=='long' else -1;gain=sign*(current-entry);peak_gain=sign*(peak-entry)
    buffer=max(tick*2,atr*.2);stop=finite(stop)
    if stop and sign*(current-stop)<=0:return {**result,'reason':'protection_crossed_market_exit_only'}
    if gain<costs+minimum_net+buffer or not bars:return result
    last=bars[-1]
    body=sign*(float(last['close'])-float(last['open']))
    slowing=body<0 or (len(bars)>=2 and sign*(float(last['close'])-float(bars[-2]['close']))<=0)
    if not slowing:return result
    near=finite(((targets or {}).get('near') or {}).get('price'))
    near_distance=sign*(near-entry) if near else None
    approaching=near_distance and near_distance>costs+minimum_net and gain>=near_distance*.9
    giveback=peak_gain-gain
    approaching_floor=stop and 0<sign*(current-stop)<=max(atr*.7,tick*4) and giveback>=max(atr*.3,tick*2)
    if approaching or approaching_floor:
        result.update(eligible=True,reason='observed_near_target_exhaustion' if approaching else 'cloud_floor_preemptive_profit',
                      current_net_gain=gain-costs,peak_net_gain=peak_gain-costs,
                      minimum_net_profit_distance=minimum_net,protected_stop=stop,
                      trigger_close_ms=last['close_ms'],cloud_protection_retained=True)
    return result
