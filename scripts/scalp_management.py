"""Post-entry management only: immutable entry context and closed-bar failure evidence.
No entry authorization, universe filter, cooldown, leverage or risk-budget changes.
Thresholds are experimental engineering rules, not calibrated trading probabilities.
"""
from copy import deepcopy
import math

VERSION='scalp-management-v3'
CONTEXT_VERSIONS={'scalp-management-v2',VERSION}
RISK_REDUCTION_R=.75
FOLLOW_THROUGH_R=1.2
SETUPS={'scalp_breakout_1m','scalp_pullback_1m','scalp_reversal_1m'}

def number(x):
    if isinstance(x,bool):return None
    try:x=float(x)
    except (ValueError,TypeError,OverflowError):return None
    return x if math.isfinite(x) else None


def context(plan,features,*,scope,decision_id,stop):
    if plan.get('setup') not in SETUPS:return None
    action=plan.get('action');side='long' if action=='BUY_LONG' else 'short' if action=='SELL_SHORT' else None
    regime=str(features.get('structure_1h') or '')
    regime_side='long' if 'BULL' in regime else 'short' if 'BEAR' in regime else None
    return {'version':VERSION,'scope':scope,'instrument':features.get('instId'),'decision_id':decision_id,
            'candidate_id':plan.get('id'),'setup':plan['setup'],'side':side,
            'trigger_level':plan.get('trigger_level'),'entry_atr':plan.get('entry_atr'),
            'initial_stop':stop,'trigger_close_ms':plan.get('trigger_close_ms'),
            'regime':regime,'alignment':'countertrend' if regime_side and regime_side!=side else 'aligned' if regime_side else 'range'}


def adopt(tracker,saved,position,scope):
    """Never relabel an existing tracker from another account or entry lifecycle."""
    if tracker.get('entry_context'):return False
    ctx=saved.get('entry_context')
    created=number(position.get('cTime'));submitted=number(saved.get('ts'))
    side=position.get('posSide') or position.get('side')
    if (not isinstance(ctx,dict) or ctx.get('version') not in CONTEXT_VERSIONS or ctx.get('scope')!=scope
        or ctx.get('instrument')!=position.get('instId') or ctx.get('side')!=side
        or not ctx.get('decision_id') or ctx.get('decision_id')!=saved.get('decision_id')
        or created is None or submitted is None or not -2<=created/1000-submitted<=300):return False
    tracker.update(setup=ctx['setup'],entry_context=deepcopy(ctx),decision_id=saved['decision_id'])
    return True


def evaluate(tracker,factor,*,entry,current,side,now,policy,tick):
    ctx=tracker.get('entry_context') or {};setup=ctx.get('setup')
    inactive={'version':VERSION,'enabled':False,'state':'UNAVAILABLE','exit':False,'reason':'entry_context_unavailable'}
    if ctx.get('version') not in CONTEXT_VERSIONS or setup not in SETUPS or ctx.get('side')!=side:return inactive
    atr=number(ctx.get('entry_atr'));trigger=number(ctx.get('trigger_level'));stop=number(ctx.get('initial_stop'))
    entry=number(entry);current=number(current);tick=number(tick)
    if any(v is None or v<=0 for v in (atr,trigger,stop,entry,current,tick)):return inactive
    sign=1 if side=='long' else -1;risk=sign*(entry-stop)
    if risk<=0:return inactive
    old=tracker.get('scalpManagement') or {};observed_peak=number(tracker.get('highWaterMark' if side=='long' else 'lowWaterMark')) or entry
    gain=sign*(current-entry);peak=max(0.,sign*(observed_peak-entry),number(old.get('peak_gain')) or 0.)
    # Marketable limit entry may pay taker, so do not assume a maker fill.
    from scripts.execution_costs import from_policy
    cost_model=from_policy(entry,entry,policy)
    costs=cost_model['taker_taker_total']
    buffer=max(tick*2,atr*.2)
    activation=max(costs+buffer, risk*(.6 if ctx.get('alignment')=='countertrend' else .8))
    stage='FOLLOW_THROUGH' if peak>=risk*.5 else 'INITIAL_CONFIRMATION'
    protection={'active':False,'kinetic_exit':False,'reason':'net_cost_not_covered','activation':activation}
    # Net-cost coverage is required to call an exit profitable, not to begin
    # reducing the original downside. A partial risk floor may still realize a loss.
    retained=None;kind=None;tier=0
    if peak>=activation:
        tier=2 if peak>=max(activation,risk*1.2) else 1
        retained=max(costs,peak*(.65 if tier==2 else .5));kind='profit_lock'
    elif peak>=risk*FOLLOW_THROUGH_R:
        retained=peak*.35;kind='risk_reduction'
    elif peak>=risk*RISK_REDUCTION_R:
        retained=-risk*.25;kind='risk_reduction'
    if retained is not None:
        desired=entry+sign*retained
        previous=number(old.get('protected_stop'))
        if previous:desired=max(previous,desired) if side=='long' else min(previous,desired)
        crossed=sign*(current-desired)<=0
        # An already armed floor remains authoritative inside the market buffer.
        # Keep it rather than silently deactivating it or issuing a tighter near-market stop.
        if previous and not crossed and sign*(current-desired)<buffer:
            desired=previous;crossed=sign*(current-desired)<=0
        if crossed or sign*(current-desired)>=buffer or previous is not None:
            covers_cost=sign*(desired-entry)>=costs
            kind='profit_lock' if covers_cost else 'risk_reduction'
            protection={'active':True,'kind':kind,'net_cost_covered':covers_cost,
                        'kinetic_exit':False,'stop':desired,'crossed':crossed,
                        'activation':activation,'cost_buffer':costs,'market_buffer':buffer,
                        'peak_gain':peak,'initial_risk':risk,'tier':tier,'retained_gain':sign*(desired-entry)}
            stage=('PROFIT_LOCK' if tier==2 else 'COST_PROTECTED') if covers_cost else 'RISK_REDUCED'
    result={'version':VERSION,'enabled':True,'state':stage,'exit':False,'reason':'structure_pending',
            'setup':setup,'alignment':ctx.get('alignment'),'peak_gain':peak,
            'initial_risk':risk,'risk_reduction_activation_r':RISK_REDUCTION_R,'profit_r':gain/risk,
            'peak_r':peak/risk,'entry_atr':atr,'cost_distance':costs,'cost_model':cost_model,'protection':protection}
    if protection.get('active'):result['protected_stop']=protection['stop']
    elif old.get('protected_stop'):result['protected_stop']=old['protected_stop']
    created=number((tracker.get('positionIdentity') or {}).get('cTime'))
    if created is None:return {**result,'reason':'position_time_unavailable'}
    rows=factor.get('closed_1m') or []
    try:
        # Discard the entry's partially-held candle. Consecutive complete post-entry
        # candles are required; time alone and an adverse tick never mean failure.
        bars=[r for r in rows if int(r['close_ms'])-60000>=created]
        if not bars or any(int(b['close_ms'])-int(a['close_ms'])!=60000 for a,b in zip(bars,bars[1:])):
            return {**result,'reason':'closed_bar_sequence_unavailable'}
        if not 0<=now*1000-int(bars[-1]['close_ms'])<=90000:
            return {**result,'reason':'closed_bars_stale'}
        if any(number(r.get('close')) is None or number(r.get('close'))<=0 for r in bars):return result
        base=3 if setup=='scalp_pullback_1m' else 2
        needed=base+(1 if ctx.get('alignment')=='aligned' else 0)
        result.update(closed_bars=len(bars),required_bars=needed,last_close_ms=bars[-1]['close_ms'],
                      closed_evidence=[{'close_ms':r['close_ms'],'close':r['close']} for r in bars[-2:]])
        if len(bars)<needed:return result
        # Pullbacks have more noise allowance; no-fail on mere lack of profit.
        allowance=max(tick*2,atr*(.4 if setup=='scalp_pullback_1m' else .25))
        broken=all(sign*(number(r['close'])-trigger)<-allowance for r in bars[-2:])
        current_broken=sign*(current-trigger)<-allowance
        if broken and current_broken and gain<0:
            result.update(state='FAILED',exit=True,reason='follow_through_lost' if peak>=risk*RISK_REDUCTION_R else 'two_closed_bars_reentered_invalidated_structure',
                          trigger_level=trigger,invalidation_buffer=allowance)
        else:result['reason']='structure_holding_or_recovered'
    except (ValueError,TypeError,KeyError,OverflowError):result['reason']='closed_bar_evidence_invalid'
    return result
