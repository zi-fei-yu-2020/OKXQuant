#!/usr/bin/env python3
"""Independent read-only exchange reconciliation and confirmed-close projection.

No model/order/cancel/amend/close functions are called here. Notifications are off.
"""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from functools import wraps
from pathlib import Path
import json
import os
import sys
import tempfile
import time
import uuid
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT,ROOT/'scripts'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
DATA=ROOT/'data'

def load(name,default):
    try:return json.loads((DATA/name).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def atomic(name,value):
    DATA.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.ledger-',suffix='.tmp',dir=DATA)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(tmp,DATA/name)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

@contextmanager
def lock(name,blocking=True):
    import fcntl
    DATA.mkdir(parents=True,exist_ok=True)
    with (DATA/name).open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)

def serialized(fn):
    @wraps(fn)
    def call(*a,**kw):
        with lock('.ledger-sync.lock'):return fn(*a,**kw)
    return call

def bj(ms):
    try:return datetime.fromtimestamp(float(ms)/1000,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError,ValueError,OverflowError):return ''

def request_refresh(reason='manual_close'):
    request={'id':uuid.uuid4().hex,'at':time.time(),'reason':reason}
    atomic('ledger_refresh_request.json',request)
    return request['id']

def note_confirmed_close(env,target,result,client_id):
    """Called only AFTER exchange positions are confirmed zero. Does not touch the ledger lock."""
    event={'id':uuid.uuid4().hex,'environment_id':env.identity,'environment':env.mode,'instId':target['instId'],
        'posId':str(target.get('posId') or ''),'posSide':str(target.get('posSide') or 'net'),'open_time':bj(target.get('cTime')),
        'confirmed_at':time.time(),'client_order_id':client_id,'source':'manual_admin','state':'confirmed_closed'}
    with lock('.ledger-events.lock'):
        events=load('confirmed_closes.json',[])
        atomic('confirmed_closes.json',([event]+events)[:200])
    return request_refresh()

def event_for(row,events,scope):
    if row.get('environment_id') and row['environment_id'] != scope:return None
    side='long' if row.get('side') in {'多','long'} else 'short'
    for event in events:
        if event.get('environment_id')!=scope or event.get('state')!='confirmed_closed':continue
        if event.get('instId')!=row.get('instId',str(row.get('inst',''))+'-USDT-SWAP'):continue
        if event.get('posSide') not in {side,'net'}:continue
        if not row.get('open_time') or row.get('open_time')!=event.get('open_time'):continue
        if row.get('pos_id') and event.get('posId') and str(row['pos_id'])!=str(event['posId']):continue
        return event
    return None

def project_rows(rows,scope,*,positions=None):
    """Confirmed closed != settled. Never keep estimated PnL in a pending settlement row."""
    events=load('confirmed_closes.json',[])
    result=[]
    current={(p.get('instId'),p.get('posSide')) for p in positions or [] if abs(float(p.get('pos') or 0))>0}
    for original in rows:
        row=dict(original)
        if row.get('status') in {'holding','closed_pending'}:
            event=event_for(row,events,scope)
            key=(row.get('instId',str(row.get('inst',''))+'-USDT-SWAP'),'long' if row.get('side') in {'多','long'} else 'short')
            absent=positions is not None and row.get('environment_id')==scope and key not in current
            if event or absent:
                row.update(status='closed_pending',settlement_status='pending',pnl=None,net_pnl=None,gross_pnl=None,
                    roi=None,roi_pct=None,fee=None,open_fee=None,close_fee=None,close_px=None,sz=0,
                    fee_allocation="pending_settlement",fee_reconciliation={"status":"unverified","reason":"pending_settlement"},
                    close_time='--',confirmed_close_at=bj(event['confirmed_at']*1000) if event else '',
                    exit_reason='手动平仓（结算同步中）' if event else '持仓已归零，等待交易所结算',
                    exit_source='manual_admin' if event else 'unknown')
        result.append(row)
    return result

def should_run(last_run,now=None):
    now=time.time() if now is None else now
    status=load('ledger_sync_status.json',{});request=load('ledger_refresh_request.json',{})
    if status.get('status')=='error' and status.get('last_error_at',0)>=request.get('at',0) and now-last_run<60:
        return False
    if request.get('id') and request['id']!=status.get('handled_request'):
        return now-last_run>=5
    if status.get('pending_settlements',0) and now-(status.get('pending_since') or 0)<120:
        return now-last_run>=10
    return now-last_run>=60

def sync_once():
    from scripts.okx_runtime import selected_environment
    from scripts import sync_full_ledger
    env=selected_environment();requested=load('ledger_refresh_request.json',{})
    try:
        rows=sync_full_ledger.build_lifecycle_ledger(notify=False)
        from scripts.db_manager import sync_json_to_sqlite
        try:
            mirror = {'status':'ok','rows':sync_json_to_sqlite(DATA/'trading_ledger.json')}
        except Exception as exc:
            # JSON is authoritative and has already been atomically updated.
            # Report mirror failure separately; never discard the good ledger.
            mirror = {'status':'error','error_type':type(exc).__name__}
        previous=load('ledger_sync_status.json',{})
        pending=sum(r.get('status')=='closed_pending' for r in rows)
        state={'status':'ok' if mirror['status']=='ok' else 'partial','sqlite_mirror':mirror,'last_success':time.time(),'pending_since':(previous.get('pending_since') or time.time()) if pending else None,'environment_id':env.identity,'environment':env.mode,
               'handled_request':requested.get('id'),'pending_settlements':sum(r.get('status')=='closed_pending' for r in rows),'rows':len(rows)}
        atomic('ledger_sync_status.json',state);return state
    except Exception as exc:
        previous=load('ledger_sync_status.json',{})
        atomic('ledger_sync_status.json',{**previous,'status':'error','last_error_at':time.time(),'error':type(exc).__name__})
        raise

if __name__=='__main__':
    result=sync_once()
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['status']=='ok' else 1)
