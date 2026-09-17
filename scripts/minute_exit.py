"""Live observed closed-bar ATR for minute positions; failure never fabricates volatility."""
import time
from urllib.parse import urlencode
from scripts.signal_data import closed_candles


def enrich_volatility(factor, inst_id, market):
    factor.pop('closed_1m',None)
    for bar,field in [('1m','atr_1m'),('5m','atr_5m')]:
        factor.pop(field,None)
        try:
            payload=market.get_json(market.BASE_URL+'/api/v5/market/candles?'+urlencode({'instId':inst_id,'bar':bar,'limit':20}),timeout=3)
            if payload.get('code')!='0': continue
            rows=list(reversed(closed_candles(payload['data'],bar,as_of_ms=int(time.time()*1000))))
            if len(rows)<15:continue
            if bar=='1m':
                factor['closed_1m']=[{'close_ms':int(r[0])+60000,'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),'close':float(r[4])} for r in rows]
            factor[field]=sum(max(float(b[2])-float(b[3]),abs(float(b[2])-float(a[4])),abs(float(b[3])-float(a[4])))
                              for a,b in zip(rows[-15:-1],rows[-14:]))/14
        except (OSError,RuntimeError,ValueError,KeyError,TypeError):
            factor.pop(field,None)
