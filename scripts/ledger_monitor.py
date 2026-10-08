#!/usr/bin/env python3
"""Independent read-only exchange reconciliation and confirmed-close projection.

No model/order/cancel/amend/close functions are called here. Notifications are off.
"""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from functools import wraps
from pathlib import Path
import json
import math
import os
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT,ROOT/'scripts'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
DATA=ROOT/'data'
ACTIVE_INTERVAL_SECONDS=60
IDLE_INTERVAL_SECONDS=300
PENDING_SETTLEMENT_INTERVAL_SECONDS=300
from scripts.json_projection_cache import VersionedJsonProjection


def _project_ledger_activity(rows):
    if not isinstance(rows, list):
        raise ValueError('ledger activity must be a list')
    scopes = {}
    unscoped_active = False
    for row in rows:
        if not isinstance(row, dict) or row.get('status') not in {'holding', 'closed_pending', 'closed'}:
            raise ValueError('ledger activity row is malformed')
        scope = row.get('environment_id')
        if scope is None or scope == '':
            if row['status'] != 'closed':
                unscoped_active = True
            continue
        if not isinstance(scope, str):
            raise ValueError('ledger activity account scope is malformed')
        counts = scopes.setdefault(scope, {'holding_count': 0, 'closed_pending_count': 0})
        counts['holding_count'] += row['status'] == 'holding'
        counts['closed_pending_count'] += row['status'] == 'closed_pending'
    return {'scopes': scopes, 'unscoped_active': unscoped_active}


_LEDGER_ACTIVITY_CACHE = VersionedJsonProjection(_project_ledger_activity)
_INTENT_CACHE_LOCK = threading.Lock()
_INTENT_CACHE_KEY = None
_INTENT_CACHE_VALUE = None
_INTENT_DB_CONNECTION = None
_INTENT_DB_PATH = None
_INTENT_DB_SIGNATURE = None

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
            from scripts.ledger_accounting import matches_lifecycle
            live = [p for p in positions or [] if abs(float(p.get('pos') or 0)) > 0]
            exact_identity = row.get('pos_id') and row.get('open_time')
            present = (any(matches_lifecycle(row, p, scope) for p in live)
                       if exact_identity else key in current)
            absent=positions is not None and row.get('environment_id')==scope and not present
            if event or absent:
                row.update(status='closed_pending',settlement_status='pending',pnl=None,net_pnl=None,gross_pnl=None,
                    roi=None,roi_pct=None,fee=None,open_fee=None,close_fee=None,close_px=None,sz=0,
                    fee_allocation="pending_settlement",fee_reconciliation={"status":"unverified","reason":"pending_settlement"},
                    close_time='--',confirmed_close_at=bj(event['confirmed_at']*1000) if event else '',
                    exit_reason='手动平仓（结算同步中）' if event else '持仓已归零，等待交易所结算',
                    exit_source='manual_admin' if event else 'unknown')
        result.append(row)
    return result

def _control_file(name):
    """Return (object, valid); missing is distinct from unreadable/corrupt."""
    try:
        payload = json.loads((DATA / name).read_text(encoding='utf-8'))
        return (payload, isinstance(payload, dict))
    except FileNotFoundError:
        return {}, True
    except (OSError, ValueError, TypeError):
        return {}, False


def _db_file_signature(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _has_unresolved_intents(scope):
    """Generation-cached account-scoped existence query; never reads payloads."""
    global _INTENT_CACHE_KEY, _INTENT_CACHE_VALUE
    global _INTENT_DB_CONNECTION, _INTENT_DB_PATH, _INTENT_DB_SIGNATURE
    if not isinstance(scope, str) or not scope:
        raise ValueError('account scope unavailable')
    from scripts import strategy_evidence
    path = Path(strategy_evidence.DB_PATH).absolute()
    with _INTENT_CACHE_LOCK:
        for _ in range(2):
            before = _db_file_signature(path)  # Missing/corrupt evidence fails closed.
            if (_INTENT_DB_CONNECTION is None or _INTENT_DB_PATH != str(path)
                    or _INTENT_DB_SIGNATURE != before):
                if _INTENT_DB_CONNECTION is not None:
                    _INTENT_DB_CONNECTION.close()
                uri = path.as_uri() + '?mode=ro'
                _INTENT_DB_CONNECTION = sqlite3.connect(uri, uri=True, timeout=1.0)
                _INTENT_DB_CONNECTION.execute('PRAGMA query_only=ON')
                _INTENT_DB_PATH = str(path)
                _INTENT_DB_SIGNATURE = before
                _INTENT_CACHE_KEY = None
                _INTENT_CACHE_VALUE = None

            db = _INTENT_DB_CONNECTION
            data_version = db.execute('PRAGMA data_version').fetchone()[0]
            key = (str(path), before, scope, data_version)
            if key == _INTENT_CACHE_KEY:
                return _INTENT_CACHE_VALUE
            row = db.execute(
                "SELECT 1 FROM intents WHERE scope=? AND state IN ('unknown','acknowledged','pending') LIMIT 1",
                (scope,),
            ).fetchone()
            version_after = db.execute('PRAGMA data_version').fetchone()[0]
            after = _db_file_signature(path)
            if before == after and data_version == version_after:
                _INTENT_CACHE_KEY = key
                _INTENT_CACHE_VALUE = row is not None
                return _INTENT_CACHE_VALUE
        raise OSError('strategy evidence changed during intent read')


def _active_reconciliation_state(status, *, require_pending=False):
    """Prove current-account flatness; any unavailable/ambiguous source is active."""
    try:
        from scripts.okx_runtime import selected_environment
        current_scope = selected_environment().identity
        # Always consult the generation-cached ledger projection. On ordinary
        # cadence the currently selected account scopes known rows; long
        # settlement backoff additionally requires the sync status to prove
        # that its pending count belongs to this same account generation.
        activity = _LEDGER_ACTIVITY_CACHE.read(DATA / 'trading_ledger.json')
        ledger_scope = status.get('environment_id')
        if not isinstance(ledger_scope, str) or not ledger_scope:
            return True
        if ledger_scope != current_scope:
            return True
        if activity['unscoped_active']:
            return True
        scoped = activity['scopes'].get(current_scope, {'holding_count': 0, 'closed_pending_count': 0})
        if scoped['holding_count']:
            return True
        if require_pending and scoped['closed_pending_count'] <= 0:
            return True
        return _has_unresolved_intents(current_scope)
    except Exception:
        # Missing, corrupt, stale-account, or unreadable evidence must never
        # convert a reconciliation into the long flat-pending/idle interval.
        return True


def should_run(last_run,now=None):
    now=time.time() if now is None else now
    status,status_valid=_control_file('ledger_sync_status.json')
    request,request_valid=_control_file('ledger_refresh_request.json')
    if not request_valid:
        # A malformed request marker cannot prove that no urgent request exists.
        return now-last_run>=ACTIVE_INTERVAL_SECONDS
    if request.get('id') and request.get('id')!=status.get('handled_request'):
        return now-last_run>=5

    pending = status.get('pending_settlements', 0)
    pending_since = status.get('pending_since')
    pending_valid = (status_valid and type(pending) is int and pending >= 0
                     and (pending == 0 or (type(pending_since) in (int, float)
                         and math.isfinite(pending_since) and 0 <= pending_since <= now)))
    if not pending_valid:
        return now-last_run>=ACTIVE_INTERVAL_SECONDS
    if pending_valid and pending:
        if now-pending_since<120:
            return now-last_run>=10
        # Long settlement polling is allowed only with a current, validated
        # account ledger, no live holding, and no unresolved durable entry intent.
        if not _active_reconciliation_state(status, require_pending=True):
            return now-last_run>=PENDING_SETTLEMENT_INTERVAL_SECONDS
        return now-last_run>=ACTIVE_INTERVAL_SECONDS

    if (status_valid and status.get('status')=='error'
            and isinstance(status.get('last_error_at'), (int, float))
            and status.get('last_error_at', 0) >= request.get('at', 0)
            and now-last_run<ACTIVE_INTERVAL_SECONDS):
        return False

    if now-last_run<ACTIVE_INTERVAL_SECONDS:return False
    if not status_valid:
        return True
    active = _active_reconciliation_state(status)
    return now-last_run>=(ACTIVE_INTERVAL_SECONDS if active else IDLE_INTERVAL_SECONDS)


def settlement_diagnostics(rows, scope, history_latest_ms=0, now=None):
    """Show known openings separately from settled receipts; never estimate PnL."""
    now=time.time() if now is None else float(now)
    today=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    scoped=[r for r in rows if r.get('environment_id')==scope]
    opened=[r for r in scoped if str(r.get('open_time','')).startswith(today)]
    pending=[r for r in scoped if r.get('status')=='closed_pending']
    latest=float(history_latest_ms or 0)/1000
    unmatched_newer=0
    for row in pending:
        try:
            opened_at=datetime.strptime(str(row.get('open_time')),'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
            unmatched_newer+=int(opened_at>latest)
        except (TypeError,ValueError):pass
    return {'day':today,'known_opened_today':len(opened),
            'opened_today_pending_settlement':sum(r.get('status')=='closed_pending' for r in opened),
            'settled_closes_today':sum(r.get('status')=='closed' and str(r.get('close_time','')).startswith(today) for r in scoped),
            'latest_position_history_ms':int(history_latest_ms or 0),
            'pending_opened_after_latest_history':unmatched_newer,
            'status':'history_gap_observed' if unmatched_newer else 'pending_settlement' if pending else 'reconciled',
            'pnl_estimated':False}


def sync_once():
    from scripts.okx_runtime import selected_environment
    from scripts import sync_full_ledger
    env=selected_environment();requested=load('ledger_refresh_request.json',{})
    started=time.perf_counter();cpu_started=time.process_time()
    try:
        rows=sync_full_ledger.build_lifecycle_ledger(notify=False)
        build_finished=time.perf_counter()
        from scripts.db_manager import sync_json_to_sqlite
        try:
            mirror = {'status':'ok','rows':sync_json_to_sqlite(DATA/'trading_ledger.json',trades=rows)}
        except Exception as exc:
            # JSON is authoritative and has already been atomically updated.
            # Report mirror failure separately; never discard the good ledger.
            mirror = {'status':'error','error_type':type(exc).__name__}
        previous=load('ledger_sync_status.json',{})
        pending=sum(r.get('status')=='closed_pending' for r in rows)
        state={'status':'ok' if mirror['status']=='ok' else 'partial','sqlite_mirror':mirror,'last_success':time.time(),'pending_since':(previous.get('pending_since') or time.time()) if pending else None,'environment_id':env.identity,'environment':env.mode,
               'handled_request':requested.get('id'),'pending_settlements':sum(r.get('status')=='closed_pending' for r in rows),'rows':len(rows)}
        state['performance']={'build_seconds':round(build_finished-started,3),
            'mirror_seconds':round(time.perf_counter()-build_finished,3),
            'total_cpu_seconds':round(time.process_time()-cpu_started,3),
            'reconciliation':getattr(sync_full_ledger,'LAST_RECONCILIATION_METRICS',{})}
        state['settlement_diagnostics']=settlement_diagnostics(rows,env.identity,
            state['performance']['reconciliation'].get('history_latest_ms',0))
        atomic('ledger_sync_status.json',state);return state
    except Exception as exc:
        previous=load('ledger_sync_status.json',{})
        atomic('ledger_sync_status.json',{**previous,'status':'error','last_error_at':time.time(),'error':type(exc).__name__})
        raise

if __name__=='__main__':
    result=sync_once()
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['status']=='ok' else 1)
