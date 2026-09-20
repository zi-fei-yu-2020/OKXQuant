"""Post-cycle, account-scoped shadow observations using already fetched public bars.
No HTTP, credentials, LLM, trade imports or order operations. This is NOT a fills/PnL backtest.
"""
from collections import Counter
from contextlib import closing
from copy import deepcopy
import hashlib,json,math,sqlite3,time
from pathlib import Path
from scripts.entry_candidates import verified_bars
from scripts.scalp_ranking import VERSION as RANK_VERSION

VERSION='scalp-research-v2'
DB_PATH=Path(__file__).resolve().parents[1]/'data'/'scalp_research.db'
WIDTH=60000;HORIZONS=(15,30,60);MAX_CAPTURE=24;MAX_PENDING=2000
SCHEMA='''CREATE TABLE IF NOT EXISTS frames(scope TEXT NOT NULL,frame INTEGER NOT NULL,at REAL NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scope,frame));
CREATE TABLE IF NOT EXISTS cases(scope TEXT NOT NULL,id TEXT NOT NULL,inst TEXT NOT NULL,created REAL NOT NULL,status TEXT NOT NULL,first_touch TEXT,setup TEXT,version TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scope,id));
CREATE INDEX IF NOT EXISTS research_pending ON cases(scope,status,created);
'''


def encode(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def make_case(scope,package,plan,metric,observed_at):
    # Only the next complete minute can be assessed from OHLC. No assumed fill
    # at the earlier candle close and no invented price path in the entry minute.
    if isinstance(observed_at,bool) or not math.isfinite(observed_at) or observed_at<0:
        raise ValueError('Invalid shadow observation time')
    born=int(observed_at*1000)
    # Round the original instant, not truncated milliseconds: even a fraction
    # of a millisecond after a boundary excludes that partial minute.
    start=math.ceil(observed_at/(WIDTH/1000))*WIDTH
    identity=hashlib.sha256(encode([scope,VERSION,plan['id'],born]).encode()).hexdigest()[:32]
    return {'id':identity,'instrument':package['instId'],'candidate_id':plan['id'],'setup':plan['setup'],
            'action':plan['action'],'entry':plan['entry_price'],'stop':plan['stop_loss_price'],'target':plan['take_profit_price'],
            'created_ms':born,'observation_start_ms':start,'end_ms':start+60*WIDTH,'last_close_ms':start,
            'observed_bars':0,'data_complete':True,'status':'tracking','first_touch':None,
            'target_touch_ms':None,'stop_touch_ms':None,'mfe_price':0.,'mae_price':0.,'horizons':{},
            'ranking':deepcopy(metric),'version':VERSION,'ranking_version':RANK_VERSION,
            'order_authorized':False,'fill_assumed':False,'entry_partial_minute_excluded':True}


def advance(case,bars,now_ms):
    out=deepcopy(case)
    if out['status']!='tracking':return out
    side=1 if out['action']=='BUY_LONG' else -1
    end=out['end_ms'];start=out['observation_start_ms']
    for bar in sorted(bars,key=lambda row:row['close_ms']):
        at=int(bar['close_ms'])
        if at!=bar['close_ms'] or at%WIDTH:raise ValueError('Invalid shadow candle time')
        if not out['last_close_ms']<at<=min(now_ms,end):continue
        if at!=out['last_close_ms']+WIDTH:
            out['data_complete']=False
            if out['first_touch'] is None:out['first_touch']='unknown_data_gap'
        high=float(bar['high']);low=float(bar['low'])
        if not math.isfinite(high+low) or not 0<low<=high:raise ValueError('Invalid shadow candle')
        out['last_close_ms']=at;out['observed_bars']+=1
        out['mfe_price']=max(out['mfe_price'],(high-out['entry']) if side==1 else (out['entry']-low))
        out['mae_price']=max(out['mae_price'],(out['entry']-low) if side==1 else (high-out['entry']))
        target=high>=out['target'] if side==1 else low<=out['target']
        stop=low<=out['stop'] if side==1 else high>=out['stop']
        if target and out['target_touch_ms'] is None:out['target_touch_ms']=at
        if stop and out['stop_touch_ms'] is None:out['stop_touch_ms']=at
        if out['first_touch'] is None and (target or stop):
            out['first_touch']='ambiguous_same_bar' if target and stop else 'target_first' if target else 'stop_first'
        for minutes in HORIZONS:
            boundary=start+minutes*WIDTH
            if at>=boundary and str(minutes) not in out['horizons']:
                complete=out['data_complete'] and out['observed_bars']>=minutes and at==boundary
                out['horizons'][str(minutes)]={'data_complete':complete,
                    'target_observed':True if out['target_touch_ms'] is not None and out['target_touch_ms']<=boundary else False if complete else None,
                    'stop_observed':True if out['stop_touch_ms'] is not None and out['stop_touch_ms']<=boundary else False if complete else None,
                    'mfe_observed_price':out['mfe_price'] if at==boundary else None,
                    'mae_observed_price':out['mae_price'] if at==boundary else None}
    if out['last_close_ms']==end:
        out['status']='complete' if out['data_complete'] and out['observed_bars']==60 else 'incomplete'
        if out['first_touch'] is None:out['first_touch']='neither_observed'
    elif now_ms>=end+2*WIDTH:
        out.update(status='incomplete',data_complete=False)
        if out['first_touch'] is None:out['first_touch']='unknown_data_gap'
    return out


def observe(scope,packages,candidates,result,*,path=None,now=None):
    path=Path(path or DB_PATH);now=time.time() if now is None else now
    frame=int(result['at'])//60;frames={}
    for p in packages:
        try:frames[p['instId']]=verified_bars(p,'1M')
        except (ValueError,TypeError,KeyError,OverflowError):pass
    path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path,timeout=.1)) as db,db:
        db.executescript(SCHEMA);db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM frames WHERE scope=? AND frame=?',(scope,frame)).fetchone():
            return {'status':'already_observed','frame':frame,'order_authorized':False}
        pending=db.execute("SELECT id,payload FROM cases WHERE scope=? AND status='tracking' ORDER BY created LIMIT ?",(scope,MAX_PENDING)).fetchall()
        completed=0
        for identity,raw in pending:
            case=json.loads(raw);updated=advance(case,frames.get(case['instrument'],[]),int(now*1000))
            db.execute('UPDATE cases SET status=?,first_touch=?,payload=? WHERE scope=? AND id=?',
                (updated['status'],updated['first_touch'],encode(updated),scope,identity))
            completed+=updated['status']!='tracking'
        ranking=result.get('ranking') or {};metrics=ranking.get('metrics') or {}
        # Preserve both old and new top choices even with an unusually large pool.
        by_id={q['id']:(p,q) for p,q in candidates}
        selection=result.get('selection_compare') or {}
        priorities=[selection.get('legacy_candidate_id'),selection.get('new_candidate_id')]+(ranking.get('legacy_order') or [])[:1]+(ranking.get('new_order') or [])[:1]+[q['id'] for _,q in candidates]
        priority=list(dict.fromkeys(x for x in priorities if x is not None))[:MAX_CAPTURE]
        born=float(result.get('ranking_observed_at',result['at']))
        if not math.isfinite(born) or born>now+.001:raise ValueError('Future shadow observation')
        captured=[]
        for cid in priority:
            if cid not in by_id:continue
            p,q=by_id[cid];case=make_case(scope,p,q,metrics.get(cid,{}),born)
            db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,?,?,?,?,?,?,?)',
                (scope,case['id'],case['instrument'],born,'tracking',None,case['setup'],VERSION,encode(case)))
            captured.append(case['id'])
        record={'version':VERSION,'frame':frame,'observed_at':born,'candidate_count':len(candidates),
                'captured_count':len(captured),'capture_truncated':len(candidates)>len(captured),
                'ranking':ranking,'eligible_selection':result.get('selection_compare'),
                'execution_status':result.get('status'),'decision_id':result.get('decision_id'),
                'selected':result.get('selected'),'cases':captured,'counterfactual_order_sent':False}
        db.execute('INSERT INTO frames VALUES(?,?,?,?)',(scope,frame,now,encode(record)))
    return {'status':'recorded','version':VERSION,'frame':frame,'captured':len(captured),
            'finished_cases':completed,'tracking_processed':len(pending),'order_authorized':False}


def public_status(scope,*,path=None):
    path=Path(path or DB_PATH)
    if not path.exists():return {'status':'not_started','order_authorized':False,'version':VERSION}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)) as db:
            states=dict(db.execute('SELECT status,count(*) FROM cases WHERE scope=? GROUP BY status',(scope,)))
            outcomes=dict(db.execute("SELECT first_touch,count(*) FROM cases WHERE scope=? AND status='complete' GROUP BY first_touch",(scope,)))
            latest=db.execute('SELECT payload FROM frames WHERE scope=? ORDER BY at DESC LIMIT 1',(scope,)).fetchone()
            total=db.execute('SELECT count(*) FROM frames WHERE scope=?',(scope,)).fetchone()[0]
            recent=db.execute('SELECT payload FROM frames WHERE scope=? ORDER BY at DESC LIMIT 200',(scope,)).fetchall()
            comparison={}
            for (raw,) in recent:
                frame=json.loads(raw);selected=frame.get('eligible_selection') or {};ranking=frame.get('ranking') or {}
                basis='same_eligible_pool' if selected.get('same_eligible_pool') else 'candidate_pool_only'
                choices={'legacy':selected.get('legacy_candidate_id') or next(iter(ranking.get('legacy_order') or []),None),
                         'new':selected.get('new_candidate_id') or next(iter(ranking.get('new_order') or []),None)}
                if not any(choices.values()):continue
                group=comparison.setdefault(basis,{'frames':0,'different_choices':0,'legacy':{},'new':{}})
                group['frames']+=1;group['different_choices']+=choices['legacy']!=choices['new']
                for rule,cid in choices.items():
                    bucket=group[rule]
                    key=hashlib.sha256(encode([scope,frame['version'],cid,int(frame['observed_at']*1000)]).encode()).hexdigest()[:32]
                    stored=db.execute('SELECT payload FROM cases WHERE scope=? AND id=?',(scope,key)).fetchone()
                    case=json.loads(stored[0]) if stored else {};status=case.get('status','not_captured')
                    bucket[status]=bucket.get(status,0)+1
                    if status=='complete':
                        outcome=case['first_touch'];counts=bucket.setdefault('complete_first_touch',{})
                        counts[outcome]=counts.get(outcome,0)+1
                    windows=bucket.setdefault('horizons',{})
                    for window,h in case.get('horizons',{}).items():
                        if not h.get('data_complete'):continue
                        metric=windows.setdefault(window,{'valid_samples':0,'target_observed':0,'stop_observed':0})
                        metric['valid_samples']+=1;metric['target_observed']+=bool(h['target_observed']);metric['stop_observed']+=bool(h['stop_observed'])
        return {'status':'ready','version':VERSION,'frames':total,'cases':states,'complete_first_touch':outcomes,
                'latest':json.loads(latest[0]) if latest else None,'order_authorized':False,
                'selection_comparison':comparison,'comparison_window_frames':len(recent),'comparison_frame_limit':200,
                'interpretation':'bar_observation_not_filled_trade_or_profit_backtest'}
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):return {'status':'unavailable','version':VERSION,'order_authorized':False}
