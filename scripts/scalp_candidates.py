"""Closed 1M execution with 5M structure and 15M bias. Not a profitability claim.
Momentum targets are volatility projections; range-edge targets use sealed 15M bars.
"""
import hashlib
import json

VERSION = 'scalp-minute-v9'


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
        # A 1H pause inside an explicit 4H trend is NOT a 4H range.
        macro=str(package.get('macro_4h') or '').split(' (',1)[0].upper()
        range_market='RANGE' in macro and not any(x in macro for x in ('BULL','BEAR'))
        trend_pause=('CHOP' in str(package.get('structure_1h') or '').upper() and
                     any(x in macro for x in ('BULL','BEAR')))
        range_rows=bias[-12:-1]
        range_low=min(r['low'] for r in range_rows)
        range_high=max(r['high'] for r in range_rows)
        range_mid=(range_low+range_high)/2
        range_buffer=min(max(a5*.35,a1*.25),(range_high-range_low)*.18)
        exploratory_buffer=max(a5*.45,a1*1.2)
        from scripts.execution_costs import from_policy
        from scripts.strategy_modes import mode_for
        def add_plan(setup, side, sign, entry, level, stop, target, *, reclaim=None, signal_quality=None):
            if not (0<stop<entry<target if sign==1 else 0<target<entry<stop):
                reject(side,'invalid_geometry',setup=setup);return
            distance=sign*(target-entry)
            cost=from_policy(entry,max(stop,target),policy,ask-bid)['maker_taker_total']
            rr=(distance-cost)/(sign*(entry-stop)+cost)
            if rr<policy['minimum_net_rr']:
                reject(side,'net_rr_below_policy',setup=setup,net_rr=rr,target_distance=distance,
                       stop_distance=abs(entry-stop),cost=cost)
                return
            mode=mode_for('scalp');mode['engine']='demo_scalp_v2'
            is_range=setup in {'scalp_range_reversion_1m','scalp_trend_pause_reclaim_1m'}
            evidence=[]
            if is_range:
                evidence=[{'ref':'/entry_candles/15M/range_low','value':range_low,
                           'interpretation':'Frozen 15M range lower boundary'},
                          {'ref':'/entry_candles/15M/range_high','value':range_high,
                           'interpretation':'Frozen 15M range upper boundary'}]
            evidence += [
                {'ref':'/entry_candles/1M/last/close','value':last['close'],
                 'interpretation':'Closed one-minute structural trigger'},
                {'ref':'/entry_candles/1M/last/volume','value':last['volume'],
                 'interpretation':'Observed trigger candle activity'},
                {'ref':'/entry_candles/5M/last/close','value':five[-1]['close'],
                 'interpretation':'Observed five-minute directional structure'}]
            shadow_reason = None
            if setup == 'scalp_range_reversion_1m':
                shadow_reason = 'range_reversion_forward_validation'
            elif signal_quality == 'edge_observation':
                shadow_reason = 'edge_observation_forward_validation'
            plan={'version':VERSION,'instrument':package['instId'],'setup':setup,'action':
                  'BUY_LONG' if sign==1 else 'SELL_SHORT',
                  'entry_price':entry,'stop_loss_price':stop,'take_profit_price':target,
                  'horizon':'scalp','strategy_mode':mode,'entry_policy':descriptor(),
                  'quality_policy':{k:policy[k] for k in ('minimum_net_rr','taker_fee','maker_fee','slippage')},
                  'created_at':number(package['data_as_of']),'trigger_close_ms':last['close_ms'],
                  'valid_for_seconds':60,'net_rr':rr,'entry_timeframe':'1M',
                  'trigger_level':level,'entry_atr':a1,'chase_atr':.6,
                  'reentry_level':reclaim if is_range else None,
                  'signal_quality':signal_quality if is_range else None,
                  'shadow_only':bool(shadow_reason),'shadow_reason':shadow_reason,
                  'stop_basis':('range_boundary_reclaim_plus_volatility_buffer' if is_range
                                else 'two_closed_1m_extremes_with_atr_buffer'),
                  'target_basis':('observed_15m_opposite_range_boundary' if is_range
                                  else f'projected_{setup}_volatility_target'),
                  'target_observation':({'timeframe':'15M','field':'opposite_range_boundary',
                                         'price':target,'range_low':range_low,'range_high':range_high,
                                         'extrapolated':False,'derived':False} if is_range else
                                        {'extrapolated':True,
                                         'basis':'volatility_projection_not_observed_target'}),
                  'supporting_evidence':evidence,
                  'invalidation':{'price':stop,'timeframe':'1M',
                                  'condition':'One-minute structural stop breached'},
                  'order_authorized':False}
            from scripts.structure_targets import calibrate_stop,layers
            series={'1M':one,'5M':five,'15M':bias}
            calibrate_stop(plan,series,policy)
            plan['target_layers']=layers(plan,{'5M':five,'15M':bias},policy)
            from scripts.entry_quality import attach
            quality=attach(package,plan,policy)
            if quality['veto']:
                reject(side,quality['enforced_reasons'][0],setup=setup,entry_quality=quality)
            elif quality['reasons']:
                result['checks'].append({'setup':setup,'side':side,'status':'observed',
                    'reason':quality['reasons'][0],'entry_quality':quality})
            plan['id']=hashlib.sha256(json.dumps(plan,sort_keys=True,
                                  ensure_ascii=False,allow_nan=False).encode()).hexdigest()[:24]
            result['plans'].append(plan)
        for side,sign in [('long',1),('short',-1)]:
            entry=ask if sign==1 else bid
            previous_mid=(five[-2]['open']+five[-2]['close'])/2
            trend_ok=(sign*(short_mean-slow_mean)>0 and
                      sign*(five[-1]['close']-short_mean)>=0)
            turn_ok=(sign*(five[-2]['close']-five[-2]['open'])<0 and
                     sign*(five[-1]['close']-five[-1]['open'])>0 and
                     sign*(five[-1]['close']-previous_mid)>0)
            level=max(r['high'] for r in one[-7:-1]) if sign==1 else min(r['low'] for r in one[-7:-1])
            breakout=sign*(last['close']-level)>0 and sign*(prev['close']-level)<=0
            pullback_level=prev['high'] if sign==1 else prev['low']
            pullback=(sign*(prev['close']-prev['open'])<0 and
                      sign*(last['close']-pullback_level)>0)
            candle_ok=sign*(last['close']-last['open'])>0
            volume_ok=average_volume>0 and last['volume']>=average_volume*.8
            if not volume_ok:
                reject(side,'scalp_volume_insufficient');continue
            momentum=[]
            if trend_ok or turn_ok:
                if sign*(bias[-1]['close']-bias_mean)<-a5*.5:
                    reject(side,'scalp_15m_bias_opposed')
                elif not candle_ok or not (breakout or pullback):
                    reject(side,'scalp_1m_trigger_not_met')
                elif sign*(entry-last['close'])>a1*.6:
                    reject(side,'quote_moved_beyond_closed_trigger')
                else:
                    if breakout:momentum.append(('scalp_breakout_1m',level))
                    if pullback:momentum.append(('scalp_pullback_1m',pullback_level))
                    if turn_ok and not trend_ok:
                        momentum.append(('scalp_reversal_1m',level if breakout else pullback_level))
            else:reject(side,'scalp_5m_confirmation_not_met')
            for setup,trigger in momentum:
                if sign*(entry-trigger)<=0:
                    reject(side,'quote_moved_beyond_closed_trigger',setup=setup);continue
                stop=(min(last['low'],prev['low'])-a1*.2 if sign==1
                      else max(last['high'],prev['high'])+a1*.2)
                stop=min(stop,entry-a1*1.1) if sign==1 else max(stop,entry+a1*1.1)
                projection={
                    'scalp_breakout_1m':max(a1*5.5,a5*4.0),
                    'scalp_pullback_1m':max(a1*5.0,a5*3.5),
                    'scalp_reversal_1m':max(a1*4.5,a5*3.25),
                }[setup]
                add_plan(setup,side,sign,entry,trigger,stop,entry+sign*projection)
            # Independent range candidate: a completed 1M edge rejection near
            # the observed 15M boundary, still inside the half-channel at entry.
            # No volume, quote, risk or final-gateway check is bypassed.
            boundary=range_low if sign==1 else range_high
            target=range_high if sign==1 else range_low
            edge=last['low'] if sign==1 else last['high']
            prior_edge=prev['low'] if sign==1 else prev['high']
            prior_mid=(prev['open']+prev['close'])/2
            edge_test=(sign*(edge-boundary)<=range_buffer or
                       sign*(prior_edge-boundary)<=range_buffer)
            reentry=(sign*(last['close']-boundary)>0 and
                     sign*(last['close']-prior_mid)>0 and
                     sign*(last['close']-prev['close'])>0)
            in_half_channel=sign*(range_mid-last['close'])>0 and sign*(range_mid-entry)>0
            quote_close=(abs(entry-last['close'])<=a1*.6 and
                         sign*(entry-prior_mid)>0)
            confirmed=(range_market and candle_ok and edge_test and reentry and
                       in_half_channel and quote_close)
            # Preserve the previous edge observation when the stronger reclaim
            # isn't confirmed. It remains a lower-ranked *real* candidate, not
            # a fabricated trade, so simulation frequency is not silently cut.
            exploratory=(range_market or trend_pause) and candle_ok and (
                sign*(last['close']-prev['close'])>0 and
                sign*(edge-boundary)<=exploratory_buffer and sign*(entry-boundary)>0)
            if confirmed or exploratory:
                stop_pad=max(a1*.15,a5*.08)
                stop=(min(last['low'],prev['low'])-stop_pad if sign==1
                      else max(last['high'],prev['high'])+stop_pad)
                add_plan(('scalp_range_reversion_1m' if range_market else
                          'scalp_trend_pause_reclaim_1m'),side,sign,entry,boundary,stop,target,
                         reclaim=prior_mid if confirmed else None,
                         signal_quality='confirmed_edge_reclaim' if confirmed else 'edge_observation')
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        result['error']=str(exc)
    return result


def validate_quote(package, plan, current, policy=None):
    from scripts.entry_candidates import number, verified_bars
    current=number(current); sign=1 if plan['action']=='BUY_LONG' else -1
    bar=verified_bars(package,'1M')[-1]
    if sign*(current-plan['trigger_level'])<=0: raise ValueError('program_trigger_lost_during_inference')
    if (plan.get('setup')=='scalp_range_reversion_1m' and
        plan.get('reentry_level') is not None and
        (sign*(current-plan['reentry_level'])<=0 or
         sign*(current-(plan['target_observation']['range_low']+
                        plan['target_observation']['range_high'])/2)>=0)):
        raise ValueError('program_range_reclaim_lost_during_inference')
    if sign*(current-bar['close'])>plan['entry_atr']*plan['chase_atr']: raise ValueError('program_trigger_chase_limit_exceeded')
    if not (plan['stop_loss_price']<current<plan['take_profit_price'] if sign==1 else plan['take_profit_price']<current<plan['stop_loss_price']):
        raise ValueError('program_quote_outside_stop_target')
    from scripts.entry_quality import evaluate,enforced_reasons
    from scripts.risk_policy import Policy
    quality=evaluate(package,plan,policy or plan.get('quality_policy') or vars(Policy()),entry_price=current)
    reasons=enforced_reasons(quality)
    if reasons:raise ValueError(reasons[0])
    return plan
