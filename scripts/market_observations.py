#!/usr/bin/env python3
"""Persist derivative observations with field-level provenance and freshness.

Read failures are represented as unavailable fields, never numeric zeroes. Derived
changes use only previously persisted observations from the same instrument.
"""
from __future__ import annotations
from pathlib import Path
import json
import math
import os
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
HISTORY=DATA/'market_observations.jsonl'
LATEST=DATA/'market_observations_latest.json'
VERSION='market-observations-v1'


def number(value):
    if isinstance(value,bool):raise ValueError('invalid numeric observation')
    result=float(value)
    if not math.isfinite(result):raise ValueError('invalid numeric observation')
    return result


def field(value, *, source, exchange_ts, received_at, required=True):
    ts=number(exchange_ts);received=number(received_at)
    if ts > received + 1000:raise ValueError('observation timestamp is in the future')
    age=max(0.,received-ts)
    return {'value':number(value),'source':source,'exchange_ts':int(ts),'received_at':int(received),
            'age_ms':age,'status':'fresh' if age<=15000 else 'stale',
            'required_for_strategy':bool(required),'fallback_used':False}


def unavailable(source, error_type, *, required=True):
    return {'value':None,'source':source,'exchange_ts':None,'received_at':int(time.time()*1000),
            'age_ms':None,'status':'unavailable','required_for_strategy':bool(required),
            'fallback_used':False,'error_type':str(error_type)}


def _read_history(limit=20000):
    try:
        lines=HISTORY.read_text(encoding='utf-8').splitlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]
    except (OSError,ValueError,TypeError):return []


def derive(current, history):
    at=current['observed_at_ms'];inst=current['instId']
    rows=[row for row in history if row.get('instId')==inst and row.get('observed_at_ms',0)<at]
    def previous(field_name, seconds):
        target=at-seconds*1000
        candidates=[row for row in rows if isinstance((row.get('fields') or {}).get(field_name),dict)
                    and (row['fields'][field_name]).get('value') is not None
                    and row.get('observed_at_ms',0)<=target]
        return max(candidates,key=lambda row:row['observed_at_ms']) if candidates else None
    result={}
    for source_name in ('open_interest_usd','funding_rate','long_short_ratio','taker_net_volume'):
        now=(current['fields'].get(source_name) or {}).get('value')
        for label,seconds in (('5m',300),('15m',900),('1h',3600),('4h',14400)):
            old=previous(source_name,seconds)
            old_value=((old or {}).get('fields',{}).get(source_name) or {}).get('value')
            key=f'{source_name}_delta_{label}'
            result[key]=None if now is None or old_value is None else now-old_value
    return result


def collect_one(item, *, getter=None, now_ms=None, history=None):
    from scripts import public_market
    getter=getter or public_market.get_json
    now=int(time.time()*1000 if now_ms is None else now_ms)
    inst=item['instId'];parts=inst.split('-');ccy=parts[0];index_inst='-'.join(parts[:2])
    fields={}
    reads=(
      ('ticker',f'https://www.okx.com/api/v5/market/ticker?instId={inst}'),
      ('mark',f'https://www.okx.com/api/v5/public/mark-price?instType=SWAP&instId={inst}'),
      ('index',f'https://www.okx.com/api/v5/market/index-tickers?instId={index_inst}'),
      ('funding',f'https://www.okx.com/api/v5/public/funding-rate?instId={inst}'),
      ('oi',f'https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst}'),
      ('ls',f'https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=5m'),
      ('taker',f'https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy={ccy}&instType=CONTRACTS&period=5m'),
    )
    payloads={}
    for name,url in reads:
        try:payloads[name]=getter(url)
        except Exception as exc:payloads[name]=exc
    try:
        row=payloads['ticker']['data'][0];ts=row['ts']
        fields['last_price']=field(row['last'],source='okx_public_ticker',exchange_ts=ts,received_at=now)
        fields['bid_price']=field(row['bidPx'],source='okx_public_ticker',exchange_ts=ts,received_at=now)
        fields['ask_price']=field(row['askPx'],source='okx_public_ticker',exchange_ts=ts,received_at=now)
    except Exception as exc:
        for name in ('last_price','bid_price','ask_price'):fields[name]=unavailable('okx_public_ticker',type(exc).__name__)
    try:
        mark_row=payloads['mark']['data'][0];index_row=payloads['index']['data'][0]
        mark=number(mark_row['markPx']);index=number(index_row['idxPx'])
        basis_ts=min(number(mark_row['ts']),number(index_row['ts']))
        fields['mark_price']=field(mark,source='okx_public_mark_price',exchange_ts=mark_row['ts'],received_at=now,required=False)
        fields['index_price']=field(index,source='okx_market_index_ticker',exchange_ts=index_row['ts'],received_at=now,required=False)
        fields['basis_bps']=field((mark-index)/index*10000,source='okx_mark_vs_index',exchange_ts=basis_ts,received_at=now,required=False)
    except Exception as exc:
        fields['mark_price']=unavailable('okx_public_mark_price',type(exc).__name__,required=False)
        fields['index_price']=unavailable('okx_market_index_ticker',type(exc).__name__,required=False)
        fields['basis_bps']=unavailable('okx_mark_vs_index',type(exc).__name__,required=False)
    try:
        row=payloads['funding']['data'][0];ts=row.get('ts') or now
        fields['funding_rate']=field(row['fundingRate'],source='okx_public_funding_rate',exchange_ts=ts,received_at=now)
    except Exception as exc:fields['funding_rate']=unavailable('okx_public_funding_rate',type(exc).__name__)
    try:
        row=payloads['oi']['data'][0];ts=row.get('ts') or now
        fields['open_interest_usd']=field(row['oiUsd'],source='okx_public_open_interest',exchange_ts=ts,received_at=now)
    except Exception as exc:fields['open_interest_usd']=unavailable('okx_public_open_interest',type(exc).__name__)
    try:
        row=payloads['ls']['data'][0]
        fields['long_short_ratio']=field(row[1],source='okx_rubik_long_short',exchange_ts=row[0],received_at=now,required=False)
    except Exception as exc:fields['long_short_ratio']=unavailable('okx_rubik_long_short',type(exc).__name__,required=False)
    try:
        row=payloads['taker']['data'][0]
        fields['taker_net_volume']=field(number(row[1])-number(row[2]),source='okx_rubik_taker_volume',exchange_ts=row[0],received_at=now,required=False)
    except Exception as exc:fields['taker_net_volume']=unavailable('okx_rubik_taker_volume',type(exc).__name__,required=False)
    record={'version':VERSION,'instId':inst,'observed_at_ms':now,'fields':fields}
    record['derivatives_history']=derive(record,history or [])
    required=[value for value in fields.values() if value.get('required_for_strategy')]
    record['quality']={'status':'fresh' if required and all(v['status']=='fresh' for v in required) else 'partial',
                       'required_fields':len(required),'fresh_required_fields':sum(v['status']=='fresh' for v in required)}
    return record


def _atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.observations-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:json.dump(value,handle,ensure_ascii=False,allow_nan=False)
        os.replace(tmp,path)
    finally:
        try:os.unlink(tmp)
        except OSError:pass


def collect():
    from scripts.instrument_pool import load_instruments
    history=_read_history();rows=[collect_one(item,history=history) for item in load_instruments() if item.get('type','crypto')=='crypto']
    DATA.mkdir(parents=True,exist_ok=True)
    with HISTORY.open('a',encoding='utf-8') as handle:
        for row in rows:handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
    # Bound local history without inventing continuity.
    lines=HISTORY.read_text(encoding='utf-8').splitlines()
    if len(lines)>20000:HISTORY.write_text('\n'.join(lines[-20000:])+'\n',encoding='utf-8')
    payload={'version':VERSION,'generated_at_ms':int(time.time()*1000),'items':rows}
    _atomic(LATEST,payload);return payload


def public_status(now_ms=None):
    try:payload=json.loads(LATEST.read_text(encoding='utf-8'))
    except (OSError,ValueError,TypeError):return {'status':'not_started','version':VERSION,'items':[]}
    now=time.time()*1000 if now_ms is None else now_ms
    age=max(0,now-float(payload.get('generated_at_ms') or 0))
    items=payload.get('items') if isinstance(payload.get('items'),list) else []
    return {**payload,'age_ms':age,'status':'fresh' if age<=180000 and items else 'stale'}

if __name__=='__main__':print(json.dumps(collect(),ensure_ascii=False))
