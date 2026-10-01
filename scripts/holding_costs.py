"""Read-only, account/lifecycle-bound opening fills for live exits."""
import hashlib
import json
import sqlite3
import math
from pathlib import Path
from scripts import strategy_evidence as evidence
from scripts.position_lifecycle import identity


def verified_payload(raw, digest):
    if hashlib.sha256(raw.encode()).hexdigest()!=digest:return None
    return json.loads(raw)


def reconcile(position, tracker, scope, contract_value):
    bound=identity(position,scope)
    unavailable={'status':'unavailable','reason':'opening_fills_not_reconciled','identity':bound,
                 'contracts':abs(float(position.get('pos') or 0))}
    if bound is None or tracker.get('positionIdentity')!=bound or not tracker.get('decision_id'):return unavailable
    try:
        size=abs(float(position['pos']));ct=float(contract_value);entry=float(position['avgPx'])
        if not all(math.isfinite(value) and value>0 for value in (size,ct,entry)):return unavailable
        with sqlite3.connect(Path(evidence.DB_PATH).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5) as db:
            orders=set();seen={};started=float(position['cTime'])/1000
            for raw,digest in db.execute("SELECT payload,digest FROM events WHERE scope=? AND kind='entry_submission' AND at>=? AND json_extract(payload,'$.plan.decision_id')=? AND json_extract(payload,'$.plan.instId')=? ORDER BY at DESC LIMIT 100",(scope,started-300,tracker['decision_id'],bound['instId'])):
                event=verified_payload(raw,digest)
                if not event:continue
                plan=event.get('plan') or {}
                if plan.get('instId')!=bound['instId'] or plan.get('side')!=bound['side'] or plan.get('decision_id')!=tracker.get('decision_id'):continue
                response=event.get('response') or []
                if isinstance(response,dict):response=response.get('data') or [response]
                if not isinstance(response,list):continue
                for row in response:
                    if isinstance(row,dict) and row.get('ordId') and str(row.get('sCode','0'))=='0':orders.add(str(row['ordId']))
            if not orders:return unavailable
            placeholders=','.join('?' for _ in orders)
            query="SELECT payload,digest FROM events WHERE scope=? AND kind='fill' AND at>=? AND json_extract(payload,'$.instId')=? AND json_extract(payload,'$.ordId') IN ("+placeholders+") ORDER BY at DESC LIMIT 20000"
            for raw,digest in db.execute(query,(scope,started-300,bound['instId'],*sorted(orders))):
                row=verified_payload(raw,digest)
                if not row or str(row.get('ordId')) not in orders or row.get('instId')!=bound['instId']:continue
                if row.get('posSide')!=bound['side'] or row.get('side')!=('buy' if bound['side']=='long' else 'sell'):continue
                if not row.get('tradeId') or float(row.get('fillTime') or 0)<float(bound['cTime'])-2000:continue
                if row.get('feeCcy')!='USDT':return unavailable
                key=(str(row['ordId']),str(row['tradeId']))
                values=tuple(float(row[field]) for field in ('fillSz','fillPx','fee'))
                if key in seen and seen[key]!=values:return unavailable
                seen[key]=values
        fills=list(seen.values());quantity=sum(fill[0] for fill in fills)
        if not fills or not all(math.isfinite(value) for row in fills for value in row):return unavailable
        if any(fill[0]<=0 or fill[1]<=0 for fill in fills) or abs(quantity-size)>max(1e-8,size*1e-7):return unavailable
        vwap=sum(fill_size*fill_price for fill_size,fill_price,_ in fills)/quantity
        if abs(vwap-entry)>max(1e-8,entry*1e-6):return unavailable
        return {'status':'verified','identity':bound,'decision_id':tracker['decision_id'],
                'contracts':quantity,'base_units':quantity*ct,'opening_vwap':vwap,
                'opening_fee':sum(fill[2] for fill in fills),'opening_order_ids':sorted(orders),
                'opening_trade_ids':sorted(key[1] for key in seen)}
    except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,OverflowError,AttributeError):return unavailable


def refresh(position, tracker, scope, contract_value, now):
    previous=tracker.get('openingCostReceipt') or {}
    bound=identity(position,scope)
    matching=bound is not None and tracker.get('positionIdentity')==bound and previous.get('identity')==bound and previous.get('contracts')==abs(float(position.get('pos') or 0))
    if previous.get('status')=='verified':
        entry=float(position['avgPx'])
        matching=matching and previous.get('decision_id')==tracker.get('decision_id') and abs(float(previous.get('opening_vwap') or 0)-entry)<=max(1e-8,entry*1e-6)
        matching=matching and abs(float(previous.get('base_units') or 0)-float(previous.get('contracts') or 0)*float(contract_value))<=1e-8
    age=now-float(tracker.get('openingCostCheckedAt') or 0)
    if matching and 0<=age<30:return previous
    receipt=reconcile(position,tracker,scope,contract_value)
    tracker['openingCostCheckedAt']=now
    tracker['openingCostReceipt']=receipt
    return receipt
