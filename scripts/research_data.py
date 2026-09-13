#!/usr/bin/env python3
"""Bounded, resumable public-data capture. No credentials or trading endpoints.

Research output is not production signal data. Only confirmed candles are kept;
missing timestamps fail the capture rather than being interpolated.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import urllib.parse
import urllib.request

ALLOWED = {'/api/v5/market/history-candles', '/api/v5/public/funding-rate-history', '/api/v5/public/instruments'}
WIDTH = 300_000
DEFAULT_SYMBOLS = ['BTC-USDT-SWAP','ETH-USDT-SWAP','SOL-USDT-SWAP','DOGE-USDT-SWAP','SUI-USDT-SWAP']


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


class PublicCapture:
    def __init__(self, directory, *, interval=.6):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.pages=self.directory/'pages';self.pages.mkdir(exist_ok=True)
        self.interval=interval;self.last=0;self.requests=0

    def read(self,path,params):
        if path not in ALLOWED:raise ValueError('Only allowlisted public GET endpoints')
        key=digest([path,params]);cache=self.pages/(key+'.json')
        if cache.exists():return json.loads(cache.read_text(encoding='utf8'))
        time.sleep(max(0,self.interval-(time.monotonic()-self.last)))
        url='https://www.okx.com'+path+'?'+urllib.parse.urlencode(params)
        req=urllib.request.Request(url,headers={'User-Agent':'OKXQuant-Public-Research/1'},method='GET')
        self.last=time.monotonic();self.requests+=1
        with urllib.request.urlopen(req,timeout=20) as response:payload=json.load(response)
        if str(payload.get('code'))!='0' or not isinstance(payload.get('data'),list):raise ValueError('Public source unavailable; no synthetic fallback')
        cache.write_text(json.dumps(payload['data'],allow_nan=False),encoding='utf8')
        return payload['data']

    def candles(self,inst,start,end):
        result={};cursor=end
        for page in range(120):
            rows=self.read('/api/v5/market/history-candles',{'instId':inst,'bar':'5m','limit':'100','after':str(cursor)})
            if not rows:break
            times=[int(r[0]) for r in rows]
            if min(times)>=cursor:raise ValueError('Candle cursor did not advance')
            for r in rows:
                if len(r)<9 or str(r[8])!='1':continue
                ts=int(r[0]);values=[float(r[i]) for i in range(1,6)]
                if not all(math.isfinite(v) for v in values):raise ValueError('Invalid OHLCV')
                o,h,l,c,v=values
                if not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('Invalid OHLCV geometry')
                if start<=ts and ts+WIDTH<=end:
                    row={'symbol':inst,'timestamp':datetime.fromtimestamp((ts+WIDTH)/1000,timezone.utc).isoformat(),
                         'ts_ms':ts+WIDTH,'open':o,'high':h,'low':l,'close':c,'volume':v,'confirm':True,'source':'okx_public_history'}
                    if ts in result and result[ts]!=row:raise ValueError('Conflicting candle revisions')
                    result[ts]=row
            cursor=min(times)
            if page%20==0:print(f'{inst} candle pages={page+1} rows={len(result)}',flush=True)
            if cursor<=start:break
        expected=list(range(start,end,WIDTH))
        if sorted(result)!=expected:raise ValueError(f'{inst}: incomplete candle window {len(result)}/{len(expected)}')
        return [result[t] for t in expected]

    def funding(self,inst,start,end):
        result={};cursor=end+1;covered=False
        for _ in range(12):
            rows=self.read('/api/v5/public/funding-rate-history',{'instId':inst,'after':str(cursor),'limit':'400'})
            if not rows:break
            earliest=min(int(r['fundingTime']) for r in rows)
            if earliest>=cursor:raise ValueError('Funding cursor did not advance')
            for row in rows:
                at=int(row['fundingTime'])
                if start<at<=end:
                    value=row.get('realizedRate') if row.get('realizedRate') not in (None,'') else row.get('fundingRate')
                    rate=float(value)
                    if not math.isfinite(rate):raise ValueError('Funding rate unavailable')
                    if at in result and result[at]!=rate:raise ValueError('Conflicting funding history')
                    result[at]=rate
            if earliest<=start:covered=True;break
            cursor=earliest
        return result,covered

    def capture(self,symbols,start,end,*,evaluation_start):
        if not start<evaluation_start<end or start%WIDTH or end%WIDTH:raise ValueError('Invalid aligned capture window')
        if end>int(time.time()*1000):raise ValueError('Cannot capture future outcomes')
        metadata={r['instId']:r for r in self.read('/api/v5/public/instruments',{'instType':'SWAP'})}
        report={'schema':1,'bar':'5m','start_ms':start,'end_ms':end,'evaluation_start_ms':evaluation_start,
                'source':'OKX public history; not historical demo fills','series':{},'metadata':{},'funding_complete':True,
                'limitations':['Current contract metadata is not historically versioned','OHLC cannot establish queue priority or actual fills'],
                'executed_orders':0}
        for inst in symbols:
            if inst not in metadata:raise ValueError('Current contract metadata absent')
            cache=self.directory/(inst+'.json')
            if cache.exists():
                cached=json.loads(cache.read_text(encoding='utf8'))
                if cached.get('start_ms')!=start or cached.get('end_ms')!=end or digest(cached['rows'])!=cached.get('hash'):raise ValueError('Capture cache mismatch')
                rows=cached['rows'];complete=cached['funding_complete']
            else:
                rows=self.candles(inst,start,end);funding,complete=self.funding(inst,start,end)
                if complete:
                    # Funding is an event stream: zero only for bars without a settlement event.
                    if any(at%WIDTH for at in funding):raise ValueError('Funding event not on a replay boundary')
                    for r in rows:r['funding_rate']=funding.get(r['ts_ms'],0.)
                cached={'start_ms':start,'end_ms':end,'rows':rows,'funding_complete':complete,'hash':digest(rows)}
                cache.write_text(json.dumps(cached,allow_nan=False),encoding='utf8')
            report['series'][inst]=rows;report['metadata'][inst]=metadata[inst]
            report['funding_complete'] &= complete
            print(f'{inst} ready bars={len(rows)} funding_complete={complete}',flush=True)
        report['capture_hash']=digest({k:report[k] for k in ('series','metadata','start_ms','end_ms')})
        (self.directory/'dataset.json').write_text(json.dumps(report,allow_nan=False),encoding='utf8')
        return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--end',required=True)
    p.add_argument('--days',type=int,default=30);p.add_argument('--warmup-days',type=int,default=4);p.add_argument('--allow-public-network',action='store_true')
    args=p.parse_args()
    if not args.allow_public_network:raise SystemExit('Explicit --allow-public-network required; no trading writes are possible')
    if not 5<=args.days<=30 or not 4<=args.warmup_days<=7:raise SystemExit('Capture bounds exceeded')
    end=int(datetime.fromisoformat(args.end.replace('Z','+00:00')).timestamp()*1000)
    evaluation=end-args.days*86400000;start=evaluation-args.warmup_days*86400000
    result=PublicCapture(args.output).capture(DEFAULT_SYMBOLS,start,end,evaluation_start=evaluation)
    print(json.dumps({'complete':True,'hash':result['capture_hash'],'funding_complete':result['funding_complete'],'executed_orders':0}))

if __name__=='__main__':main()
