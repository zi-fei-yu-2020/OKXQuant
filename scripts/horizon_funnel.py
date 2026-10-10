"""Bounded scalp/swing decision-to-trade funnel generated off the request path."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import time

BEIJING=timezone(timedelta(hours=8))

def _horizon(payload):
    decision=payload.get("decision") if isinstance(payload.get("decision"),dict) else {}
    plan=payload.get("plan") if isinstance(payload.get("plan"),dict) else {}
    features=payload.get("features") if isinstance(payload.get("features"),dict) else {}
    value=str(plan.get("horizon") or decision.get("horizon") or payload.get("horizon") or "").lower()
    if value in {"scalp","swing"}:return value
    if features.get("strategy_engine")=="demo_scalp_v2" or plan.get("entry_engine")=="demo_scalp_v2":return "scalp"
    return "unknown"


def _project_event(payload):
    """Only funnel facts; never retain candle arrays/prompts in the hot projection."""
    decision=payload.get("decision") if isinstance(payload.get("decision"),dict) else {}
    return {"horizon":_horizon(payload), "decision_id":payload.get("decision_id"),
            "decision_status":decision.get("decision_status"),
            "transport_ok":payload.get("transport_ok") is True,
            "failure":bool(payload.get("failure"))}


def _stamp(value):
    if value in (None,"","--"):return None
    if isinstance(value,(int,float)):
        value=float(value);return value/1000 if value>10_000_000_000 else value
    text=str(value).strip()
    try:
        numeric=float(text);return numeric/1000 if numeric>10_000_000_000 else numeric
    except ValueError:
        pass
    try:
        parsed=datetime.fromisoformat(text.replace("Z","+00:00"))
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=BEIJING)
        return parsed.timestamp()
    except ValueError:return None


def _bucket():
    return {h:{"decisions":0,"audited_wait":0,"incomplete":0,"entry_candidate":0,"risk_rejected":0,
               "submitted":0,"filled":0,"closed":0,"wins":0,"net_pnl":0.0,"fees":0.0}
            for h in ("scalp","swing","unknown")}


def rebuild(rows, *, scope, now=None, db_path=None):
    now=time.time() if now is None else float(now);starts={"1d":now-86400,"7d":now-7*86400}
    from scripts.statistics_epoch import epoch, filter_rows
    window=epoch(scope)
    if window: starts={key:max(value,window['started_at']) for key,value in starts.items()}
    rows=filter_rows(rows,scope=scope,keep_active=False)
    result={name:_bucket() for name in starts}
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict) or row.get("environment_id")!=scope:continue
        horizon=str(row.get("horizon") or row.get("execution_horizon") or "unknown").lower()
        if horizon not in {"scalp","swing"}:
            horizon="scalp" if str(row.get("setup") or "").startswith("scalp_") or row.get("strategy_engine")=="demo_scalp_v2" else "unknown"
        opened=_stamp(row.get("position_created_at") or row.get("open_time"));closed=_stamp(row.get("confirmed_close_at") or row.get("close_time"))
        for name,start in starts.items():
            bucket=result[name][horizon]
            if opened is not None and opened>=start:bucket["filled"]+=1
            if row.get("status")=="closed" and closed is not None and closed>=start:
                bucket["closed"]+=1
                try:pnl=float(row.get("net_pnl",row.get("pnl")) or 0)
                except (TypeError,ValueError):pnl=0
                bucket["wins"]+=int(pnl>0);bucket["net_pnl"]+=pnl
                try:bucket["fees"]+=float(row.get("fee") or 0)
                except (TypeError,ValueError):pass
    if db_path is None:
        from scripts.strategy_evidence import DB_PATH
        db_path=DB_PATH
    path=Path(db_path)
    if path.exists():
        try:
            from scripts.evidence_projection import projected_events
            parsed=projected_events(path, scope=scope, since=starts["7d"],
                kinds=("decision","entry_rejection","entry_submission"),
                namespace="horizon-funnel-v1", project=_project_event)
            decisions={identity:payload["horizon"] for identity,kind,at,payload in parsed if kind=="decision"}
            for identity,kind,at,payload in parsed:
                horizon=payload["horizon"]
                if horizon=="unknown" and payload.get("decision_id"):horizon=decisions.get(str(payload["decision_id"]),"unknown")
                for name,start in starts.items():
                    if at<start:continue
                    bucket=result[name][horizon]
                    if kind=="decision":
                        bucket["decisions"]+=1
                        status=payload.get("decision_status")
                        if status in {"audited_wait","incomplete","entry_candidate"}:bucket[status]+=1
                    elif kind=="entry_rejection":bucket["risk_rejected"]+=1
                    elif kind=="entry_submission" and payload.get("transport_ok") is True and not payload.get("failure"):bucket["submitted"]+=1
        except (OSError,sqlite3.Error,ValueError,TypeError,json.JSONDecodeError) as exc:
            result["evidence_error"]=type(exc).__name__
    for name in starts:
        for bucket in result[name].values():
            bucket["net_pnl"]=round(bucket["net_pnl"],8);bucket["fees"]=round(bucket["fees"],8)
            bucket["win_rate"]=round(bucket["wins"]/bucket["closed"],6) if bucket["closed"] else 0.0
    result["generated_at"]=now
    return result
