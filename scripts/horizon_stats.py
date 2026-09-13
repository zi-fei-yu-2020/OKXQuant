"""Deterministic strategy telemetry derived from the lifecycle ledger."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
PATH=DATA/"horizon_stats.json"

def rebuild(rows):
    result={"scalp":{},"swing":{},"unknown":{},"by_strategy":{}}
    for row in rows if isinstance(rows,list) else []:
        if row.get("status") not in {"closed","closed_pending"}: continue
        h=str(row.get("horizon") or "unknown").lower()
        if h not in result:h="unknown"
        b=result[h]; b["closed"]=int(b.get("closed",0))+1
        strategy=str(row.get("strategy_type") or row.get("setup") or "unknown")
        key=f"{h}:{strategy}"
        sb=result["by_strategy"].setdefault(key,{"horizon":h,"strategy_type":strategy})
        sb["closed"]=int(sb.get("closed",0))+1
        pnl=float(row.get("net_pnl",row.get("pnl",0)) or 0); fee=float(row.get("fee",0) or 0)
        b["wins"]=int(b.get("wins",0))+int(pnl>0); b["losses"]=int(b.get("losses",0))+int(pnl<=0)
        sb["wins"]=int(sb.get("wins",0))+int(pnl>0); sb["losses"]=int(sb.get("losses",0))+int(pnl<=0)
        b["net_pnl"]=round(float(b.get("net_pnl",0))+pnl,8); b["fees"]=round(float(b.get("fees",0))+fee,8)
        sb["net_pnl"]=round(float(sb.get("net_pnl",0))+pnl,8); sb["fees"]=round(float(sb.get("fees",0))+fee,8)
        hold=float(row.get("duration_seconds",0) or 0); b["hold_seconds"]=round(float(b.get("hold_seconds",0))+hold,3)
    for name,b in result.items():
        if name=="by_strategy": continue
        n=b.get("closed",0); b["win_rate"]=round(b.get("wins",0)/n,6) if n else 0.0; b["avg_pnl"]=round(b.get("net_pnl",0)/n,8) if n else 0.0; b["avg_hold_seconds"]=round(b.get("hold_seconds",0)/n,3) if n else 0.0; b.pop("hold_seconds",None)
    for b in result["by_strategy"].values():
        n=b.get("closed",0); b["win_rate"]=round(b.get("wins",0)/n,6) if n else 0.0; b["avg_pnl"]=round(b.get("net_pnl",0)/n,8) if n else 0.0
    return result

def write(rows):
    payload=rebuild(rows); DATA.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=".horizon-stats-",suffix=".tmp",dir=DATA)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,PATH)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return payload
