"""Deterministic strategy telemetry derived from the lifecycle ledger."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
PATH=DATA/"horizon_stats.json"

from datetime import datetime, timezone, timedelta
import math

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
SCHEMA_VERSION = 2


def _finite(value):
    if value is None or isinstance(value, bool): return None
    try: value = float(value)
    except (TypeError, ValueError): return None
    return value if math.isfinite(value) else None


def _dt(value):
    from scripts.dashboard_stats import normalize_timestamp
    return normalize_timestamp(value)


def _scope_rows(rows, scope):
    """Exclude unowned and conflicting rows; both present markers must match."""
    result=[]
    if not isinstance(scope, str) or not scope: return result
    for row in rows if isinstance(rows, (list, tuple)) else []:
        if not isinstance(row, dict): continue
        markers=[row.get(k) for k in ("environment_id", "account_source_id") if row.get(k)]
        if markers and all(isinstance(x, str) and x == scope for x in markers):
            result.append(row)
    return result


def canonical_rows(rows, scope=None):
    """Shared lifecycle canonicalizer for stats and trade projections."""
    from scripts.dashboard_stats import canonical_rows as canonicalize
    return canonicalize(rows,scope=scope)


def _canonical_rows(rows, scope):
    # Period statistics fail closed when scope is absent.
    if not isinstance(scope,str) or not scope: return []
    return canonical_rows(rows,scope=scope)


def strategy_periods(rows, *, scope, now=None):
    """Account-scoped today/all-time lifecycle statistics (Shanghai calendar day).

    "all" describes retained, verifiable rows in this ledger only, never exchange lifetime.
    """
    now_dt=_dt(now) if now is not None else datetime.now(SHANGHAI)
    if now_dt is None: raise ValueError("now must be a valid datetime/timestamp")
    as_of=now_dt.isoformat(timespec="seconds")
    midnight=now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    from scripts.statistics_epoch import filter_rows
    ledger=_canonical_rows(filter_rows(rows,scope=scope,keep_active=False), scope)

    def summarize(start, end, is_today):
        groups={h:{"closed":0,"wins":0,"losses":0,"breakeven":0,"net_pnl":0.0,
                   "win_rate":0.0,"fees":0.0,"gross_pnl":0.0,"funding_fee":0.0,"opened":0,
                   "pending_settlements":0,"unknown_settlement_evidence":0,
                   "observed":{},"completeness":{}}
                for h in ("scalp","swing","unknown")}
        relevant=[]
        for row in ledger:
            opened=_dt(row.get("open_time")); closed=_dt(row.get("close_time"))
            opened_in_range=bool(opened and (start is None or opened >= start) and (end is None or opened <= end))
            closed_in_range=bool(closed and (start is None or closed >= start) and (end is None or closed <= end))
            unknown_close_in_range=(row.get("status")=="closed" and closed is None and
                ((is_today and opened_in_range) or (not is_today and (opened is None or opened <= end))))
            if opened_in_range or closed_in_range or unknown_close_in_range:
                relevant.append((row,opened,closed,opened_in_range,closed_in_range))
            if opened_in_range:
                h=str(row.get("horizon") or "unknown").lower(); h=h if h in groups else "unknown"
                groups[h]["opened"]+=1
        financial={h:{"net":[],"fees":[],"gross":[],"funding":[],
                      "unknown_net":0,"unknown_fee":0,"unknown_gross":0,"unknown_funding":0}
                   for h in groups}
        for row,opened,closed,opened_in_range,closed_in_range in relevant:
            h=str(row.get("horizon") or "unknown").lower(); h=h if h in groups else "unknown"
            status=row.get("status")
            if status=="closed_pending":
                if closed_in_range or (closed is None and opened_in_range):
                    groups[h]["pending_settlements"]+=1
                continue
            if status!="closed": continue
            if closed is None:
                # An opening date may scope this evidence gap, but never proves a close.
                if opened_in_range or (not is_today and opened is None):
                    groups[h]["unknown_settlement_evidence"]+=1
                continue
            if not closed_in_range: continue
            pnl=_finite(row.get("net_pnl",row.get("pnl")))
            f=_finite(row.get("fee")); g=_finite(row.get("gross_pnl")); funding=_finite(row.get("funding_fee"))
            a=financial[h]
            if pnl is None:
                a["unknown_net"]+=1
            else:
                bucket=groups[h]; bucket["closed"]+=1
                bucket["wins"]+=int(pnl>0); bucket["losses"]+=int(pnl<0); bucket["breakeven"]+=int(pnl==0)
                a["net"].append(pnl)
            for field,value,values,unknown_key in (("fee",f,a["fees"],"unknown_fee"),
                    ("gross_pnl",g,a["gross"],"unknown_gross"),
                    ("funding_fee",funding,a["funding"],"unknown_funding")):
                if value is None: a[unknown_key]+=1
                else: values.append(value)
        for h,b in groups.items():
            a=financial[h]
            net_observed=round(sum(a["net"]),8)
            b["observed"]["net_pnl"]=net_observed
            b["completeness"]["net_pnl"]=a["unknown_net"]==0
            b["net_pnl"]=net_observed if a["unknown_net"]==0 else None
            if a["unknown_net"]: b["observed"]["unknown_net_rows"]=a["unknown_net"]
            b["win_rate"]=round(b["wins"]/b["closed"],6) if b["closed"] else 0.0
            for key,values,unknown in (("fees",a["fees"],a["unknown_fee"]),
                    ("gross_pnl",a["gross"],a["unknown_gross"]),
                    ("funding_fee",a["funding"],a["unknown_funding"])):
                observed=round(sum(values),8); complete=unknown==0
                b["observed"][key]=observed; b["completeness"][key]=complete
                b[key]=observed if complete else None
        event_times=[t for _,opened,closed,_,_ in relevant for t in (opened,closed)
                     if t and (start is None or t >= start) and (end is None or t <= end)]
        coverage_start=start if is_today else (min(event_times) if event_times else None)
        coverage_end=end if is_today else (max(event_times) if event_times else None)
        return {**groups,"coverage":{"start":coverage_start.isoformat(timespec="seconds") if coverage_start else None,
            "end":coverage_end.isoformat(timespec="seconds") if coverage_end else None,
            "rows":len(relevant),"scope":scope,"complete":False,
            "limitations":["today begins at Asia/Shanghai midnight; retained ledger coverage only", "ledger ingestion completeness is not independently verified"] if is_today else ["all-time means retained verifiable ledger rows, not exchange lifetime", "history may be incomplete before retained coverage"]},
            "pending_settlements":sum(x["pending_settlements"] for x in groups.values()),
            "unknown_settlement_evidence":sum(x["unknown_settlement_evidence"] for x in groups.values())}
    return {"periods":{"today":summarize(midnight,now_dt,True),"all":summarize(None,now_dt,False)},
            "timezone":"Asia/Shanghai","as_of":as_of,"scope":scope,
            "version":SCHEMA_VERSION,"schema_version":SCHEMA_VERSION}


def rebuild(rows, *, scope=None):
    from scripts.statistics_epoch import filter_rows
    rows=filter_rows(rows,scope=scope,keep_active=False)
    from scripts.trade_quality import finite,loss_class
    result={"scalp":{},"swing":{},"unknown":{},"by_strategy":{},"by_version":{},"by_loss_class":{},"pending_settlements":0}
    buckets=[]
    def bucket(group,key,**meta):
        if key not in group:
            group[key]=dict(meta);buckets.append(group[key])
        return group[key]
    buckets.extend(result[h] for h in ('scalp','swing','unknown'))
    for row in rows if isinstance(rows,list) else []:
        if scope is not None and row.get('environment_id') != scope:continue
        if row.get('status') not in ('closed','closed_pending'):continue
        pnl=finite(row.get('net_pnl',row.get('pnl')))
        if row.get('status')!='closed' or pnl is None:
            result['pending_settlements']+=1;continue
        h=str(row.get('horizon') or 'unknown').lower()
        if h not in ('scalp','swing'):h='unknown'
        strategy=str(row.get('strategy_type') or row.get('setup') or 'unknown')
        version=str(row.get('strategy_version') or 'unknown');engine=str(row.get('strategy_engine') or 'unknown')
        sb=bucket(result['by_strategy'],f'{h}:{strategy}',horizon=h,strategy_type=strategy)
        vb=bucket(result['by_version'],f'{version}:{engine}:{h}:{strategy}',strategy_version=version,strategy_engine=engine,horizon=h,setup=strategy)
        classification=loss_class(row)
        cb=bucket(result['by_loss_class'],classification,classification=classification)
        for b in (result[h],sb,vb,cb):
            b['closed']=b.get('closed',0)+1
            for k,yes in (('wins',pnl>0),('losses',pnl<0),('breakeven',pnl==0)):
                b[k]=b.get(k,0)+int(yes)
            b['net_pnl']=round(b.get('net_pnl',0)+pnl,8)
            for total, field in (('fees', 'fee'), ('gross_pnl', 'gross_pnl')):
                amount = finite(row.get(field))
                b[total] = round(b.get(total, 0) + (amount if amount is not None else 0), 8)
                b[total + '_unknown'] = b.get(total + '_unknown', 0) + int(amount is None)
            hold=finite(row.get('duration_seconds'))
            if hold is not None and hold>=0:
                b['hold_seconds']=b.get('hold_seconds',0)+hold;b['hold_samples']=b.get('hold_samples',0)+1
            b['evidence_complete']=b.get('evidence_complete',0)+int(row.get('evidence_status')=='complete')
    for b in buckets:
        for total in ('fees', 'gross_pnl'):
            if b.get(total + '_unknown', 0):
                b[total + '_observed'] = b[total]
                b[total] = None
        n=b.get('closed',0)
        b['win_rate']=round(b.get('wins',0)/n,6) if n else 0.
        b['avg_pnl']=round(b.get('net_pnl',0)/n,8) if n else 0.
        holds=b.pop('hold_samples',0);total=b.pop('hold_seconds',0)
        b['avg_hold_seconds']=round(total/holds,3) if holds else 0.
    period_payload=strategy_periods(rows, scope=scope)
    result.update(periods=period_payload["periods"], timezone=period_payload["timezone"],
                  as_of=period_payload["as_of"], scope=scope, version=period_payload["version"],
                  schema_version=period_payload["schema_version"])
    return result

def write(rows, *, scope=None):
    payload=rebuild(rows, scope=scope)
    from scripts.statistics_epoch import epoch
    window=epoch(scope) if scope is not None else None
    if window:payload['statistics_epoch_id']=window['id']
    if scope is not None:
        from scripts.horizon_funnel import rebuild as rebuild_funnel
        from scripts.horizon_allocation import public_status
        mode='live' if ':live:' in scope else 'demo'
        payload['funnel']=rebuild_funnel(rows,scope=scope)
        payload['allocation']=public_status(scope,mode,rows=rows)
    DATA.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=".horizon-stats-",suffix=".tmp",dir=DATA)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,PATH)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return payload
