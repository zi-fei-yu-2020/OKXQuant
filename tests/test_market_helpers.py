"""Shared realistic public order-book fixture for final gateway tests."""
import time


def orderbook(price=100.0, *, ts_ms=None, size=100000.0):
    price=float(price);step=max(price*.0001, .00000001)
    ts=int(time.time()*1000 if ts_ms is None else ts_ms)
    return {'data':[{'ts':str(ts),
        'bids':[[format(price-step*(i+1),'.12g'),str(size),'0','1'] for i in range(50)],
        'asks':[[format(price+step*(i+1),'.12g'),str(size),'0','1'] for i in range(50)]}]}
