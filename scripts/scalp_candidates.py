"""Closed 1M execution with 5M structure and 15M bias. DEMO experiment, not a profitability claim.
Targets from volatility are explicitly projections, never represented as observed resistance.
"""
import hashlib
import json

VERSION = 'scalp-minute-v6'


def catalog(package, policy):
    from scripts.entry_candidates import verified_bars, number
    from scripts.demo_scalp_policy import descriptor
    result = {'version': VERSION, 'plans': [], 'checks': [], 'order_authorized': False}
    def reject(side, reason, **details):
        result['checks'].append({'setup': 'scalp_momentum_1m', 'side': side,
                                 'status': 'not_ready', 'reason': reason, **details})
    try:
        if package.get('data_quality') != 'valid': raise ValueError('market_data_invalid')
        if not (package.get('environment_support') or {}).get('can_open'): raise ValueError('environment_not_verified_for_entry')
        one, five, bias = [verified_bars(package, tf) for tf in ('1M', '5M', '15M')]
        bid, ask = number(package['bidPx']), number(package['askPx'])
        if not 0 < bid <= ask: raise ValueError('invalid_quote')
        def atr(rows):
            return sum(max(b['high']-b['low'], abs(b['high']-a['close']), abs(b['low']-a['close']))
                       for a,b in zip(rows[-15:-1], rows[-14:]))/14
        a1, a5 = atr(one), atr(five)
        if min(a1,a5) <= 0: raise ValueError('scalp_1m_volatility_unavailable')
        last, prev = one[-1], one[-2]
        short_mean = sum(r['close'] for r in five[-5:])/5
        slow_mean = sum(r['close'] for r in five[-12:])/12
        bias_mean = sum(r['close'] for r in bias[-8:])/8
        average_volume = sum(r['volume'] for r in one[-9:-1])/8
        # Range-reversion uses the frozen 15M channel only as a context and
        # trigger boundary. It is an additional candidate, never a replacement
        # for the existing breakout/pullback/reversal candidates.
        macro=str(package.get('macro_4h') or '').upper()
        structure=str(package.get('structure_1h') or '').upper()
        range_market=('RANGE' in macro or 'CHOP' in macro or
                      'RANGE' in structure or 'CHOP' in structure)
        range_rows=bias[-12:-1]
        range_low=min(r['low'] for r in range_rows)
        range_high=max(r['high'] for r in range_rows)
        range_mid=(range_low+range_high)/2
        range_buffer=max(a5*.45,a1*1.2)
        range_long=(range_market and last['close']>last['open'] and
                    last['close']>prev['close'] and last['low']<=range_low+range_buffer and
                    last['close']>range_low)
        range_short=(range_market and last['close']<last['open'] and
                     last['close']<prev['close'] and last['high']>=range_high-range_buffer and
                     last['close']<range_high)
        for side, sign in [('long',1), ('short',-1)]:
            action = 'BUY_LONG' if sign == 1 else 'SELL_SHORT'
            entry = ask if sign == 1 else bid
            # Confirmation is relative price structure, not an RSI overbought/oversold veto.
            trend_ok = sign*(short_mean-slow_mean)>0 and sign*(five[-1]['close']-short_mean)>=0
            # Also admit a newly confirmed 5M turn; don't wait for slow averages to cross.
            previous_mid=(five[-2]['open']+five[-2]['close'])/2
            turn_ok = (sign*(five[-2]['close']-five[-2]['open'])<0
                       and sign*(five[-1]['close']-five[-1]['open'])>0
                       and sign*(five[-1]['close']-previous_mid)>0)
            range_reversion = range_long if sign==1 else range_short
            if not (trend_ok or turn_ok or range_reversion):
                reject(side,'scalp_5m_confirmation_not_met'); continue
            if sign*(bias[-1]['close']-bias_mean) < -a5*.5 and not range_reversion:
                reject(side,'scalp_15m_bias_opposed'); continue
            level = (max(r['high'] for r in one[-7:-1]) if sign==1 else min(r['low'] for r in one[-7:-1]))
            breakout = sign*(last['close']-level)>0 and sign*(prev['close']-level)<=0
            pullback_level = prev['high'] if sign==1 else prev['low']
            pullback = sign*(prev['close']-prev['open'])<0 and sign*(last['close']-pullback_level)>0
            if sign*(last['close']-last['open'])<=0 or not (breakout or pullback or range_reversion):
                reject(side,'scalp_1m_trigger_not_met'); continue
            if average_volume<=0 or last['volume']<average_volume*.8:
                reject(side,'scalp_volume_insufficient'); continue
            if range_reversion:
                level = range_low if sign==1 else range_high
            else:
                level = level if breakout else pullback_level
            if sign*(entry-level)<=0 or (not range_reversion and sign*(entry-last['close'])>a1*.6):
                reject(side,'quote_moved_beyond_closed_trigger'); continue
            if range_reversion:
                stop_buffer=max(a1*.15,a5*.08)
                stop=(min(last['low'],prev['low'])-stop_buffer if sign==1
                      else max(last['high'],prev['high'])+stop_buffer)
            else:
                stop = (min(last['low'],prev['low'])-a1*.2 if sign==1 else max(last['high'],prev['high'])+a1*.2)
                stop = min(stop,entry-a1*1.1) if sign==1 else max(stop,entry+a1*1.1)
            setup=('scalp_range_reversion_1m' if range_reversion else
                   'scalp_reversal_1m' if turn_ok and not trend_ok else
                   'scalp_breakout_1m' if breakout else 'scalp_pullback_1m')
            distance_by_setup = {
                'scalp_breakout_1m': max(a1*5.5, a5*4.0),
                'scalp_pullback_1m': max(a1*5.0, a5*3.5),
                'scalp_reversal_1m': max(a1*4.5, a5*3.25),
                'scalp_range_reversion_1m': max(a1*3.5, a5*1.5),
            }
            range_target = range_high if sign==1 else range_low
            distance = (abs(range_target-entry) if range_reversion else distance_by_setup[setup])
            target = (range_target if range_reversion else entry+sign*distance)
            if min(entry,stop,target)<=0: reject(side,'invalid_geometry'); continue
            from scripts.execution_costs import from_policy
            cost=from_policy(entry,max(stop,target),policy,ask-bid)['maker_taker_total']
            rr=(distance-cost)/(abs(entry-stop)+cost)
            if rr<policy['minimum_net_rr']:
                reject(side,'net_rr_below_policy',net_rr=rr,target_distance=distance,stop_distance=abs(entry-stop),cost=cost); continue
            from scripts.strategy_modes import mode_for
            mode=mode_for('scalp'); mode['engine']='demo_scalp_v2'
            plan={'version':VERSION,'instrument':package['instId'],'setup':setup,'action':action,
                  'entry_price':entry,'stop_loss_price':stop,'take_profit_price':target,
                  'horizon':'scalp','strategy_mode':mode,'entry_policy':descriptor(),
                  'created_at':number(package['data_as_of']),'trigger_close_ms':last['close_ms'],
                  'valid_for_seconds':60,'net_rr':rr,'entry_timeframe':'1M',
                  'trigger_level':level,'entry_atr':a1,'chase_atr':.6,
                  'stop_basis':('range_boundary_reclaim_plus_volatility_buffer' if range_reversion else 'two_closed_1m_extremes_with_atr_buffer'),
                  'target_basis':('observed_15m_opposite_range_boundary' if range_reversion else f'projected_{setup}_volatility_target'),
                  'target_observation':({'timeframe':'15M','field':'opposite_range_boundary','price':range_target,
                                         'range_low':range_low,'range_high':range_high,
                                         'extrapolated':False,'derived':False}
                                        if range_reversion else
                                        {'extrapolated':True,'basis':'volatility_projection_not_observed_target'}),
                  'supporting_evidence':([{
                    'ref':'/entry_candles/15M/range_low','value':range_low,
                    'interpretation':'Frozen 15M range lower boundary'} ,{
                    'ref':'/entry_candles/15M/range_high','value':range_high,
                    'interpretation':'Frozen 15M range upper boundary'}]
                    if range_reversion else []) + [
                    {'ref':'/entry_candles/1M/last/close','value':last['close'],'interpretation':'Closed one-minute structural trigger'},
                    {'ref':'/entry_candles/1M/last/volume','value':last['volume'],'interpretation':'Observed trigger candle activity'},
                    {'ref':'/entry_candles/5M/last/close','value':five[-1]['close'],'interpretation':'Observed five-minute directional structure'}],
                  'invalidation':{'price':stop,'timeframe':'1M','condition':'One-minute structural stop breached'},
                  'order_authorized':False}
            plan['id']=hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()[:24]
            result['plans'].append(plan)
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        result['error']=str(exc)
    return result


def validate_quote(package, plan, current):
    from scripts.entry_candidates import number, verified_bars
    current=number(current); sign=1 if plan['action']=='BUY_LONG' else -1
    bar=verified_bars(package,'1M')[-1]
    if sign*(current-plan['trigger_level'])<=0: raise ValueError('program_trigger_lost_during_inference')
    if sign*(current-bar['close'])>plan['entry_atr']*plan['chase_atr']: raise ValueError('program_trigger_chase_limit_exceeded')
    if not (plan['stop_loss_price']<current<plan['take_profit_price'] if sign==1 else plan['take_profit_price']<current<plan['stop_loss_price']):
        raise ValueError('program_quote_outside_stop_target')
    return plan
