"""Deterministic strategy telemetry derived from the lifecycle ledger."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
PATH=DATA/"horizon_stats.json"

def rebuild(rows, *, scope=None):
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
    return result

def write(rows, *, scope=None):
    payload=rebuild(rows, scope=scope); DATA.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=".horizon-stats-",suffix=".tmp",dir=DATA)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,PATH)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return payload
