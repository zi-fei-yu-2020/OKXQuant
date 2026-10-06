#!/usr/bin/env python3
"""Archive actual entry fill quality and delayed post-fill markouts.

Sampling timestamps and delay are explicit. A late sample is never represented as
an exact 1s/5s markout.
"""
from __future__ import annotations
from pathlib import Path
import json
import math
import sqlite3
import time

ROOT=Path(__file__).resolve().parents[1]
VERSION='execution-quality-v1'
HORIZONS=(1,5,30,60,300)
HOT_INTERVAL_SECONDS=5
IDLE_INTERVAL_SECONDS=60
HOT_WINDOW_SECONDS=max(HORIZONS)+60


def _recent_time_sensitive_work(now):
    """Cheap local preflight; no exchange request and no database scan."""
    from scripts.strategy_evidence import execution_quality_marker
    try:
        age=float(now)-execution_quality_marker().stat().st_mtime
        return 0 <= age <= HOT_WINDOW_SECONDS
    except (OSError,TypeError,ValueError):
        # A missing marker must not permanently disable reconciliation; the
        # conservative idle poll still discovers fills once per minute.
        return False


def should_run(last_run,now=None):
    now=time.time() if now is None else float(now)
    elapsed=max(0.,now-float(last_run or 0))
    interval=HOT_INTERVAL_SECONDS if _recent_time_sensitive_work(now) else IDLE_INTERVAL_SECONDS
    return elapsed>=interval


def _number(value,positive=False):
    if isinstance(value,bool):raise ValueError('invalid execution number')
    result=float(value)
    if not math.isfinite(result) or (positive and result<=0):raise ValueError('invalid execution number')
    return result


def _submission_index(scope):
    from scripts.strategy_evidence import DB_PATH
    by_client={};by_order={}
    try:
        with sqlite3.connect(Path(DB_PATH).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5) as db:
            rows=db.execute("SELECT at,payload FROM events WHERE scope=? AND kind='entry_submission' ORDER BY at DESC LIMIT 4000",(scope,)).fetchall()
        for at,raw in rows:
            item=json.loads(raw);plan=item.get('plan') or {};client=str(item.get('client_id') or '')
            record={'submitted_at':at,'client_id':client,'plan':plan,'order_type':item.get('order_type')}
            if client:by_client.setdefault(client,record)
            response=item.get('response') or []
            if isinstance(response,dict):response=response.get('data') or [response]
            for row in response if isinstance(response,list) else []:
                if isinstance(row,dict) and row.get('ordId'):by_order.setdefault(str(row['ordId']),record)
    except (OSError,ValueError,TypeError,sqlite3.Error):pass
    return by_client,by_order


def receipt(scope, fill, submission):
    plan=submission['plan'];planned=_number(plan.get('entry'),True);actual=_number(fill.get('fillPx'),True)
    size=_number(fill.get('fillSz'),True);planned_size=_number(plan.get('size'),True)
    side=plan.get('side');sign=1 if side=='long' else -1 if side=='short' else 0
    if not sign:raise ValueError('unknown execution side')
    fill_ts=_number(fill.get('ts'),True)
    submitted_ms=_number(submission['submitted_at'],True)*1000
    exec_type=str(fill.get('execType') or '').upper()
    liquidity={'M':'maker','T':'taker'}.get(exec_type,'unknown')
    return {'version':VERSION,'scope':scope,'instrument':fill.get('instId'),'decision_id':plan.get('decision_id'),
            'candidate_id':plan.get('candidate_id'),'client_id':submission.get('client_id'),
            'order_id':str(fill.get('ordId') or ''),'trade_id':str(fill.get('tradeId') or ''),
            'bill_id':str(fill.get('billId') or ''),'side':side,'order_type':submission.get('order_type'),
            'planned_price':planned,'fill_price':actual,'fill_size':size,'planned_size':planned_size,
            'partial_fill_ratio':min(1.,size/planned_size),'fill_type':liquidity,'exchange_exec_type':exec_type or None,
            'fee':float(fill['fee']) if fill.get('fee') not in (None,'') else None,'fee_currency':fill.get('feeCcy'),
            'fill_ts_ms':int(fill_ts),'submitted_at_ms':int(submitted_ms),'ack_to_fill_latency_ms':max(0.,fill_ts-submitted_ms),
            'adverse_slippage_bps':sign*(actual-planned)/planned*10000,
            'entry_market_snapshot':plan.get('entry_market_snapshot'),
            'liquidity_admission':plan.get('liquidity_admission'),'cost_model':plan.get('cost_model')}


def record_receipts(env, fills):
    from scripts import strategy_evidence as evidence
    by_client,by_order=_submission_index(env.identity)
    candidates={}
    for fill in fills:
        source=by_client.get(str(fill.get('clOrdId') or '')) or by_order.get(str(fill.get('ordId') or ''))
        if not source:continue
        try:item=receipt(env.identity,fill,source)
        except (ValueError,TypeError,KeyError,OverflowError):continue
        identity='execution-receipt:'+env.identity+':'+(item['trade_id'] or item['bill_id'] or item['order_id']+':'+str(item['fill_ts_ms']))
        candidates.setdefault(identity,item)
    if not candidates:return 0
    identities=list(candidates)
    placeholders=','.join('?' for _ in identities)
    try:
        with evidence.connection() as db:
            existing={row[0] for row in db.execute(
                f"SELECT id FROM events WHERE scope=? AND kind='execution_receipt' AND id IN ({placeholders})",
                (env.identity,*identities)).fetchall()}
    except sqlite3.Error:
        existing=set()
    pending=[(identity,candidates[identity]) for identity in identities if identity not in existing]
    if pending:evidence.append_batch(env.identity,'execution_receipt',pending)
    return len(pending)


def _existing_markouts(scope):
    from scripts.strategy_evidence import DB_PATH
    result=set()
    try:
        with sqlite3.connect(Path(DB_PATH).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5) as db:
            rows=db.execute("SELECT payload FROM events WHERE scope=? AND kind='post_fill_markout'",(scope,)).fetchall()
        for (raw,) in rows:
            item=json.loads(raw);result.add((item.get('receipt_id'),int(item.get('horizon_seconds'))))
    except (OSError,ValueError,TypeError,sqlite3.Error):pass
    return result


def sample_markouts(env, *, getter=None, now_ms=None):
    from scripts import strategy_evidence as evidence, public_market
    getter=getter or public_market.get_json;now=time.time()*1000 if now_ms is None else now_ms
    existing=_existing_markouts(env.identity);events=evidence.export_events(env.identity,'execution_receipt')
    due=[]
    for event in events:
        payload=event['payload'];fill_ts=float(payload.get('fill_ts_ms') or 0)
        for horizon in HORIZONS:
            if now>=fill_ts+horizon*1000 and (event['id'],horizon) not in existing:
                due.append((event,horizon))
    prices={};written=0
    for event,horizon in due:
        payload=event['payload'];inst=payload.get('instrument')
        if inst not in prices:
            try:
                row=getter(f'https://www.okx.com/api/v5/market/ticker?instId={inst}',simulated=env.simulated)['data'][0]
                prices[inst]=(_number(row['last'],True),_number(row['ts'],True))
            except Exception as exc:prices[inst]=exc
        observed=prices[inst]
        if isinstance(observed,Exception):continue
        price,source_ts=observed;fill=_number(payload['fill_price'],True);sign=1 if payload.get('side')=='long' else -1
        item={'version':VERSION,'receipt_id':event['id'],'instrument':inst,'order_id':payload.get('order_id'),
              'trade_id':payload.get('trade_id'),'horizon_seconds':horizon,'fill_price':fill,
              'mark_price':price,'markout_bps':sign*(price-fill)/fill*10000,'fill_ts_ms':payload['fill_ts_ms'],
              'target_sample_at_ms':payload['fill_ts_ms']+horizon*1000,'sampled_at_ms':int(now),
              'source_ts_ms':int(source_ts),'sample_delay_ms':max(0,now-(payload['fill_ts_ms']+horizon*1000)),
              'sampling_precision':'observed_at_scheduler_run_not_exact_horizon'}
        evidence.append(env.identity,'post_fill_markout',item,f"markout:{event['id']}:{horizon}");written+=1
    return written


def run():
    from scripts.okx_runtime import selected_environment
    from okxquant_backend.okx_trade_service import _request
    env=selected_environment();fills=_request('GET','/api/v5/trade/fills',{'instType':'SWAP','limit':'100'},env)
    return {'version':VERSION,'receipts_recorded':record_receipts(env,fills),'markouts_recorded':sample_markouts(env)}

if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False))
