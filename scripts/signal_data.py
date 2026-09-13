"""Closed-bar decision contract; intrabar market/risk readers remain unchanged."""
import math
import re
import time

class SignalDataError(ValueError): pass

def bar_seconds(bar):
    match=re.fullmatch(r'(\d+)(m|H|D)(?:utc)?',bar)
    if not match: raise SignalDataError('Unsupported signal interval')
    return int(match[1])*{'m':60,'H':3600,'D':86400}[match[2]]

def closed_candles(rows, bar, *, as_of_ms=None, limit=None, require_fresh=True):
    as_of_ms=int(time.time()*1000) if as_of_ms is None else int(as_of_ms)
    width=bar_seconds(bar)*1000
    out=[]; seen=set()
    for row in rows:
        if not isinstance(row,(list,tuple)) or len(row)<9: raise SignalDataError('Candle confirmation field missing')
        if str(row[8])!='1': continue
        ts=int(row[0])
        if ts+width>as_of_ms: continue
        values=[float(row[i]) for i in (1,2,3,4,5)]
        if not all(math.isfinite(v) for v in values) or min(values[:4])<=0 or values[4]<0:
            raise SignalDataError('Invalid closed candle')
        o,h,l,c,_=values
        if not l<=min(o,c)<=max(o,c)<=h: raise SignalDataError('Invalid candle geometry')
        if ts in seen: raise SignalDataError('Duplicate closed candle')
        seen.add(ts);out.append(list(row))
    out.sort(key=lambda r:int(r[0]),reverse=True)
    if limit: out=out[:limit]
    if not out: raise SignalDataError('No closed candles')
    # One-bar tolerance accommodates exchange publication at a boundary; not arbitrary stale data.
    if require_fresh and as_of_ms-(int(out[0][0])+width)>width:
        raise SignalDataError('Closed candle feed is stale')
    if any(int(a[0])-int(b[0])!=width for a,b in zip(out,out[1:])):
        raise SignalDataError('Closed candle sequence has gaps')
    return out
