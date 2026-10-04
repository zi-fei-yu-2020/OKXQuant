"""Lifecycle-bound 15M structure / 1H profit management for new swing entries."""
from copy import deepcopy
from scripts.structure_targets import finite
from scripts.exit_coordination import complete_bars,normal_profit

VERSION='swing-structure-v1'

def context(plan,features,*,scope,decision_id,stop):
    if plan.get('horizon')!='swing' or plan.get('setup') not in {'pullback_reclaim','closed_range_breakout','range_reversion'}:return None
    return {'version':VERSION,'scope':scope,'instrument':features.get('instId'),
        'decision_id':decision_id,'candidate_id':plan.get('id'),'setup':plan['setup'],
        'side':'long' if plan['action']=='BUY_LONG' else 'short','initial_stop':stop,
        'entry_atr':plan.get('entry_atr'),'entry_atr_1h':plan.get('entry_atr_1h'),
        'trigger_level':plan.get('trigger_level'),'target':plan['take_profit_price'],
        'target_layers':deepcopy(plan.get('target_layers')),'structure_stop':deepcopy(plan.get('structure_stop'))}

def adopt(tracker,saved,position,scope):
    if tracker.get('entry_context'):return False
    ctx=saved.get('entry_context');created=finite(position.get('cTime'));submitted=finite(saved.get('ts'))
    if not isinstance(ctx,dict) or ctx.get('version')!=VERSION or ctx.get('scope')!=scope or ctx.get('instrument')!=position.get('instId'):return False
    if ctx.get('side')!=(position.get('posSide') or position.get('side')) or not ctx.get('decision_id') or ctx['decision_id']!=saved.get('decision_id'):return False
    if created is None or submitted is None or not -2<=created/1000-submitted<=300:return False
    tracker.update(entry_context=deepcopy(ctx),setup=ctx['setup'],decision_id=ctx['decision_id'])
    return True

def evaluate(tracker,factor,*,entry,current,side,now,policy,tick,thresholds):
    ctx=tracker.get('entry_context') or {};bound=tracker.get('positionIdentity') or {}
    inactive={'enabled':False,'version':VERSION,'exit':False,'reason':'swing_context_unavailable'}
    if ctx.get('version')!=VERSION or ctx.get('scope')!=bound.get('scope') or ctx.get('instrument')!=bound.get('instId') or ctx.get('side')!=side or bound.get('side')!=side or ctx.get('decision_id')!=tracker.get('decision_id'):return inactive
    atr=finite(factor.get('atr_1h'));tick=finite(tick)
    if not atr or atr<=0 or not tick or tick<=0:return inactive
    sign=1 if side=='long' else -1;entry=finite(entry);current=finite(current);stop=finite(ctx.get('initial_stop'))
    if not entry or not current or not stop or sign*(entry-stop)<=0:return inactive
    from scripts.execution_costs import holding_budget
    receipt=tracker.get('openingCostReceipt')
    if not isinstance(receipt,dict) or receipt.get('identity')!=bound or receipt.get('decision_id')!=tracker.get('decision_id'):receipt=None
    budget=holding_budget(entry,current,policy,receipt)
    minimum=max(sign*(entry-stop)*.2,atr*.25,tick*2)
    budget['minimum_net_profit_distance']=minimum
    peak=tracker.get('highWaterMark' if side=='long' else 'lowWaterMark',current)
    from scripts.profit_protection import floor_plan
    protection=floor_plan(side,entry,current,peak,stop,atr,taker_fee=policy.taker_fee,slippage=policy.slippage,thresholds=thresholds,cost_budget=budget)
    old=finite(tracker.get('trailingStopPx'));desired=finite(protection.get('stop'));cloud=finite(tracker.get('exchangeStopPx'))
    stops=[v for v in (old,desired,cloud) if v and v>0]
    effective=(max if sign==1 else min)(stops) if stops else None
    bars=complete_bars(factor.get('closed_15m') or [],bound.get('cTime'),now,900000)
    coordinated=normal_profit(side=side,entry=entry,current=current,peak=peak,atr=atr,tick=tick,
        costs=budget['total_cost_distance'],minimum_net=minimum,stop=effective,targets=ctx.get('target_layers'),bars=bars)
    if coordinated['eligible']:
        protection.update(kinetic_exit=True,normal_profit_reason=coordinated['reason'],pullback=max(0.,sign*(float(peak)-current)),pullback_threshold=atr*.3)
    # New failure decisions require two full post-fill 15M closes, not 1M noise.
    level=finite(ctx.get('trigger_level'));a15=finite(ctx.get('entry_atr'))
    failed=bool(ctx.get('setup')=='pullback_reclaim' and len(bars)>=2 and level and a15 and sign*(current-entry)<0 and
        all(sign*(float(b['close'])-level)<-max(a15*.35,tick*2) for b in bars[-2:]) and
        sign*(current-level)<-max(a15*.35,tick*2))
    return {'enabled':True,'version':VERSION,'exit':failed,'reason':'swing_reclaim_lost' if failed else 'swing_structure_holding',
        'protection':protection,'cost_model':budget,'minimum_net_profit_distance':minimum,
        'coordination':coordinated,'closed_bars':len(bars),'atr':atr,'volatility_source':'atr_1h',
        'peak_gain':max(0.,sign*(float(peak)-entry)),
        'target_layers':deepcopy(ctx.get('target_layers'))}
