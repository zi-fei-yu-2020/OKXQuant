"""Exit attribution from complete lifecycle receipts, not PnL or missing tags.

Exact exchange links and scoped execution-log correlations are explicitly distinct.
"""
import math
import re


def number(value):
    try:n=float(value);return n if math.isfinite(n) else 0.
    except (TypeError,ValueError):return 0.


def fill_time(order):
    return number(order.get('fillTime')) or number(order.get('uTime'))


def closing_orders(history,orders):
    direction=history.get('direction') or history.get('posSide')
    side='sell' if direction=='long' else 'buy' if direction=='short' else ''
    end=number(history.get('uTime'));start=number(history.get('cTime'))
    if not end or not side:return []
    # Use the whole lifecycle for partial exits; retain narrow matching only when
    # old fixtures/receipts genuinely lack an opening timestamp.
    lower=start if 0<start<=end else end-5000
    matches={}
    for order in orders:
        if not isinstance(order,dict) or not order.get('ordId'):continue
        if order.get('instId')!=history.get('instId') or order.get('side')!=side or order.get('posSide') not in (direction,'net'):continue
        if order.get('state') not in ('filled','canceled','mmp_canceled') or number(order.get('accFillSz'))<=0:continue
        if not lower<=fill_time(order)<=end+5000:continue
        identity=str(order['ordId']);old=matches.get(identity)
        score=lambda row:(number(row.get('uTime')),number(row.get('accFillSz')),bool(row.get('algoId')),bool(row.get('clOrdId')),bool(row.get('source')))
        if old is None or score(order)>score(old):matches[identity]=order
    return sorted(matches.values(),key=fill_time)


def order_source(order,history,orders,algos,executions,scope):
    result={'ordId':str(order['ordId']),'filled_size':number(order.get('accFillSz')),'source':'unknown',
            'label':'来源证据不足','tier':'unknown','evidence':'no_source_metadata_or_execution_link'}
    if order.get('category') in ('full_liquidation','partial_liquidation','adl'):
        return {**result,'source':'exchange_risk','label':'交易所强制结算','tier':'verified','evidence':'exchange_category'}
    if re.fullmatch(r'okxquantclose\d{10,13}',str(order.get('clOrdId') or '')):
        return {**result,'source':'manual_admin','label':'手动平仓（后台操作）','tier':'verified','evidence':'reserved_admin_client_id'}
    linked=[]
    for algo in algos:
        if not isinstance(algo,dict):continue
        if algo.get('state')!='effective' or algo.get('instId')!=order.get('instId') or algo.get('side')!=order.get('side'):continue
        if algo.get('posSide') not in (order.get('posSide'),'net'):continue
        ids=algo.get('ordIdList') or []
        ids=[str(x) for x in ids] if isinstance(ids,list) else []
        ids.append(str(algo.get('ordId') or ''))
        direct=str(order['ordId']) in ids
        same_algo=bool(order.get('algoId')) and str(order['algoId'])==str(algo.get('algoId'))
        if direct or same_algo:linked.append(algo)
    actual={a.get('actualSide') for a in linked}
    if linked and actual in ({'sl'},{'tp'}):
        side=next(iter(actual));label='云端止损触发' if side=='sl' else '云端止盈触发'
        return {**result,'source':'exchange_algo','label':label,'tier':'verified','evidence':'exchange_algo_order_link',
                'trigger_side':side,'algo_ids':sorted({str(a['algoId']) for a in linked})}
    if linked or order.get('algoId') or str(order.get('source'))=='7':
        return {**result,'source':'exchange_algo','label':'云端条件单触发平仓','tier':'verified','evidence':'exchange_algo_source'}
    from scripts.close_evidence import LABELS
    def response_ids(event):
        ids=event.get('response_order_ids',[])
        return ids if isinstance(ids,list) and all(isinstance(x,str) for x in ids) else []
    direct=[e for e in executions if isinstance(e,dict) and scope and e.get('scope')==scope and e.get('status') in ('accepted','confirmed','unconfirmed','flat_observed')
            and e.get('instId')==order.get('instId') and (not e.get('posSide') or e['posSide']==order.get('posSide'))
            and str(order['ordId']) in response_ids(e)]
    if direct:
        codes={e.get('reason_code','strategy_close') for e in direct}
        code=next(iter(codes)) if len(codes)==1 else 'strategy_close'
        return {**result,'source':'strategy','label':LABELS.get(code,LABELS['strategy_close']),'tier':'verified',
                'reason_code':code,'evidence':'exchange_order_id_in_close_receipt'}
    candidates=[]
    if scope:
        for event in executions:
            if not isinstance(event,dict):continue
            if event.get('scope')!=scope or event.get('status')!='confirmed' or event.get('instId')!=order.get('instId'):continue
            if event.get('posSide') and event['posSide']!=order.get('posSide'):continue
            start=number(event.get('started_at'))*1000;end=number(event.get('confirmed_at'))*1000
            if not start<=fill_time(order)<=end+2000 or not 0<=end-start<=900000:continue
            if event.get('position_id') and history.get('posId') and str(event['position_id'])!=str(history['posId']):continue
            if event.get('position_created_at') and history.get('cTime') and abs(number(event['position_created_at'])-number(history['cTime']))>1000:continue
            if event.get('size') and not math.isclose(number(event['size']),number(order.get('accFillSz')),rel_tol=.001,abs_tol=1e-8):continue
            competing=[o for o in orders if start<=fill_time(o)<=end+2000]
            if len(competing)!=1:continue  # A temporal coincidence is not enough to choose among multiple orders.
            candidates.append(event)
    codes={e.get('reason_code','strategy_close') for e in candidates}
    if len(codes)==1:
        code=next(iter(codes));label=LABELS.get(code,LABELS['strategy_close'])
        if any(str(order['ordId']) in response_ids(e) for e in candidates):
            return {**result,'source':'strategy','label':label,'tier':'verified','reason_code':code,'evidence':'exchange_order_id_in_close_receipt'}
        return {**result,'source':'strategy_correlated','label':label+'（执行记录关联）','tier':'corroborated',
                'reason_code':code,'evidence':'scoped_execution_and_unique_fill','execution_ids':[e.get('id') or e.get('evidence_kind') for e in candidates]}
    return result


def reason(history,orders,*,algos=None,executions=None,scope=None):
    matches=closing_orders(history,orders)
    result={'exit_reason':'已平仓（来源证据不足）','exit_source':'unknown','exit_evidence':'insufficient_order_evidence',
            'close_order_ids':[str(o['ordId']) for o in matches],'attribution_status':'unknown',
            'attribution_note':'未取得完整的来源证据；不据此推断为外部人工操作'}
    if not matches:return result
    parts=[order_source(o,history,matches,algos or [],executions or [],scope) for o in matches]
    result['close_order_sources']=parts
    size=number(history.get('closeTotalPos'));matched=sum(number(o.get('accFillSz')) for o in matches)
    full=size>0 and abs(matched-size)<=max(1e-8,size*.001)
    result['exit_evidence']='matched_lifecycle_closing_orders'
    if not full:
        result.update(exit_reason='分批平仓（成交证据未齐）',exit_source='partial',attribution_status='partial',
                      attribution_note=f'已匹配{matched:g}/{size:g}张；未匹配部分不推测来源')
        return result
    labels=list(dict.fromkeys(p['label'] for p in parts));sources={p['source'] for p in parts}
    tiers={p['tier'] for p in parts}
    if sources=={'unknown'}:
        result['attribution_note']='成交已确认，但订单未带来源标签，也无唯一的执行记录关联'
    elif len(labels)==1:
        result.update(exit_reason=labels[0],exit_source=parts[0]['source'],attribution_status=parts[0]['tier'])
    else:
        result.update(exit_reason='分批平仓：'+' / '.join(labels),exit_source='mixed',
                      attribution_status='partial' if 'unknown' in tiers else 'corroborated' if 'corroborated' in tiers else 'mixed')
    if result['attribution_status'] in ('verified','mixed'):result['attribution_note']='来源由交易所订单/算法订单标识直接关联，不按盈亏推断'
    elif result['attribution_status']=='corroborated':result['attribution_note']='包含账户隔离的策略执行记录与唯一成交交叉关联；不是交易所直接给出的来源标签'
    return result
