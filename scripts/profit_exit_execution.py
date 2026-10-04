"""One bounded passive normal-profit exit. Stops never enter this route."""
import time
import uuid
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from scripts import strategy_evidence as evidence
from scripts.position_lifecycle import identity

REASONS={'trailing_exit','time_exit'}
WAIT_SECONDS=2.


def attempt(env, position, reason, budget, *, request, sleep=time.sleep,quote_reader=None):
    result={'status':'market_allowed','order_ids':[],'reason':'passive_not_eligible'}
    if reason not in REASONS or not isinstance(budget,dict):return {**result,'reason':'exit_reason_requires_market'}
    bound=identity(position,env.identity)
    if bound is None or bound['side'] not in ('long','short') or budget.get('identity')!=bound:return {**result,'reason':'passive_identity_unverified'}
    if budget.get('opening_cost_basis')!='verified_opening_fills':return {**result,'reason':'opening_cost_not_verified'}
    try:
        entry=float(position['avgPx']);sign=1 if bound['side']=='long' else -1
        receipt=budget.get('opening_receipt') or {}
        if receipt.get('status')!='verified' or receipt.get('identity')!=bound:return {**result,'reason':'opening_receipt_missing'}
        if abs(float(receipt.get('contracts') or 0)-abs(float(position['pos'])))>1e-8:return {**result,'reason':'opening_quantity_changed'}
        if abs(float(receipt.get('opening_vwap') or 0)-entry)>max(1e-8,entry*1e-6):return {**result,'reason':'opening_vwap_changed'}
        tick=float(budget['tick']);cost=float(budget['total_cost_distance']);net=float(budget['minimum_net_profit_distance'])
        if not all(math.isfinite(value) and value>0 for value in (entry,tick,cost,net)):return result
        if quote_reader is None:
            from scripts.public_market import get_json,BASE_URL
            payload=get_json(BASE_URL+'/api/v5/market/ticker?instId='+bound['instId'],timeout=2)
            quotes=payload['data'] if payload.get('code')=='0' else []
        else:quotes=quote_reader()
        if len(quotes)!=1 or quotes[0].get('instId')!=bound['instId']:return {**result,'reason':'profit_quote_unavailable'}
        quote=quotes[0]
        bid=float(quote['bidPx']);ask=float(quote['askPx'])
        if not all(math.isfinite(value) and value>0 for value in (bid,ask)) or ask<bid:return result
        if not 0<=time.time()*1000-float(quote['ts'])<=5000:return {**result,'reason':'profit_quote_stale'}
        executable=float(quote['bidPx'] if sign==1 else quote['askPx'])
        armed=float(budget.get('protected_stop') or 0)
        if armed and (not math.isfinite(armed) or sign*(executable-armed)<=0):
            return {**result,'reason':'cloud_floor_crossed_market_only'}
        if all(key in budget for key in ('opening_cost','entry_taker_fee','entry_price','slippage_budget','spread_budget')):
            cost=float(budget['opening_cost'])+executable*float(budget['entry_taker_fee'])/float(budget['entry_price'])+float(budget['slippage_budget'])+float(budget['spread_budget'])
            if not math.isfinite(cost) or cost<=0:return result
        if sign*(executable-entry)<cost+net+tick*2:return {**result,'reason':'executable_net_profit_insufficient'}
        passive=float(quote['askPx'] if sign==1 else quote['bidPx'])
        step=Decimal(str(tick));rounded=(Decimal(str(passive))/step).to_integral_value(rounding=ROUND_CEILING if sign==1 else ROUND_FLOOR)*step
        if rounded<=0:return result
        quantity=Decimal(str(position['pos'])).copy_abs()
        if not quantity.is_finite() or quantity<=0:return result
    except Exception:return result
    client='okxqp'+uuid.uuid4().hex[:27]
    payload={'instId':bound['instId'],'tdMode':'cross','posSide':bound['side'],
             'side':'sell' if sign==1 else 'buy','ordType':'post_only',
             'px':format(rounded,'f'),'sz':format(quantity,'f'),'reduceOnly':True,'clOrdId':client}
    try:
        evidence.append(env.identity,'profit_exit_intent',{'identity':bound,'reason':reason,'client_id':client,'budget':budget,'payload':payload})
    except Exception:return {**result,'reason':'passive_journal_unavailable'}
    unknown={'status':'unknown','order_ids':[],'reason':'passive_outcome_unknown','client_id':client}
    try:
        try:
            ack=request('POST','/api/v5/trade/order',payload,env,timeout=2)
        except Exception as error:
            from okxquant_backend.okx_trade_service import OKXAPIError
            if isinstance(error,OKXAPIError) and str(error.code).isdigit() and str(error.code)!='0':
                return {**result,'reason':'passive_explicit_rejection','refresh_required':True}
            raise
        if not isinstance(ack,list) or len(ack)!=1:return unknown
        row=ack[0]
        if str(row.get('sCode','0'))!='0':return {**result,'reason':'passive_explicit_rejection','refresh_required':True}
        oid=str(row.get('ordId') or '')
        if not oid.isdigit():return unknown
        unknown['order_ids']=[oid]
        evidence.best_effort(env.identity,'profit_exit_execution',{'identity':bound,'client_id':client,'order_id':oid,'stage':'accepted'})
        sleep(WAIT_SECONDS)
        def receipt():
            rows=request('GET','/api/v5/trade/order',{'instId':bound['instId'],'ordId':oid},env,timeout=2)
            if not isinstance(rows,list) or len(rows)!=1:return None
            observed=rows[0]
            if observed.get('instId')!=bound['instId'] or str(observed.get('ordId'))!=oid:return None
            return observed
        status=receipt()
        if status is None:return unknown
        if status.get('state') not in {'filled','canceled','mmp_canceled'}:
            canceled=request('POST','/api/v5/trade/cancel-order',{'instId':bound['instId'],'ordId':oid},env,timeout=2)
            if not isinstance(canceled,list) or len(canceled)!=1 or str(canceled[0].get('sCode','0'))!='0':return unknown
            status=receipt()
        if status is None or status.get('state') not in {'filled','canceled','mmp_canceled'}:return unknown
        evidence.best_effort(env.identity,'profit_exit_execution',{'identity':bound,'client_id':client,'order_id':oid,'stage':status['state'],'accFillSz':status.get('accFillSz')})
        return {'status':'terminal','order_ids':[oid],'reason':'passive_terminal','state':status['state'],'client_id':client}
    except Exception:return unknown
