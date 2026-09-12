"""Read-only reconciliation inputs and local close-execution journaling.

Never sends, cancels or retries a trade. Normal CLI close does not expose clOrdId;
execution/log correlations are explicitly weaker than exact exchange ID links.
"""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from scripts import strategy_evidence as evidence
from scripts import ledger_monitor

ROOT=Path(__file__).resolve().parents[1]
GATEWAY_DB=ROOT/'data'/'okxquant_gateway.db'
HISTORY_TTL=300
LABELS={'strategy_close':'策略主动平仓','hard_stop':'策略硬止损','oco_unverified':'保护核验失败安全退出',
        'time_exit':'策略时间止损','profit_lock':'策略阶梯锁利','trailing_exit':'策略移动止盈','ai_exit':'AI主动退出','independent_guard':'独立风控安全退出'}
ORDER_FIELDS=('ordId','instId','side','posSide','state','accFillSz','fillTime','uTime','clOrdId','algoId','linkedAlgoOrd','category','source')
ALGO_FIELDS=('algoId','instId','side','posSide','state','ordId','ordIdList','actualSide','actualSz','triggerTime','uTime','slTriggerPx','tpTriggerPx','ordType')


def stamp(value):
    try:return datetime.strptime(str(value)[:19],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
    except ValueError:return 0.


def record_close(env, *, inst_id, side, size, started_at, confirmed_at, reason='strategy_close',position=None,result=None,status='confirmed',attempt_id=None,transport_code=None):
    position=position or {}
    result_rows=result if isinstance(result,list) else [result] if isinstance(result,dict) else []
    payload={'instId':inst_id,'posSide':side,'size':abs(float(size)),'started_at':started_at,'confirmed_at':confirmed_at,
             'reason_code':reason if reason in LABELS else 'strategy_close','status':status,'attempt_id':attempt_id,'transport_code':transport_code,'position_id':str(position.get('posId') or ''),
             'position_created_at':position.get('cTime'),'transport':'existing_cli_close','response_order_ids':[str(r['ordId']) for r in result_rows if isinstance(r,dict) and str(r.get('ordId') or '').isdigit()]}
    identity=evidence.best_effort(env.identity,'close_execution',payload)
    try:
        if status in {'confirmed','flat_observed'}:ledger_monitor.request_refresh('strategy_close')
    except Exception:pass  # A successful close must not become a retryable trade failure.
    return identity


def archive(scope,kind,rows,fields):
    clean=[{k:r.get(k) for k in fields} for r in rows if isinstance(r,dict)]
    pairs=[('close-receipt:'+scope+':'+hashlib.sha256(evidence.canonical([kind,r]).encode()).hexdigest(),r) for r in clean]
    if not pairs:return
    try:evidence.append_batch(scope,kind,pairs)
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Close receipt archive unavailable; no trading operation retried')


def read_events(scope,kind,limit=5000):
    if not evidence.DB_PATH.exists():return []
    try:
        with sqlite3.connect(evidence.DB_PATH.resolve().as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute('SELECT id,at,payload FROM events WHERE scope=? AND kind=? ORDER BY at DESC LIMIT ?',(scope,kind,limit)).fetchall()
        return [{'id':r[0],'at':r[1],**json.loads(r[2])} for r in rows]
    except (OSError,sqlite3.Error,ValueError,TypeError):return []


def action_reason(action):
    """Recognize only the program's completed-close phrases, not generic model opinions."""
    if not isinstance(action,str):return None
    patterns=(('hard_stop',r'触发硬止损.+并确认平仓'),('time_exit',r'时间止损平仓释放保证金|时间退出，交易所确认平仓'),
              ('profit_lock',r'触发阶梯动态锁利平仓|触发浮盈保护.+并确认平仓'),('trailing_exit',r'动能见顶.*移动止盈|高点回撤止盈|移动止盈.*确认平仓|动能止盈已确认'),
              ('oco_unverified',r'(?:已安全平仓|已按安全策略确认平仓)'),('ai_exit',r'AI高置信度整仓退出:'))
    if any(word in action for word in ('平仓失败','退出失败','确认=False','未获交易所确认')):return None
    for code,pattern in patterns:
        if re.search(pattern,action):return code
    return None


def actions_to_events(actions,scope,start,end,origin):
    result=[]
    for action in actions:
        if isinstance(action,dict):
            if action.get('closed') is True and action.get('instrument'):
                result.append({'scope':scope,'instId':action['instrument'],'started_at':start,'confirmed_at':end,
                               'reason_code':'independent_guard','status':'confirmed','evidence_kind':origin})
            continue
        code=action_reason(action)
        inst=re.match(r'\[([A-Z0-9]+)\]',str(action))
        if code and inst:
            result.append({'scope':scope,'instId':inst[1]+'-USDT-SWAP','started_at':start,'confirmed_at':end,
                           'reason_code':code,'status':'confirmed','evidence_kind':origin})
    return result


def legacy_job_events(rows,scope):
    result=[]
    for row in rows:
        detail=str(row.get('detail') or '')
        if ' / '+scope not in detail:continue  # Do not attribute unscoped text from another account/environment.
        start=stamp(row.get('started_at'));end=stamp(row.get('finished_at'))
        if not start or not start<=end<=start+900:continue
        line=detail[detail.rfind('巡检完成'):] if '巡检完成' in detail else ''
        if '动作:' not in line:continue
        actions=line.split('动作:',1)[1].split(', ')
        result.extend(actions_to_events([a.strip() for a in actions],scope,start,end,'scoped_scheduler_log'))
    return result


def local_close_events(scope):
    result=read_events(scope,'close_execution',2000)
    for row in result:row.update(scope=scope,evidence_kind='close_execution_journal')
    journal = list(result)
    for row in read_events(scope,'execution_cycle',2000):
        start=stamp(row.get('timestamp'));end=float(row.get('at') or 0)
        if start and 0<=end-start<=900:result.extend(actions_to_events(row.get('actions',[]),scope,start,end,'scoped_execution_cycle'))
    for row in read_events(scope,'position_guard',3000):
        if row.get('observe_only'):continue
        end=float(row.get('at') or 0)
        result.extend(actions_to_events(row.get('actions',[]),scope,end-90,end,'scoped_guard_record'))
    try:
        if GATEWAY_DB.exists():
            with sqlite3.connect(GATEWAY_DB.resolve().as_uri()+'?mode=ro',uri=True) as db:
                db.row_factory=sqlite3.Row
                rows=[dict(r) for r in db.execute("SELECT started_at,finished_at,detail FROM job_runs WHERE job_name='trader' AND status='success' ORDER BY id DESC LIMIT 2000")]
            result.extend(legacy_job_events(rows,scope))
    except (OSError,sqlite3.Error):pass
    # An explicit attempt journal outranks reconstructed text. In particular,
    # flat-after-unknown transport must not become "our close confirmed" because
    # an outer guard logged that the position is now absent.
    def overlaps(event, direct):
        return (event.get('instId') == direct.get('instId')
                and float(event.get('started_at') or 0) <= float(direct.get('confirmed_at') or direct.get('started_at') or 0) + 1
                and float(event.get('confirmed_at') or 0) >= float(direct.get('started_at') or 0) - 1)
    return journal + [event for event in result[len(journal):] if not any(overlaps(event, direct) for direct in journal)]


def load_inputs(env,orders):
    # Only completed closing-direction receipts are archived, not every open/canceled entry.
    cycle_start = 0.0
    try:
        from datetime import datetime, timezone, timedelta
        initial = ledger_monitor.load('account_initial_state.json', {})
        cycle_start = datetime.strptime(initial.get('reset_time','1970-01-01 00:00:00'), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000
    except (TypeError, ValueError, OverflowError):
        cycle_start = 0.0
    closing=[r for r in orders if isinstance(r,dict) and float(r.get('uTime') or r.get('cTime') or 0) >= cycle_start and ((r.get('posSide')=='long' and r.get('side')=='sell') or (r.get('posSide')=='short' and r.get('side')=='buy') or str(r.get('reduceOnly','')).lower()=='true')]
    archive(env.identity,'close_order_receipt',closing,ORDER_FIELDS)
    scope_key=hashlib.sha256(env.identity.encode()).hexdigest()[:16]
    name='close_history_refresh_'+scope_key+'.json';refresh=ledger_monitor.load(name,{})
    has_algo=any(r.get('algoId') or str(r.get('source'))=='7' for r in closing)
    if has_algo and getattr(env,'configured',False) and time.time()-float(refresh.get('attempted_at') or 0)>=HISTORY_TTL:
        attempted=time.time();status='ok'
        from scripts.algo_reader import read_algo_history
        for kind in ('oco','conditional'):
            try:archive(env.identity,'close_algo_receipt',read_algo_history(env,ord_type=kind),ALGO_FIELDS)
            except Exception as exc:
                status=type(exc).__name__;break  # No history retry storm; regular ledger reads still work.
        ledger_monitor.atomic(name,{'attempted_at':attempted,'status':status})
    saved=read_events(env.identity,'close_order_receipt')
    return {'orders':orders+saved,'algos':read_events(env.identity,'close_algo_receipt',2000),'executions':local_close_events(env.identity)}
