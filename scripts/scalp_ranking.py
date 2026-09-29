"""Rank existing valid candidates; never remove, create or authorize an entry.
Scores are transparent bounded heuristics, NOT an empirical win probability.
"""
import math
from scripts.entry_candidates import verified_bars, number
from scripts.execution_costs import from_policy

VERSION='scalp-ranking-v4'
# Ranking changes priority only. It never removes candidates or authorizes an order.
WEIGHTS={'net_rr':.14,'observed_range_coverage':.26,'cost_efficiency':.15,'candle_body':.06,'quote_quality':.08,'direction_alignment':.07,'regime_fit':.18,'breakout_quality':.06}


def _regime_fit(package, plan):
    """Score setup fit without turning a 1H pause inside a 4H trend into a range."""
    macro=str(package.get('macro_4h') or '').split(' (',1)[0].upper()
    structure=str(package.get('structure_1h') or '').split(' (',1)[0].upper()
    setup=str(plan.get('setup') or '')
    action=str(plan.get('action') or '')
    if not macro and not structure:return .5,'unknown'
    macro_side='long' if 'BULL' in macro else 'short' if 'BEAR' in macro else None
    side='long' if action=='BUY_LONG' else 'short' if action=='SELL_SHORT' else None
    if 'RANGE' in macro and not macro_side:
        label='range_chop'
        fit={'range_reversion':1.,'scalp_range_reversion_1m':1.,
             'scalp_reversal_1m':.76,'scalp_pullback_1m':.78,
             'pullback_reclaim':.70,'scalp_breakout_1m':.38,
             'closed_range_breakout':.42}.get(setup,.55)
    elif macro_side:
        label='trend_pause' if 'CHOP' in structure else 'trend'
        # 1H chop weakens a raw breakout but never becomes a blanket entry veto.
        fit={'scalp_breakout_1m':.60 if label=='trend_pause' else .92,
             'closed_range_breakout':.60 if label=='trend_pause' else .90,
             'scalp_pullback_1m':.92 if label=='trend_pause' else .88,
             'pullback_reclaim':.90 if label=='trend_pause' else .86,
             'scalp_reversal_1m':.66 if label=='trend_pause' else .56,
             'scalp_range_reversion_1m':.30,'range_reversion':.35}.get(setup,.60)
    elif 'BULL' in structure or 'BEAR' in structure:
        label='trend'
        fit={'scalp_breakout_1m':.86,'scalp_pullback_1m':.86,
             'scalp_range_reversion_1m':.35}.get(setup,.60)
    else:
        label='mixed';fit=.55
    if setup=='scalp_range_reversion_1m' and plan.get('signal_quality')=='edge_observation':
        fit=min(fit,.40)
    if setup=='scalp_trend_pause_reclaim_1m':
        fit=.46
    if macro_side and side:
        fit+=.06 if macro_side==side else -.12
    return clip(fit),label

def clip(x):return min(1.,max(0.,x))
def legacy_key(item):
    p,q=item
    return (-q['net_rr'],p['instId'],q['id'])


def describe(package,plan,policy):
    try:
        one=verified_bars(package,'1M');five=verified_bars(package,'5M');last=one[-1]
        entry=number(plan['entry_price']);stop=number(plan['stop_loss_price']);target=number(plan['take_profit_price'])
        if plan['action'] not in ('BUY_LONG','SELL_SHORT'):raise ValueError('Invalid candidate direction')
        sign=1 if plan['action']=='BUY_LONG' else -1
        spread=number(package['askPx'])-number(package['bidPx'])
        costs=from_policy(entry,max(stop,target),policy,spread)
        risk=sign*(entry-stop);reward=sign*(target-entry);cost=costs['taker_taker_total']
        entry_atr=number(plan['entry_atr'])
        if min(entry,stop,target,risk,reward,entry_atr)<=0:raise ValueError('Invalid candidate geometry')
        conservative_rr=(reward-cost)/(risk+cost)
        observed_range=max(r['high'] for r in five[-12:])-min(r['low'] for r in five[-12:])
        candle_range=last['high']-last['low']
        body=sign*(last['close']-last['open'])/candle_range if candle_range>0 else 0.
        # A retrace *behind* the close is also stale for edge-reclaim plans.
        chase=(abs(entry-last['close'])/entry_atr if plan.get('setup') in
               {'scalp_range_reversion_1m','scalp_trend_pause_reclaim_1m'} else
               max(0.,sign*(entry-last['close'])/entry_atr))
        fast=sum(r['close'] for r in five[-5:])/5;slow=sum(r['close'] for r in five[-12:])/len(five[-12:])
        alignment=1. if sign*(fast-slow)>0 and sign*(five[-1]['close']-fast)>=0 else .5
        regime_fit,regime_label=_regime_fit(package,plan)
        breakout_quality=.5
        if plan.get('setup')=='scalp_breakout_1m':
            # Recompute the frozen 1M channel from the same verified bars: a
            # copied plan's price fields alone must not change the score scale.
            trigger=(max(r['high'] for r in one[-7:-1]) if sign==1 else
                     min(r['low'] for r in one[-7:-1]))
            close_location=(last['close']-last['low'])/candle_range if sign==1 else (last['high']-last['close'])/candle_range
            recent_volume=sum(r['volume'] for r in one[-9:-1])/8
            breakout_quality=(.5*clip(close_location)+
                              .3*clip(sign*(last['close']-trigger)/(entry_atr*.5))+
                              .2*clip(last['volume']/(recent_volume*1.5))) if candle_range>0 and recent_volume>0 else .5
        terms={'net_rr':clip(conservative_rr/3),'observed_range_coverage':clip(observed_range/reward),
               'cost_efficiency':clip(1-cost/reward),'candle_body':clip(body),
               'quote_quality':clip(1-chase/.6),'direction_alignment':alignment,
               'regime_fit':regime_fit,'breakout_quality':breakout_quality}
        score=100*sum(WEIGHTS[k]*v for k,v in terms.items())
        if not math.isfinite(score):raise ValueError('Invalid ranking input')
        return {'version':VERSION,'candidate_id':plan['id'],'instrument':package['instId'],
                'score':round(score,8),'semantics':'heuristic_not_win_probability','status':'evaluated',
                'terms':terms,'weights':dict(WEIGHTS),'admission_net_rr':plan['net_rr'],
                'conservative_net_rr':conservative_rr,'cost_model':costs,
                'target_projection_distance':reward,'observed_5m_range':observed_range,
                'market_regime':regime_label,'target_observed':False,'order_authorized':False}
    except (ValueError,TypeError,KeyError,OverflowError):
        return {'version':VERSION,'candidate_id':plan.get('id'),'instrument':package.get('instId'),
                'score':0.,'semantics':'heuristic_not_win_probability','status':'reference_rank_fallback',
                'admission_net_rr':plan.get('net_rr'),'order_authorized':False}


def rank(candidates,policy):
    descriptions={plan['id']:describe(p,plan,policy) for p,plan in candidates}
    # A partial metric outage must not silently mix in incomparable zero scores.
    complete=all(d['status']=='evaluated' for d in descriptions.values())
    ordered=sorted(candidates,key=lambda item:((-descriptions[item[1]['id']]['score'],)+legacy_key(item)) if complete else legacy_key(item))
    return ordered,{'version':VERSION,'status':'evaluated' if complete else 'reference_rank_fallback',
                    'candidate_count':len(candidates),'minimum_score':None,'candidates_removed':0,
                    'legacy_order':[p['id'] for _,p in sorted(candidates,key=legacy_key)],
                    'new_order':[p['id'] for _,p in ordered],'metrics':descriptions}
