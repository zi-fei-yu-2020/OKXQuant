#!/usr/bin/env python3
"""Read-only incremental fill archive. Missing pages are explicit, never fabricated."""
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import strategy_evidence as evidence
from scripts.okx_runtime import selected_environment
from okxquant_backend.okx_trade_service import _request


def collect_fills(env,*,max_pages=5):
    with evidence.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS sync_state(scope TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        row=db.execute('SELECT payload FROM sync_state WHERE scope=?',(env.identity,)).fetchone()
    state=json.loads(row[0]) if row else {}
    since=float(state.get('since',max(0,float(state.get('covered_until',0))-300)))
    cursor=state.get('cursor');seen=set();count=0;complete=False
    started=float(state.get('started_at',time.time()))
    for _ in range(max_pages):
        params={'instType':'SWAP','limit':'100'}
        if cursor:params['after']=cursor
        rows=_request('GET','/api/v5/trade/fills-history',params,env)
        batch=[]
        for item in rows:
            identity=str(item.get('billId') or '')
            if not identity or not item.get('ordId') or not item.get('instId'):
                raise ValueError('Incomplete exchange fill identity')
            if identity in seen:continue
            seen.add(identity)
            batch.append(('fill:'+env.identity+':'+identity,item))
            count+=1
        evidence.append_batch(env.identity,'fill',batch)
        if len(rows)<100 or any(float(r.get('ts',0))/1000<=since for r in rows):
            complete=True;break
        next_cursor=str(rows[-1].get('billId'))
        if next_cursor==cursor:break
        cursor=next_cursor
    next_state=({'covered_until':started} if complete else {'since':since,'cursor':cursor,'started_at':started})
    with evidence.connection() as db:
        db.execute('INSERT OR REPLACE INTO sync_state VALUES (?,?)',(env.identity,evidence.canonical(next_state)))
    result={'started_at':started,'archived_rows':count,'complete':complete,'since':since,
            'retention_boundary':'exchange fills-history availability; not a lifetime-history guarantee'}
    evidence.append(env.identity,'fill_sync_complete' if complete else 'fill_sync_partial',result)
    return result

if __name__=='__main__':
    print(json.dumps(collect_fills(selected_environment()),ensure_ascii=False))
