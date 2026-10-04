"""Receipt-grounded geometry replay; no synthetic fills or profitability claims."""
import math

VERSION='joint-execution-replay-v1'


def finite(value):
    if value is None or isinstance(value,bool):return None
    try:value=float(value)
    except (ValueError,TypeError,OverflowError):return None
    return value if math.isfinite(value) else None


def geometry(candidate, submission):
    candidate=candidate or {};ctx=submission.get('entry_context') or {}
    return {'version':VERSION,'candidate_id':candidate.get('id') or submission.get('candidate_id'),
            'setup':candidate.get('setup') or submission.get('setup'),'side':submission.get('side'),
            'signal_entry':candidate.get('entry_price'),
            'submitted_entry':submission.get('entry'),'stop':submission.get('stop'),
            'target':submission.get('take_profit'),'trigger_level':candidate.get('trigger_level') or ctx.get('trigger_level'),
            'reentry_level':candidate.get('reentry_level') or ctx.get('reentry_level'),
            'entry_atr':candidate.get('entry_atr') or ctx.get('entry_atr'),
            'contract_base_units':(submission.get('cost_model') or {}).get('contract_base_units'),
            'target_observation':candidate.get('target_observation'),
            'target_layers':candidate.get('target_layers') or ctx.get('target_layers'),
            'structure_stop':candidate.get('structure_stop') or ctx.get('structure_stop'),
            'trigger_close_ms':candidate.get('trigger_close_ms') or ctx.get('trigger_close_ms')}


def touch_path(bars, *, entry_ms, stop, target, side):
    """Only whole post-fill bars. Same-bar double touches remain ambiguous."""
    sign=1 if side=='long' else -1
    eligible=sorted((candle for candle in bars if finite(candle.get('close_ms')) is not None
                     and float(candle['close_ms'])-60000>=entry_ms),key=lambda candle:candle['close_ms'])
    previous=None
    for bar in eligible:
        high=finite(bar.get('high'));low=finite(bar.get('low'));closed=float(bar['close_ms'])
        if high is None or low is None or low<=0 or high<low:return {'status':'invalid_bars'}
        if previous is None and closed-60000-entry_ms>=60000:return {'status':'incomplete_path'}
        if previous is not None and closed-previous!=60000:return {'status':'incomplete_path'}
        previous=closed
        stopped=low<=stop if sign==1 else high>=stop
        targeted=high>=target if sign==1 else low<=target
        if stopped or targeted:
            return {'status':'ambiguous' if stopped and targeted else 'stop_first' if stopped else 'target_first',
                    'close_ms':closed,'partial_entry_candle_excluded':True}
    return {'status':'no_touch' if eligible else 'unavailable','partial_entry_candle_excluded':True}


def analyze(frozen, history, observations=(), *, bars=(), fees_verified=False):
    result={'version':VERSION,'status':'unavailable','order_authorized':False,
            'profitability_backtest':False,'classification':'insufficient_evidence'}
    if not isinstance(frozen,dict):return result
    side=history.get('direction') or history.get('posSide')
    if side not in ('long','short') or frozen.get('side')!=side:return result
    signal=finite(frozen.get('signal_entry'));actual=finite(history.get('openAvgPx'))
    stop=finite(frozen.get('stop'));target=finite(frozen.get('target'))
    if any(value is None or value<=0 for value in (signal,actual,stop,target)):return result
    sign=1 if side=='long' else -1;risk=sign*(signal-stop);reward=sign*(target-signal)
    if risk<=0 or reward<=0:return result
    actual_risk=sign*(actual-stop);actual_reward=sign*(target-actual)
    deterioration=sign*(actual-signal)
    peaks=[finite((sample.get('management_state') or {}).get('peak_gain')) for sample in observations]
    peaks=[value for value in peaks if value is not None and value>=0]
    result.update(status='observed',signal_entry=signal,actual_entry=actual,
                  submitted_entry=finite(frozen.get('submitted_entry')),stop=stop,target=target,
                  planned_risk=risk,actual_risk=actual_risk,planned_reward=reward,actual_reward=actual_reward,
                  entry_deterioration=deterioration,entry_deterioration_r=deterioration/risk,
                  reward_compression_fraction=deterioration/reward,
                  planned_gross_rr=reward/risk,actual_gross_rr=actual_reward/actual_risk if actual_risk>0 else None,
                  sampled_peak_gain=max(peaks) if peaks else None,
                  holding_samples=len(observations),peak_basis='periodic_observations_not_tick_extremes')
    result['signal_reference_path']={'status':'unavailable','reason':'post_fill_ohlc_not_provided'}
    failed=any((sample.get('management_state') or {}).get('exit') is True for sample in observations)
    closed=finite(history.get('closeAvgPx'))
    breached=closed is not None and closed>0 and sign*(closed-stop)<=0
    result['frozen_stop_breached_at_exit']=breached
    if actual_risk<=0 or actual_reward<=0:classification='geometry_invalid_at_fill'
    elif deterioration/risk>=.25 or deterioration/reward>=.2:classification='execution_deterioration'
    elif failed:classification='observed_structure_failure'
    elif breached:classification='frozen_stop_breach_at_exit'
    else:classification='no_material_entry_deterioration_structure_unproven'
    result['classification']=classification
    result['diagnostic_thresholds']={'entry_deterioration_r':.25,'reward_compression_fraction':.2,'entry_gate':False}
    if fees_verified:
        gross=finite(history.get('pnl'));net=finite(history.get('realizedPnl'))
        result['cost_only_loss']=gross is not None and net is not None and gross>=0 and net<0
        contracts=finite(history.get('closeTotalPos'));contract_units=finite(frozen.get('contract_base_units'))
        fee=finite(history.get('fee'))
        if contracts is not None and contracts>0 and contract_units is not None and contract_units>0 and fee is not None:
            fee_distance=max(0.,-fee)/(contracts*contract_units)
            result.update(settled_fee_distance=fee_distance,
                          net_target_distance_at_settled_fee=actual_reward-fee_distance,
                          cost_semantics='retrospective_total_fees_not_signal_time_forecast')
    opened=finite(history.get('cTime'))
    if bars and opened is not None:
        result['signal_reference_path']=touch_path(bars,entry_ms=opened,stop=stop,target=target,side=side)
        result['path_semantics']='frozen_geometry_after_actual_fill_not_hypothetical_signal_fill'
    return result
