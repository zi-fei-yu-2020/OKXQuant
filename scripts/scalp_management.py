"""Post-entry management only: immutable entry context and closed-bar failure evidence.
No entry authorization, universe filter, cooldown, leverage or risk-budget changes.
Thresholds are experimental engineering rules, not calibrated trading probabilities.
"""
from copy import deepcopy
import math

VERSION='scalp-management-v7'
CONTEXT_VERSIONS={'scalp-management-v2','scalp-management-v4','scalp-management-v5','scalp-management-v6',VERSION}
RISK_REDUCTION_R=.75
FOLLOW_THROUGH_R=1.2
ALIGNED_ACTIVATION_R=.8
COUNTERTREND_ACTIVATION_R=.6
SETUPS={'scalp_breakout_1m','scalp_pullback_1m','scalp_reversal_1m','scalp_range_reversion_1m','scalp_trend_pause_reclaim_1m'}
PROFILES={
    'scalp_breakout_1m':{'activation_r':1.1,'net_floor_r':.2,'retain':.35,'extended_retain':.55,'tier2_r':2.,'failure_atr':.35,'bars':3},
    'scalp_pullback_1m':{'activation_r':1.,'net_floor_r':.2,'retain':.4,'extended_retain':.6,'tier2_r':1.8,'failure_atr':.4,'bars':4},
    'scalp_reversal_1m':{'activation_r':.85,'net_floor_r':.2,'retain':.55,'extended_retain':.7,'tier2_r':1.4,'failure_atr':.3,'bars':3},
    'scalp_range_reversion_1m':{'activation_r':.8,'net_floor_r':.15,'retain':.6,'extended_retain':.75,'tier2_r':1.3,'failure_atr':.45,'bars':3},
    'scalp_trend_pause_reclaim_1m':{'activation_r':1.1,'net_floor_r':.2,'retain':.35,'extended_retain':.55,'tier2_r':2.,'failure_atr':.45,'bars':4}}

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
    macro=str(features.get('macro_4h') or '')
    if plan['setup']=='scalp_trend_pause_reclaim_1m' and regime_side is None:
        regime_side='long' if 'BULL' in macro else 'short' if 'BEAR' in macro else None
    return {'version':VERSION,'scope':scope,'instrument':features.get('instId'),'decision_id':decision_id,
            'candidate_id':plan.get('id'),'setup':plan['setup'],'side':side,
            'trigger_level':plan.get('trigger_level'),'reentry_level':plan.get('reentry_level'),
            'entry_atr':plan.get('entry_atr'),
            'initial_stop':stop,'trigger_close_ms':plan.get('trigger_close_ms'),
            'signal_entry':plan.get('entry_price'),'target':plan.get('take_profit_price'),
            'target_observation':deepcopy(plan.get('target_observation')),
            'management_profile':deepcopy(PROFILES[plan['setup']]),
            'regime':regime,'macro_regime':macro,'alignment':'countertrend' if regime_side and regime_side!=side else 'aligned' if regime_side else 'range'}


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
    from scripts.execution_costs import holding_budget
    receipt=tracker.get('openingCostReceipt') or {}
    if receipt.get('identity')!=tracker.get('positionIdentity') or receipt.get('decision_id')!=tracker.get('decision_id'):receipt=None
    cost_model=holding_budget(entry,current,policy,receipt)
    costs=cost_model['total_cost_distance']
    profile=ctx.get('management_profile') if ctx.get('version')==VERSION else None
    profile=profile or PROFILES[setup]
    if not isinstance(profile,dict):return {**inactive,'reason':'management_profile_unavailable'}
    profile={field:number(profile.get(field)) for field in PROFILES[setup]}
    if any(value is None or value<=0 for value in profile.values()):return {**inactive,'reason':'management_profile_unavailable'}
    if profile['retain']>=1 or profile['extended_retain']>=1 or not profile['bars'].is_integer() or profile['bars']>60:
        return {**inactive,'reason':'management_profile_unavailable'}
    profile['bars']=int(profile['bars'])
    buffer=max(tick*2,atr*.2)
    activation_r=profile['activation_r']*(.85 if ctx.get('alignment')=='countertrend' else 1.)
    minimum_net=max(risk*profile['net_floor_r'],atr*.25,tick*2)
    activation=max(costs+minimum_net+buffer, risk*activation_r)
    stage='FOLLOW_THROUGH' if peak>=risk*.5 else 'INITIAL_CONFIRMATION'
    protection={'active':False,'kinetic_exit':False,'reason':'net_cost_not_covered','activation':activation}
    # Net-cost coverage is required to call an exit profitable, not to begin
    # reducing the original downside. A partial risk floor may still realize a loss.
    retained=None;kind=None;tier=0
    if peak>=activation:
        tier=2 if peak>=max(activation,risk*profile['tier2_r']) else 1
        fraction=profile['extended_retain'] if tier==2 else profile['retain']
        target=number(ctx.get('target'));target_distance=sign*(target-entry) if target else None
        if setup=='scalp_range_reversion_1m' and target_distance and target_distance>0 and peak>=target_distance*.75:
            fraction=max(fraction,.8)
        retained=costs+max(minimum_net,(peak-costs)*fraction);kind='profit_lock'
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
            'initial_risk':risk,'risk_reduction_activation_r':RISK_REDUCTION_R,'activation_target_r':activation_r,'profit_r':gain/risk,
            'peak_r':peak/risk,'entry_atr':atr,'cost_distance':costs,'cost_model':cost_model,'protection':protection}
    result.update(management_profile=deepcopy(profile),minimum_net_profit_distance=minimum_net,
                  current_net_gain=gain-costs,peak_net_gain=peak-costs,
                  target_net_distance=sign*(number(ctx['target'])-entry)-costs if number(ctx.get('target')) else None)
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
        base=profile['bars']
        needed=base+(1 if ctx.get('alignment')=='aligned' else 0)
        result.update(closed_bars=len(bars),required_bars=needed,last_close_ms=bars[-1]['close_ms'],
                      closed_evidence=[{'close_ms':r['close_ms'],'close':r['close']} for r in bars[-2:]])
        if len(bars)<needed:return result
        # Pullbacks have more noise allowance; no-fail on mere lack of profit.
        allowance=max(tick*2,atr*profile['failure_atr'])
        # A new range-reversion thesis loses its 1M reclaim before the wider
        # 15M boundary fails. Legacy entry contexts have no frozen reclaim level
        # and continue using their original boundary without retrospective edits.
        failure_level=(number(ctx.get('reentry_level')) if setup=='scalp_range_reversion_1m'
                       and ctx.get('version') in {'scalp-management-v6',VERSION} else None)
        failure_level=failure_level if failure_level and failure_level>0 else trigger
        broken=all(sign*(number(r['close'])-failure_level)<-allowance for r in bars[-2:])
        current_broken=sign*(current-failure_level)<-allowance
        if broken and current_broken and gain<0:
            result.update(state='FAILED',exit=True,reason='follow_through_lost' if peak>=risk*RISK_REDUCTION_R else 'two_closed_bars_reentered_invalidated_structure',
                          trigger_level=trigger,failure_level=failure_level,invalidation_buffer=allowance)
        else:result['reason']='structure_holding_or_recovered'
        target=number(ctx.get('target'));target_distance=sign*(target-entry) if target else None
        net_ready=gain>=costs+minimum_net+buffer
        target_approach=setup=='scalp_range_reversion_1m' and target_distance and target_distance>0 and gain>=target_distance*.9
        turning=all(number(row.get('open')) is not None and sign*(number(row['close'])-number(row['open']))<0 for row in bars[-2:])
        continuation_exhausted=setup!='scalp_range_reversion_1m' and peak>=risk*profile['tier2_r'] and turning and peak-gain>=max(atr*.8,tick*2)
        if net_ready and protection.get('active') and not protection.get('crossed') and (target_approach or continuation_exhausted):
            result['normal_profit_reason']='range_target_approach' if target_approach else 'confirmed_continuation_exhaustion'
            protection.update(kinetic_exit=True,normal_profit_reason=result['normal_profit_reason'],
                              pullback=peak-gain,pullback_threshold=max(atr*.8,tick*2))
    except (ValueError,TypeError,KeyError,OverflowError):result['reason']='closed_bar_evidence_invalid'
    return result
