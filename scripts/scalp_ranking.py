"""Rank existing valid candidates; never remove, create or authorize an entry.
Scores are transparent bounded heuristics, NOT an empirical win probability.
"""
import math
from scripts.entry_candidates import verified_bars, number
from scripts.execution_costs import from_policy

VERSION='scalp-ranking-v3'
# Ranking changes priority only. It never removes candidates or authorizes an order.
WEIGHTS={'net_rr':.18,'observed_range_coverage':.30,'cost_efficiency':.15,'candle_body':.08,'quote_quality':.09,'direction_alignment':.10,'regime_fit':.10}


def _regime_fit(package, plan):
    """Return a bounded fit score for setup + direction versus the current regime.

    This is deliberately a ranking signal, not an entry veto. In a 4H range /
    1H chop, mean-reversion and pullback structures receive priority while
    breakouts remain eligible. In aligned higher-timeframe trends, breakouts
    and pullbacks lead. Missing regime evidence is neutral.
    """
    macro=str(package.get('macro_4h') or '').upper()
    structure=str(package.get('structure_1h') or '').upper()
    setup=str(plan.get('setup') or '')
    action=str(plan.get('action') or '')
    if not macro and not structure:
        return 0.5, 'unknown'
    range_market='RANGE' in macro or 'CHOP' in macro or 'RANGE' in structure or 'CHOP' in structure
    trend_market=any(x in macro or x in structure for x in ('BULL','BEAR','TREND')) and not range_market
    side='long' if action=='BUY_LONG' else 'short' if action=='SELL_SHORT' else None
    macro_side='long' if 'BULL' in macro else 'short' if 'BEAR' in macro else None
    if range_market:
        setup_score={'range_reversion':1.0,'scalp_range_reversion_1m':1.0,'scalp_reversal_1m':.88,'scalp_pullback_1m':.82,
                     'pullback_reclaim':.78,'scalp_breakout_1m':.36,'closed_range_breakout':.36}.get(setup,.55)
        label='range_chop'
    elif trend_market:
        setup_score={'scalp_breakout_1m':.96,'closed_range_breakout':.94,'scalp_pullback_1m':.86,
                     'pullback_reclaim':.84,'scalp_reversal_1m':.58,'scalp_range_reversion_1m':.42,'range_reversion':.42}.get(setup,.60)
        label='trend'
    else:
        setup_score=.55;label='mixed'
    if macro_side and side:
        setup_score += .08 if side==macro_side else -.08
    return clip(setup_score), label

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
        chase=max(0.,sign*(entry-last['close'])/entry_atr)
        fast=sum(r['close'] for r in five[-5:])/5;slow=sum(r['close'] for r in five[-12:])/len(five[-12:])
        alignment=1. if sign*(fast-slow)>0 and sign*(five[-1]['close']-fast)>=0 else .5
        regime_fit,regime_label=_regime_fit(package,plan)
        terms={'net_rr':clip(conservative_rr/3),'observed_range_coverage':clip(observed_range/reward),
               'cost_efficiency':clip(1-cost/reward),'candle_body':clip(body),
               'quote_quality':clip(1-chase/.6),'direction_alignment':alignment,
               'regime_fit':regime_fit}
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
