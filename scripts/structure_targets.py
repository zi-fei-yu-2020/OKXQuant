"""Frozen observed near targets; never extend a target to manufacture net RR."""
from copy import deepcopy
import math

VERSION='structure-targets-v1'

def finite(value):
    if isinstance(value,bool):return None
    try:value=float(value)
    except (ValueError,TypeError,OverflowError):return None
    return value if math.isfinite(value) else None

def layers(plan, series, policy):
    sign=1 if plan['action']=='BUY_LONG' else -1
    entry=float(plan['entry_price']);far=float(plan['take_profit_price'])
    from scripts.execution_costs import from_policy
    costs=from_policy(entry,entry,policy)['taker_taker_total']
    risk=abs(entry-float(plan['stop_loss_price']))
    minimum=max(costs+risk*.2,float(plan.get('entry_atr') or 0)*.75)
    observed=[]
    for tf,rows in series.items():
        field='high' if sign==1 else 'low'
        # The trigger bar does not establish its own opposing boundary.
        for bar in rows[-13:-1]:
            price=finite(bar.get(field));stamp=finite(bar.get('close_ms'))
            if price and stamp and minimum<=sign*(price-entry)<sign*(far-entry):
                observed.append({'price':price,'timeframe':tf,'field':field,
                                 'close_ms':int(stamp),'extrapolated':False})
    near=min(observed,key=lambda item:sign*(item['price']-entry)) if observed else None
    return {'version':VERSION,'near':near,'extension':{'price':far,
            'observation':deepcopy(plan.get('target_observation'))},
            'semantics':'near_observed_structure_then_conditional_extension',
            'entry_gate_changed':False}

def calibrate_stop(plan, series, policy):
    """Only recalibrate already admitted new plans; insufficient RR retains original.

    Never edits a live stop or the original observed target. The gateway sizes
    against the resulting frozen stop, preserving its existing USDT risk budget.
    """
    setup=plan['setup'];horizon=plan.get('horizon')
    tf='5M' if setup=='scalp_reversal_1m' else '1H' if setup=='pullback_reclaim' and horizon=='swing' else None
    if not tf:return
    rows=series.get(tf) or []
    if len(rows)<15:return
    atr=sum(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close']))
            for a,b in zip(rows[-15:-1],rows[-14:]))/14
    if atr<=0:return
    sign=1 if plan['action']=='BUY_LONG' else -1
    entry=float(plan['entry_price']);original=float(plan['stop_loss_price'])
    risk=sign*(entry-original)
    if risk<=0:return
    # A bounded horizon buffer, not an unbounded historical extreme.
    distance=min(max(risk,atr*.55),risk*1.75)
    proposed=entry-sign*distance
    from scripts.execution_costs import from_policy
    cost=from_policy(entry,max(proposed,plan['take_profit_price']),policy)['maker_taker_total']
    rr=(sign*(plan['take_profit_price']-entry)-cost)/(distance+cost)
    accepted=proposed>0 and rr>=policy['minimum_net_rr']
    plan['structure_stop']={'timeframe':tf,'atr':atr,'original_stop':original,
        'proposed_stop':proposed,'applied':accepted and distance>risk,
        'reason':'horizon_buffer_with_existing_risk_budget' if accepted else 'retain_original_geometry',
        'reclaim_level':rows[-2]['close'],'source_close_ms':rows[-2]['close_ms']}
    if accepted and distance>risk:
        plan['stop_loss_price']=proposed;plan['net_rr']=rr
        plan['invalidation']['price']=proposed
        plan['stop_basis']=plan.get('stop_basis','')+'_bounded_'+tf.lower()+'_buffer'
