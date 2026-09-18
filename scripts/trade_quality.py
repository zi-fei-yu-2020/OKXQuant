"""Explicit evidence gaps and loss attribution, never inferred profits or exits."""
import json,math,sqlite3
from pathlib import Path
from scripts import strategy_evidence as evidence


def observation_index(scope):
    try:
        with sqlite3.connect(Path(evidence.DB_PATH).resolve().as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute("SELECT at,payload FROM events WHERE scope=? AND kind='position_observation' ORDER BY at DESC LIMIT 20000",(scope,)).fetchall()
        result={}
        for at,raw in rows:
            p=json.loads(raw);identity=p.get('identity') or {}
            key=(identity.get('instId'),str(identity.get('posId')),str(identity.get('cTime')))
            result.setdefault(key,[]).append({'at':at,**p})
        return result
    except (OSError,ValueError,sqlite3.Error):return {}


def record_observation(scope,position,tracker):
    from scripts.position_lifecycle import identity
    evidence.best_effort(scope,'position_observation',{'identity':identity(position,scope),
        'position':{k:position.get(k) for k in ('instId','posSide','posId','cTime','pos','avgPx','markPx','upl','lever')},
        'horizon':tracker.get('horizon'),'mode':tracker.get('mode'),'setup':tracker.get('setup'),
        'management_state':tracker.get('scalpManagement'),'entry_context':tracker.get('entry_context'),
        'exit_evaluation':tracker.get('exitEvaluation'),'stop':tracker.get('trailingStopPx')})


def finite(value):
    if value is None or isinstance(value,bool):return None
    try:n=float(value)
    except (ValueError,TypeError):return None
    return n if math.isfinite(n) else None


def loss_class(row):
    pnl=finite(row.get('net_pnl',row.get('pnl')))
    if row.get('status')!='closed' or pnl is None:return 'pending_settlement'
    codes={p.get('reason_code') for p in row.get('close_order_sources') or []}
    if row.get('attribution_status')=='unknown' or row.get('exit_source')=='unknown':return 'unresolved_exit'
    if codes & {'oco_unverified','independent_guard'}:return 'execution_safety_exit'
    if pnl>=0:return 'profit' if pnl>0 else 'breakeven'
    gross=finite(row.get('gross_pnl'))
    if gross is not None and gross>=0:return 'cost_loss'
    return 'strategy_or_market_loss'


def annotate(row,history,observations,executions):
    key=(history.get('instId'),str(history.get('posId')),str(history.get('cTime')))
    start=float(history.get('cTime') or 0)/1000;end=float(history.get('uTime') or 0)/1000
    samples=[p for p in observations.get(key,[]) if start<=p['at']<=end]
    upl=[n for p in samples if (n:=finite((p.get('position') or {}).get('upl'))) is not None]
    row['holding_observations']={'samples':len(samples),'sampled_max_unrealized_pnl':max(upl) if upl else None,
        'sampled_min_unrealized_pnl':min(upl) if upl else None,'basis':'periodic_snapshots_not_tick_extremes'}
    exits=[e for e in executions if str(e.get('position_id'))==key[1]
           and e.get('instId')==key[0] and str(e.get('position_created_at'))==key[2]
           and e.get('status') in ('accepted','confirmed')
           and 0<=end-float(e.get('started_at') or 0)<=120
           and finite((e.get('position_snapshot') or {}).get('markPx')) is not None]
    if exits:
        latest=max(exits,key=lambda e:float(e.get('started_at') or 0))
        row['exit_market_snapshot']={'observed_at':latest['started_at'],**latest['position_snapshot']}
    elif samples and 0<=end-samples[0]['at']<=120:
        row['exit_market_snapshot']={'observed_at':samples[0]['at'],**samples[0]['position']}
    else:row['exit_market_snapshot']=None
    gaps=[]
    required=('strategy_version','setup','decision_id','candidate_id','opening_order_ids','opening_trade_ids','opening_features','news_snapshot')
    for k in required:
        if not row.get(k):gaps.append(k)
    if row.get('strategy_version') in ('local','unknown'):gaps.append('version_not_reproducible')
    if row.get('horizon') not in ('scalp','swing'):gaps.append('horizon')
    if row.get('execution_horizon') and row.get('decision_horizon')!=row.get('execution_horizon'):gaps.append('horizon_execution_mismatch')
    if row.get('attribution_status') not in ('verified','corroborated','mixed') or any(p.get('tier')=='unknown' for p in row.get('close_order_sources') or []):gaps.append('exit_attribution')
    if not row.get('close_order_ids'):gaps.append('close_order_ids')
    if not row['exit_market_snapshot']:gaps.append('exit_market_snapshot')
    if (row.get('fee_reconciliation') or {}).get('status')!='verified':gaps.append('fees')
    from scripts.execution_costs import compare_actual
    row['cost_comparison']=compare_actual(row.get('execution_cost_model'),history,row.get('fee')) if (row.get('fee_reconciliation') or {}).get('status')=='verified' else {'status':'unavailable','reason':'fees_not_verified'}
    row.update(evidence_status='partial' if gaps else 'complete',evidence_gaps=gaps,loss_classification=loss_class(row))
    return row
