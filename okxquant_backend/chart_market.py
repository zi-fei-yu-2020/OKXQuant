"""Read-only chart data, adapted from upstream TacticalChart's candle contract.

Uses the existing credential-free, cross-process public-market transport. Never
calls CLI/account/order APIs, fabricates bars, or changes the trading environment.
"""
from decimal import Decimal
import math
import time
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Path, Query, Response
from scripts import public_market

router = APIRouter(prefix='/api/v1/market', tags=['chart'])
BAR_MS = {'15m': 900000, '1H': 3600000, '4H': 14400000, '1D': 86400000}


def numeric(value, *, positive=False):
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise ValueError('Missing candle observation')
    number = float(value)
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        raise ValueError('Invalid candle observation')
    return number


def normalize_candles(raw, now_ms):
    if not isinstance(raw, list) or not raw or len(raw) > 300:
        raise ValueError('Candle history unavailable')
    bars, precision = {}, 2
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 9:
            raise ValueError('Incomplete candle row')
        ts = numeric(row[0], positive=True)
        if ts != int(ts) or ts > now_ms + 60000:
            raise ValueError('Invalid candle clock')
        o, h, low, c = [numeric(v, positive=True) for v in row[1:5]]
        volume, turnover = numeric(row[5]), numeric(row[7])
        if not low <= min(o, c) <= max(o, c) <= h or row[8] not in ('0', '1', 0, 1) or isinstance(row[8], bool):
            raise ValueError('Invalid candle geometry or close status')
        bar = {'ts': int(ts), 'open': o, 'high': h, 'low': low, 'close': c,
               'vol': volume, 'turnover': turnover, 'confirmed': str(row[8]) == '1'}
        if int(ts) in bars and bars[int(ts)] != bar:
            raise ValueError('Conflicting candle timestamps')
        bars[int(ts)] = bar
        for value in row[1:5]:
            precision = max(precision, min(10, max(0, -Decimal(str(value)).normalize().as_tuple().exponent)))
    return [bars[key] for key in sorted(bars)], precision


@router.get('/{inst_id}/candles')
def chart_candles(
    response: Response,
    inst_id: str = Path(pattern=r'^[A-Z0-9]{1,24}-USDT-SWAP$'),
    bar: Literal['15m', '1H', '4H', '1D'] = '1H',
    limit: int = Query(default=150, ge=10, le=300),
):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        # Public-market prices, not a live trading account. DEMO stays DEMO.
        payload = public_market.get_json(
            public_market.BASE_URL + '/api/v5/market/candles?' + urlencode({'instId': inst_id, 'bar': bar, 'limit': limit}),
            timeout=5, simulated=False,
        )
        now_ms = int(time.time()*1000)
        if not isinstance(payload, dict) or str(payload.get('code')) != '0':
            raise ValueError('Exchange candle request rejected')
        candles, precision = normalize_candles(payload.get('data'), now_ms)
        width = BAR_MS[bar]
        return {'instId': inst_id, 'bar': bar, 'candles': candles, 'price_precision': precision,
                'source': 'OKX public market', 'market_environment': 'public', 'read_only': True,
                'volume_unit': 'contracts', 'as_of_ms': now_ms, 'cache_max_age_seconds': 5,
                'stale': now_ms - candles[-1]['ts'] > width + 20000,
                'has_gaps': any(b['ts']-a['ts'] != width for a, b in zip(candles, candles[1:]))}
    except Exception as exc:
        # Browser may retain its last valid chart with an explicit delay badge.
        # Do not return synthetic OHLC or provider details/credentials as success.
        raise HTTPException(status_code=502, detail='K线行情暂不可用，请稍后重试', headers={'Cache-Control': 'no-store'}) from exc
