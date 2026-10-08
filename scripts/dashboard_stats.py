"""Single lifecycle-ledger source for dashboard intraday settlement statistics."""
from __future__ import annotations
import math
from datetime import date, datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


def _finite(value):
    if isinstance(value,bool): return None
    try: value=float(value)
    except (TypeError,ValueError,OverflowError): return None
    return value if math.isfinite(value) else None


def normalize_timestamp(value):
    """Parse ledger dates as Asia/Shanghai, honoring explicit UTC/offset timestamps."""
    if value is None or isinstance(value, bool): return None
    try:
        if isinstance(value, (int, float)):
            stamp=float(value)
            return datetime.fromtimestamp(stamp/(1000 if stamp > 10**11 else 1), SHANGHAI)
        text=str(value).strip()
        if not text: return None
        parsed=datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def canonical_rows(rows, scope=None):
    """Deduplicate lifecycle IDs/corrections and optionally enforce account ownership.

    With an explicit scope, unowned and conflicting rows are excluded. Without one,
    preserve legacy caller behavior while still applying exact-ID correction handling.
    """
    selected=[]
    for row in rows if isinstance(rows,(list,tuple)) else []:
        if not isinstance(row,dict): continue
        markers=[row.get(key) for key in ("environment_id","account_source_id") if row.get(key)]
        if scope is not None and (not markers or not all(isinstance(v,str) and v==scope for v in markers)):
            continue
        selected.append(row)
    by_id={}; anonymous=[]; superseded=set()
    for row in selected:
        identity=row.get("id")
        if isinstance(identity,str) and identity: by_id[identity]=row
        else: anonymous.append(row)
        superseded.update(value for value in (row.get("superseded_ledger_ids") or []) if isinstance(value,str))
    return [row for identity,row in by_id.items() if identity not in superseded]+anonymous


def today_lifecycle_stats(rows, date_prefix, reset_time=None, *, as_of=None):
    """Calendar-day close stats; reset_time is an optional legacy explicit cutoff.

    Outcomes require a valid Shanghai close timestamp and finite net PnL. Amount totals
    expose observed sums separately and are null when any confirmed-close amount is unknown.
    """
    target_text=str(date_prefix)
    try:
        target_date=date.fromisoformat(target_text[:10])
    except (TypeError,ValueError):
        parsed_target=normalize_timestamp(date_prefix)
        if parsed_target is None: raise ValueError("date_prefix must identify a calendar date")
        target_date=parsed_target.date()
    as_of_dt=normalize_timestamp(as_of) if as_of is not None else datetime.now(SHANGHAI)
    if as_of_dt is None: raise ValueError("as_of must be a valid timestamp")
    reset_dt=normalize_timestamp(reset_time) if reset_time is not None else None
    if reset_time is not None and reset_dt is None: raise ValueError("reset_time must be a valid timestamp")

    closed=[]; unknown_close_time_rows=0
    scoped=canonical_rows(rows)
    for row in scoped:
        if row.get("status")!="closed": continue
        close_dt=normalize_timestamp(row.get("close_time"))
        if close_dt is None:
            unknown_close_time_rows+=1
            continue
        if close_dt.date()!=target_date or close_dt>as_of_dt: continue
        if reset_dt is not None and close_dt<reset_dt: continue
        closed.append(row)
    outcome_rows=[r for r in closed if _finite(r.get("net_pnl",r.get("pnl"))) is not None]
    wins=sum(_finite(r.get("net_pnl",r.get("pnl")))>0 for r in outcome_rows)
    losses=sum(_finite(r.get("net_pnl",r.get("pnl")))<0 for r in outcome_rows)
    breakeven=len(outcome_rows)-wins-losses
    fields=(
        ("net_realized",lambda r:_finite(r.get("net_pnl",r.get("pnl")))),
        ("realized_gross",lambda r:_finite(r.get("gross_pnl"))),
        ("fees_paid",lambda r:_finite(r.get("fee"))),
        ("funding_paid",lambda r:_finite(r.get("funding_fee"))),
    )
    observed={}; completeness={}; totals={}
    for name,getter in fields:
        values=[getter(row) for row in closed]
        known=[value for value in values if value is not None]
        value=round(sum(known),8)
        complete=len(known)==len(values)
        observed[name]=value; completeness[name]=complete
        totals[name]=value if complete else None
    return {'closed_trades':len(closed),'win_trades':wins,'loss_trades':losses,'breakeven_trades':breakeven,
            'outcome_trades':len(outcome_rows),'unsettled_amount_rows':len(closed)-len(outcome_rows),
            'unknown_close_time_rows':unknown_close_time_rows,
            'net_complete':completeness['net_realized'],'gross_complete':completeness['realized_gross'],
            'fees_complete':completeness['fees_paid'],'funding_complete':completeness['funding_paid'],
            'completeness':completeness,'observed':observed,
            'win_rate':round(wins/len(outcome_rows)*100,1) if outcome_rows else 0.0,
            'net_realized':totals['net_realized'],'realized_gross':totals['realized_gross'],
            'fees_paid':totals['fees_paid'],'funding_paid':totals['funding_paid'],
            'source':'lifecycle_ledger','settled_rows':closed,
            'timezone':'Asia/Shanghai','as_of':as_of_dt.isoformat(timespec='seconds')}


def scoped_rows(rows, scope):
    """Never adopt unowned legacy rows or rows with invalid/conflicting scope markers."""
    if not isinstance(scope,str) or not scope.strip(): return []
    return canonical_rows(rows,scope=scope)


def page_trades(rows, scope, page=1, page_size=50):
    """Read-only, account-scoped pagination over the supplied full canonical ledger."""
    if not isinstance(scope,str) or not scope.strip():
        raise ValueError("scope must be a non-empty account/environment identifier")
    try: page=int(page)
    except (TypeError,ValueError): raise ValueError("page must be a positive integer")
    try: page_size=int(page_size)
    except (TypeError,ValueError): raise ValueError("page_size must be between 1 and 200")
    if page < 1: raise ValueError("page must be a positive integer")
    if not 1 <= page_size <= 200: raise ValueError("page_size must be between 1 and 200")
    full=scoped_rows(rows,scope)
    full.sort(key=lambda row:(str(row.get('close_time') or row.get('open_time') or ''),str(row.get('id') or '')),reverse=True)
    total=len(full); start=(page-1)*page_size
    return {'trades':full[start:start+page_size],'page':page,'page_size':page_size,
            'total':total,'total_pages':(total+page_size-1)//page_size}
