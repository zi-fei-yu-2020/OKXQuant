"""Read-only ledger/receipt replay. Does not import a trader or authorize orders."""
import argparse
import collections
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from scripts.execution_replay import analyze,geometry


def decoded(raw,digest):
    if hashlib.sha256(raw.encode()).hexdigest()!=digest:return None
    return json.loads(raw)


def archived_inputs(path, scope, rows):
    submissions={};samples=collections.defaultdict(list)
    orders={str(oid) for row in rows for oid in row.get('opening_order_ids',[])}
    keys={(row.get('instId'),str(row.get('pos_id')),str(row.get('position_created_at'))) for row in rows}
    starts=[float(row['position_created_at'])/1000 for row in rows if row.get('position_created_at')]
    since=min(starts)-300 if starts else 0
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        for raw,digest in db.execute("SELECT payload,digest FROM events WHERE scope=? AND kind='entry_submission' AND at>=? ORDER BY at DESC LIMIT 4000",(scope,since)):
            event=decoded(raw,digest)
            if not event:continue
            plan=event.get('plan') or {};did=plan.get('decision_id')
            response=event.get('response') or []
            if isinstance(response,dict):response=response.get('data') or [response]
            if not isinstance(response,list):continue
            matching=[str(receipt['ordId']) for receipt in response if isinstance(receipt,dict) and str(receipt.get('ordId')) in orders and str(receipt.get('sCode','0'))=='0']
            if not matching or not did:continue
            record=db.execute("SELECT payload,digest FROM events WHERE id=? AND scope=? AND kind='decision'",(did,scope)).fetchone()
            if not record:continue
            decision=decoded(*record)
            if not decision:continue
            decision=decision.get('decision') or {}
            candidate=next((plan_candidate for plan_candidate in (decision.get('entry_plans') or {}).get('plans',[]) if plan_candidate.get('id')==decision.get('candidate_id')),None)
            frozen=plan.get('entry_geometry') or geometry(candidate,plan)
            for oid in matching:submissions.setdefault(oid,frozen)
        for at,raw,digest in db.execute("SELECT at,payload,digest FROM events WHERE scope=? AND kind='position_observation' AND at>=? ORDER BY at DESC LIMIT 40000",(scope,since)):
            sample=decoded(raw,digest)
            if not sample:continue
            bound=sample.get('identity') or {};key=(bound.get('instId'),str(bound.get('posId')),str(bound.get('cTime')))
            if key in keys:samples[key].append({**sample,'at':at})
        for at,raw,digest in db.execute("SELECT at,payload,digest FROM events WHERE scope=? AND kind='scalp_exit_evaluation' AND at>=? ORDER BY at DESC LIMIT 4000",(scope,since)):
            event=decoded(raw,digest)
            if not event:continue
            bound=event.get('position_identity') or {};key=(bound.get('instId'),str(bound.get('posId')),str(bound.get('cTime')))
            if key in keys:samples[key].append({'at':at,'management_state':event.get('evaluation') or {},'basis':'journaled_exit_evaluation'})
    return submissions,samples


def report(rows, *, evidence_db=None, bars_by_trade=None):
    closed=[row for row in rows if row.get('status')=='closed'];results=[]
    for row in closed:
        if not row.get('environment_id'):
            results.append({'id':row.get('id'),'scope':None,'archive_status':'missing_account_scope',
                            'replay':{'status':'unavailable','classification':'insufficient_evidence'}})
    for scope in sorted({row.get('environment_id') for row in closed if row.get('environment_id')}):
        scoped=[row for row in closed if row.get('environment_id')==scope]
        submissions={};observations={};archive_status='not_provided'
        if evidence_db:
            try:
                submissions,observations=archived_inputs(evidence_db,scope,scoped);archive_status='read_only_verified_digests'
            except (OSError,sqlite3.Error,ValueError,TypeError):archive_status='unavailable'
        for row in scoped:
            history=row.get('exit_snapshot') or {}
            key=(row.get('instId'),str(row.get('pos_id')),str(row.get('position_created_at')))
            samples=[sample for sample in observations.get(key,[]) if float(history.get('cTime') or 0)/1000<=sample['at']<=float(history.get('uTime') or 0)/1000]
            ids=row.get('opening_order_ids') or []
            frozen=row.get('entry_geometry')
            if not frozen and len(ids)==1:frozen=submissions.get(str(ids[0]))
            diagnostic=analyze(frozen,history,samples,bars=(bars_by_trade or {}).get(row.get('id'),[]),
                fees_verified=(row.get('fee_reconciliation') or {}).get('status')=='verified')
            results.append({'id':row.get('id'),'scope':scope,'instrument':row.get('inst'),
                'setup':row.get('setup'),'open_time':row.get('open_time'),'close_time':row.get('close_time'),
                'official_net_pnl':row.get('net_pnl'),'archive_status':archive_status,'replay':diagnostic})
    return {'version':'joint-trade-replay-v1','mode':'read_only_receipts_and_observations',
            'profitability_backtest':False,'entry_rules_changed':False,'trades':len(results),
            'classifications':dict(collections.Counter(row['replay']['classification'] for row in results)),
            'rows':results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger',required=True,type=Path)
    parser.add_argument('--evidence-db',type=Path)
    parser.add_argument('--candles',type=Path,help='JSON mapping of ledger trade ID to closed 1M OHLC bars')
    parser.add_argument('--start',help='Inclusive Beijing close date, YYYY-MM-DD')
    parser.add_argument('--end',help='Inclusive Beijing close date, YYYY-MM-DD')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();rows=json.loads(args.ledger.read_text(encoding='utf-8'))
    if any(args.output.resolve()==path.resolve() for path in (args.ledger,args.evidence_db,args.candles) if path):
        parser.error('Report output must not overwrite an input or evidence database')
    try:
        for value in (args.start,args.end):
            if value and date.fromisoformat(value).isoformat()!=value:raise ValueError('Invalid date')
        if args.start and args.end and args.start>args.end:raise ValueError('Invalid date range')
    except ValueError:parser.error('Use an ordered inclusive YYYY-MM-DD date range')
    if not isinstance(rows,list):parser.error('Ledger must be an array')
    if args.start:rows=[row for row in rows if str(row.get('close_time',''))[:10]>=args.start]
    if args.end:rows=[row for row in rows if str(row.get('close_time',''))[:10]<=args.end]
    bars=json.loads(args.candles.read_text(encoding='utf-8')) if args.candles else None
    result=report(rows,evidence_db=args.evidence_db,bars_by_trade=bars)
    args.output.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    print(json.dumps({field:value for field,value in result.items() if field!='rows'},ensure_ascii=True))


if __name__=='__main__':main()
