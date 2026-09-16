"""Single lifecycle-ledger source for dashboard intraday settlement statistics."""
from __future__ import annotations
import math


def _number(value):
    try:
        value=float(value)
    except (TypeError,ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def today_lifecycle_stats(rows, date_prefix, reset_time='1970-01-01 00:00:00'):
    """Count closed lifecycle rows once; holdings and partial settlements are excluded."""
    closed=[]
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict) or row.get('status')!='closed':continue
        close_time=str(row.get('close_time') or '')
        if close_time[:10]!=str(date_prefix) or close_time<str(reset_time):continue
        closed.append(row)
    wins=sum(_number(r.get('net_pnl',r.get('pnl'))) > 0 for r in closed)
    losses=sum(_number(r.get('net_pnl',r.get('pnl'))) < 0 for r in closed)
    breakeven=len(closed)-wins-losses
    net=sum(_number(r.get('net_pnl',r.get('pnl'))) for r in closed)
    gross=sum(_number(r.get('gross_pnl')) for r in closed)
    fees=sum(_number(r.get('fee')) for r in closed)
    funding=sum(_number(r.get('funding_fee')) for r in closed)
    return {'closed_trades':len(closed),'win_trades':wins,'loss_trades':losses,'breakeven_trades':breakeven,
            'win_rate':round(wins/len(closed)*100,1) if closed else 0.0,
            'net_realized':round(net,8),'realized_gross':round(gross,8),'fees_paid':round(fees,8),
            'funding_paid':round(funding,8),'source':'lifecycle_ledger','settled_rows':closed}
