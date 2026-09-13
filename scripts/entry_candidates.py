"""Closed-candle entry plans for model review, NOT autonomous order authorization.

Fixed structural definitions, observed targets, common cost policy. No network,
filesystem, model calls or account writes. Selecting a plan still goes through
trading_prompt validation, interceptors and the final account risk gateway.
"""
from copy import deepcopy
import hashlib
import json
import math

VERSION = 'closed-candle-plans-v4'
# Entry-quality guardrails: do not sell the exhausted tail of a move or buy the
# panic low. These are deterministic filters, not probability claims.
MIN_PULLBACK_ATR = 0.35
MIN_BREAKOUT_ATR = 1.0
EXTENDED_RSI_SHORT = 30.0
EXTENDED_RSI_LONG = 70.0
EXTENDED_VWAP = 0.65
WIDTHS = {'15M': 900_000, '1H': 3_600_000}


def number(value):
    if isinstance(value, bool): raise ValueError('invalid_number')
    value = float(value)
    if not math.isfinite(value): raise ValueError('invalid_number')
    return value


def seal_candles(raw, timeframe, as_of_ms):
    width = WIDTHS[timeframe]
    rows = []
    for r in raw:
        if len(r) < 9 or str(r[8]) != '1': raise ValueError('unconfirmed_entry_candle')
        start = int(r[0]); o,h,l,c,v = [number(r[i]) for i in range(1,6)]
        if start % width or start + width > as_of_ms or not 0 < l <= min(o,c) <= max(o,c) <= h or v < 0:
            raise ValueError('invalid_entry_candle')
        rows.append({'close_ms':start+width,'open':o,'high':h,'low':l,'close':c,'volume':v})
    rows.sort(key=lambda r:r['close_ms'])
    if len(rows)<15 or rows[-1]['close_ms']!=int(as_of_ms)//width*width:
        raise ValueError('stale_or_insufficient_entry_candles')
    if any(b['close_ms']-a['close_ms']!=width for a,b in zip(rows,rows[1:])):
        raise ValueError('entry_candle_gap_or_duplicate')
    return {'contract':'closed-v1','as_of_ms':int(as_of_ms),'rows':rows[-24:]}


def verified_bars(package, timeframe):
    frames=package.get('entry_candles')
    if not isinstance(frames,dict): raise ValueError('entry_candle_provenance_missing')
    frame=frames.get(timeframe)
    if not isinstance(frame,dict): raise ValueError('entry_candle_provenance_missing')
    as_of=int(number(package.get('data_as_of'))*1000)
    if frame.get('contract')!='closed-v1' or abs(frame.get('as_of_ms',0)-as_of)>1:
        raise ValueError('entry_candle_provenance_missing')
    width=WIDTHS[timeframe];rows=frame.get('rows') or []
    raw=[[r['close_ms']-width,r['open'],r['high'],r['low'],r['close'],r['volume'],0,0,'1'] for r in rows]
    return seal_candles(raw,timeframe,as_of)['rows']


def candle_facts(package):
    """Small, independently rechecked reference set; never invent missing candles."""
    out={}
    try:
        bars=verified_bars(package,'15M')
        for label,bar in [('last',bars[-1]),('previous',bars[-2])]:
            for key in ('open','high','low','close'):
                out[f'/entry_candles/15M/{label}/{key}']={'value':bar[key],'group':'price'}
    except (ValueError,TypeError,KeyError,OverflowError): pass
    return out


def hourly_trend(package):
    """Trend means ordered closed-hour structure, not merely no 4H veto."""
    if package.get('entry_candles'):
        rows=verified_bars(package,'1H');closes=[b['close'] for b in rows]
        fast=sum(closes[-7:])/7;slow=sum(closes[-20:])/min(20,len(closes))
        if closes[-1]>fast>slow:return 'long'
        if closes[-1]<fast<slow:return 'short'
        return 'range'
    # Compatibility for verified adapters; absence is NOT inferred as bullish.
    return {'1H_SWING_BULL':'long','1H_SWING_BEAR':'short'}.get(package.get('structure_1h'),'unknown')


def classify_horizon(package, setup):
    """Classify holding horizon from setup, volatility and regime evidence."""
    if setup == 'closed_range_breakout':
        return 'scalp'
    regime = str(package.get('market_regime') or '')
    atr15 = number(package.get('atr_15m', 0.0) or 0.0)
    atr1h = number(package.get('atr_1h', 0.0) or 0.0)
    price = number(package.get('price', 0.0) or 0.0)
    # Very high short-term volatility is managed as a short-horizon trade even
    # when the trigger is a pullback; quiet aligned trend is a swing trade.
    if price > 0 and atr15 / price >= 0.012:
        return 'scalp'
    if regime in {'BULL_TREND', 'BEAR_TREND'}:
        return 'swing'
    return 'scalp' if atr1h and atr15 / atr1h >= 0.55 else 'swing'

def validate_independent_direction(package, action):
    side='long' if action=='BUY_LONG' else 'short'
    trend=hourly_trend(package)
    if trend != side:
        raise ValueError('独立方案缺少同向1H趋势；逆向恢复必须选择已收盘突破候选，不能以减速或持仓占比代替反转')
    return trend


def catalog(package, policy=None):
    from scripts.risk_policy import Policy
    policy=policy or vars(Policy())
    result={'version':VERSION,'plans':[],'checks':[],'order_authorized':False}
    def rejected(setup,side,reason,**extra):
        result['checks'].append({'setup':setup,'side':side,'status':'not_ready','reason':reason,**extra})
    try:
        if package.get('data_quality')!='valid': raise ValueError('market_data_invalid')
        if not (package.get('environment_support') or {}).get('can_open'): raise ValueError('environment_not_verified_for_entry')
        f=verified_bars(package,'15M');h=verified_bars(package,'1H')
        price,bid,ask=[number(package.get(k)) for k in ('price','bidPx','askPx')]
        if not 0<bid<=ask or price<=0: raise ValueError('invalid_quote')
        at=number(package['data_as_of']);bar=f[-1];prev=f[-2]
        atr=sum(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(f[-15:-1],f[-14:]))/14
        if atr<=0: raise ValueError('zero_entry_volatility')
        macro=str(package.get('macro_4h') or '')
        if not any(k in macro for k in ('4H_MACRO_BULL','4H_MACRO_BEAR','4H_MACRO_RANGE')): raise ValueError('macro_environment_missing')
        rsi_15m=number(package.get('rsi_15m', package.get('rsi', 50.0)))
        rsi_1h=number(package.get('rsi_1h', package.get('rsi', 50.0)))
        vwap_bias=number(package.get('vwap_bias', 0.0))
        # A trend regime is a directional permission, not an unconditional order.
        # The old implementation allowed a short after RSI 19-29 readings and used
        # a tiny retest stop, systematically selling the exhausted tail.
        extended_short = rsi_1h <= EXTENDED_RSI_SHORT or (rsi_15m <= 32.0 and vwap_bias <= -EXTENDED_VWAP)
        extended_long = rsi_1h >= EXTENDED_RSI_LONG or (rsi_15m >= 68.0 and vwap_bias >= EXTENDED_VWAP)
        # A partial rebound/rejection is a measurable event; slow 1H momentum need not already agree.
        range_rows=h[-13:-1]
        range_high=max(b['high'] for b in range_rows)
        range_low=min(b['low'] for b in range_rows)
        hourly_state=hourly_trend(package)
        atr_1h=number(package.get('atr_1h', atr))
        range_reclaim_long=(hourly_state=='range' and bar['close']>bar['open'] and bar['close']>prev['close'] and bar['low']<=range_low+atr_1h*.45)
        range_reclaim_short=(hourly_state=='range' and bar['close']<bar['open'] and bar['close']<prev['close'] and bar['high']>=range_high-atr_1h*.45)
        for side in ('long','short'):
            action='BUY_LONG' if side=='long' else 'SELL_SHORT'
            if (side=='long' and '4H_MACRO_BEAR' in macro) or (side=='short' and '4H_MACRO_BULL' in macro):
                rejected('all',side,'existing_macro_direction_veto');continue
            if side=='short' and extended_short and not (hourly_state=='range' and range_reclaim_short):
                rejected('all',side,'trend_tail_overextended',rsi_1h=rsi_1h,rsi_15m=rsi_15m,vwap_bias=vwap_bias);continue
            if side=='long' and extended_long and not (hourly_state=='range' and range_reclaim_long):
                rejected('all',side,'trend_tail_overextended',rsi_1h=rsi_1h,rsi_15m=rsi_15m,vwap_bias=vwap_bias);continue
            entry=ask if side=='long' else bid
            channel_target=range_high if side=='long' else range_low
            if (entry-bar['close'])*(1 if side=='long' else -1)>atr*.25:
                rejected('all',side,'quote_moved_beyond_closed_trigger');continue
            # A target must be supported by frozen observations. Extending it to a
            # multiple of stop distance cannot establish additional market space.
            # A separately validated extension setup can be added in a future version.
            window=f[-9:-1]   # 8 closed 15M bars (was 12): trigger surfaces earlier in chop
            reclaim=(bar['close']>bar['open'] and bar['close']>prev['close'] and bar['low']<=prev['low']+atr*.25) if side=='long' else (bar['close']<bar['open'] and bar['close']<prev['close'] and bar['high']>=prev['high']-atr*.25)
            level=max(b['high'] for b in window) if side=='long' else min(b['low'] for b in window)
            vol_ratio=float(package.get('vol_ratio') or 1.0)
            breakout=(bar['close']>level and bar['open']<=level and vol_ratio>=1.1) if side=='long' else (bar['close']<level and bar['open']>=level and vol_ratio>=1.1)
            for setup,triggered in [('pullback_reclaim',reclaim),('closed_range_breakout',breakout),('range_reversion',range_reclaim_long if side=='long' else range_reclaim_short)]:
                if setup=='pullback_reclaim' and hourly_trend(package)!=side:
                    rejected(setup,side,'pullback_requires_established_trend');continue
                if not triggered: rejected(setup,side,'closed_candle_trigger_not_met');continue
                hold_level=prev['close'] if setup in {'pullback_reclaim','range_reversion'} else level
                if (entry-hold_level)*(1 if side=='long' else -1)<=0:
                    rejected(setup,side,'closed_trigger_invalidated_by_quote');continue
                # A retest stop must survive ordinary 15M noise. The previous
                # 0.1 ATR pad manufactured high R:R and caused repeated tail stops.
                # Use the wider of structure invalidation and a volatility buffer.
                if setup=='pullback_reclaim':
                    atr_1h=number(package.get('atr_1h', atr))
                    buffer=max(atr*MIN_PULLBACK_ATR, atr_1h*0.20)
                    stop=(prev['low']-buffer) if side=='long' else (prev['high']+buffer)
                    stop_basis='retest_structure_plus_volatility_buffer'
                elif setup=='range_reversion':
                    buffer=max(atr*0.55, atr_1h*0.18)
                    stop=(prev['low']-buffer) if side=='long' else (prev['high']+buffer)
                    stop_basis='range_boundary_reclaim_plus_volatility_buffer'
                else:
                    structural_stop=(min(b['low'] for b in f[-3:])-atr*.1) if side=='long' else (max(b['high'] for b in f[-3:])+atr*.1)
                    volatility_stop=(entry-atr*1.5) if side=='long' else (entry+atr*1.5)
                    stop=min(structural_stop,volatility_stop) if side=='long' else max(structural_stop,volatility_stop)
                    stop_basis='max_structural_3bar_extreme_and_1.5x_atr'
                target=channel_target
                horizon=classify_horizon(package, setup)
                if not (0<stop<entry<target if side=='long' else 0<target<entry<stop):
                    rejected(setup,side,'invalid_geometry');continue
                # Realistic cost: limit entry pays maker fee, OCO stop pays taker fee once, slippage on stop.
                cost=entry*policy['maker_fee']+max(stop,target)*policy['taker_fee']+entry*policy['slippage']
                rr=(abs(target-entry)-cost)/(abs(entry-stop)+cost)
                geometry={'entry_price':entry,'stop_loss_price':stop,'take_profit_price':target}
                if rr<policy['minimum_net_rr']:
                    rejected(setup,side,'net_rr_below_policy',net_rr=rr,geometry=geometry);continue
                reference='/askPx' if side=='long' else '/bidPx'
                plan={'version':VERSION,'instrument':package['instId'],'setup':setup,'action':action,**geometry,
                      'created_at':at,'trigger_close_ms':bar['close_ms'],'valid_for_seconds':300,'net_rr':rr,
                      'target_basis':'prior_12_closed_hour_channel_boundary','stop_basis':stop_basis,'horizon':horizon,
                      'target_observation':{'timeframe':'1H','field':'high' if side=='long' else 'low',
                          'window_start_close_ms':h[-13]['close_ms'],'window_end_close_ms':h[-2]['close_ms'],
                          'price':target,'extrapolated':False},
                      'supporting_evidence':[{'ref':'/macro_4h','value':package['macro_4h'],'interpretation':'现有宏观方向约束允许研究此方向，并非胜率保证'},
                         {'ref':reference,'value':entry,'interpretation':'本轮可观察报价，最终成交和风险仍需核验'},
                         {'ref':'/entry_candles/15M/last/close','value':bar['close'],'interpretation':'已收盘的回收/突破触发，不等待所有慢周期指标同时同向'}],
                      'invalidation':{'price':stop,'timeframe':'15M','condition':'价格突破结构失效点（回踩防守）或1.5倍ATR波动率防线（突破追势），候选失效'},
                      'order_authorized':False}
                plan['id']=hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()[:24]
                result['plans'].append(plan)
        return result
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        result['plans']=[];result['error']=str(exc);return result


def expand_selection(package, raw, policy=None):
    """A model-selected ID may only materialize the matching immutable current plan."""
    if not isinstance(raw,dict) or not raw.get('candidate_id'): return raw
    available=catalog(package,policy)['plans']
    selected=next((p for p in available if p['id']==raw['candidate_id']),None)
    if selected is None: raise ValueError('候选ID已失效、属于其他标的或不在本轮计划中')
    if raw.get('action')!=selected['action']: raise ValueError('候选方向与选择ID不匹配')
    for key in ('entry_price','stop_loss_price','take_profit_price','invalidation'):
        if key in raw and raw[key]!=selected[key]: raise ValueError('不得改写程序候选的价格或失效点')
    result={**deepcopy(raw),**{k:deepcopy(selected[k]) for k in ('entry_price','stop_loss_price','take_profit_price','invalidation','supporting_evidence','valid_for_seconds')}}
    result['confidence']=raw.get('confidence',0)
    result['candidate_origin']=VERSION
    return result


def validate_live_quote(package, candidate_id, current, policy=None):
    """Recheck the frozen trigger against the FINAL quote, after potentially slow model inference."""
    current=number(current)
    plan=next((p for p in catalog(package,policy)['plans'] if p['id']==candidate_id),None)
    if plan is None: raise ValueError('program_plan_no_longer_matches_evidence_or_policy')
    bars=verified_bars(package,'15M');bar=bars[-1];sign=1 if plan['action']=='BUY_LONG' else -1
    if plan['setup']=='pullback_reclaim':level=bars[-2]['close']
    else:level=max(b['high'] for b in bars[-9:-1]) if sign==1 else min(b['low'] for b in bars[-9:-1])
    atr=sum(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(bars[-15:-1],bars[-14:]))/14
    if (current-level)*sign<=0:raise ValueError('program_trigger_lost_during_inference')
    if (current-bar['close'])*sign>atr*.25:raise ValueError('program_trigger_chase_limit_exceeded')
    if not (plan['stop_loss_price']<current<plan['take_profit_price'] if sign==1 else plan['take_profit_price']<current<plan['stop_loss_price']):
        raise ValueError('program_quote_outside_stop_target')
    return plan
