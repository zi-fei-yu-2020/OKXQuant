"""Durable research/entry evidence. No network or trading side effects on import."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import time
import uuid

DB_PATH = Path(__file__).resolve().parents[1] / 'data' / 'strategy_evidence.db'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL, at REAL NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_scope ON events(scope,kind,at);
CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY, scope TEXT NOT NULL, decision_id TEXT NOT NULL, inst_id TEXT NOT NULL, state TEXT NOT NULL, at REAL NOT NULL, payload TEXT NOT NULL, UNIQUE(scope,decision_id,inst_id));
CREATE TABLE IF NOT EXISTS equity_state(scope TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS capital_pool_state(scope TEXT PRIMARY KEY, payload TEXT NOT NULL);
'''

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))

def _scrub(value):
    if isinstance(value, dict):
        return {str(k): _scrub(v) for k,v in value.items() if not any(x in str(k).lower() for x in ('secret','password','passphrase','api_key','authorization','token'))}
    if isinstance(value, list): return [_scrub(v) for v in value]
    return value

@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=5)
    try:
        os.chmod(DB_PATH, 0o600)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript(SCHEMA)
        with db:
            yield db
    finally:
        db.close()

def append(scope, kind, payload, event_id=None):
    clean = canonical(_scrub(payload))
    digest = hashlib.sha256(clean.encode()).hexdigest()
    identity = event_id or uuid.uuid4().hex
    with connection() as db:
        old = db.execute('SELECT digest FROM events WHERE id=?',(identity,)).fetchone()
        if old and old[0] != digest: raise ValueError('Evidence identity collision')
        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',(identity,scope,kind,time.time(),clean,digest))
    return identity

def append_batch(scope, kind, items):
    """Archive a page with one durable transaction rather than one fsync per fill."""
    prepared=[]
    for identity,payload in items:
        clean=canonical(_scrub(payload));prepared.append((identity,clean,hashlib.sha256(clean.encode()).hexdigest()))
    with connection() as db:
        for identity,clean,digest in prepared:
            old=db.execute('SELECT digest FROM events WHERE id=?',(identity,)).fetchone()
            if old and old[0]!=digest:raise ValueError('Evidence identity collision')
            db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',(identity,scope,kind,time.time(),clean,digest))


def best_effort(scope, kind, payload, event_id=None):
    try: return append(scope,kind,payload,event_id)
    except Exception:
        import logging
        logging.getLogger(__name__).error('Evidence unavailable for %s; no write retry added',kind)
        return None

def record_decisions(scope, cache, packages, model, prompt_hash, as_of):
    """Retain features and final decisions, not credentials/raw provider responses."""
    by_id = {p['instId']:p for p in packages}
    for inst, row in cache.items():
        payload = {'schema':1,'model':model,'prompt_hash':prompt_hash,'as_of_ms':int(row.get('data_as_of',as_of)*1000),'generated_at_ms':int(as_of*1000),
                   'instrument':inst,'position_basis':row.get('position_basis',{}),'decision':row.get('decision',{}),'features':by_id.get(inst,{}),
                   'strategy_version':os.getenv('OKXQUANT_BUILD_COMMIT','local-risk-v2'),'counterfactual':False}
        from scripts.execution_profiles import runtime as execution_runtime
        payload['execution_profile_signature']=row.get('execution_profile_signature') or execution_runtime()['signature']
        from scripts.okx_runtime import selected_environment
        env=selected_environment()
        if getattr(env,'connection_id',''):
            payload.update(connection_id=env.connection_id,binding_version=env.binding_version)
        row['decision_id'] = append(scope,'decision',payload)

def begin_intent(scope, decision_id, inst_id, payload):
    """Commit before sending exposure. Duplicate/uncertain decisions cannot be resent."""
    if not decision_id: raise ValueError('New exposure requires a durable decision ID')
    identity = 'okxquant' + uuid.uuid4().hex[:28]
    with connection() as db:
        db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?)',
                   (identity,scope,decision_id,inst_id,'unknown',time.time(),canonical(_scrub(payload))))
    return identity

def finish_intent(identity, state, result=None):
    with connection() as db:
        row = db.execute('SELECT scope FROM intents WHERE id=?',(identity,)).fetchone()
        if row:
            db.execute('UPDATE intents SET state=? WHERE id=?',(state,identity))
    if row: best_effort(row[0],'order_status',{'client_id':identity,'state':state,'result':result or {}})

def unresolved(scope):
    with connection() as db:
        rows = db.execute("SELECT id,payload FROM intents WHERE scope=? AND state IN ('unknown','acknowledged') ORDER BY at",(scope,)).fetchall()
    return [(key,json.loads(payload)) for key,payload in rows]

def export_events(scope, kind=None):
    with connection() as db:
        rows = db.execute('SELECT id,kind,at,payload,digest FROM events WHERE scope=? AND (? IS NULL OR kind=?) ORDER BY at,id',(scope,kind,kind)).fetchall()
    return [{'id':r[0],'kind':r[1],'at':r[2],'payload':json.loads(r[3]),'digest':r[4]} for r in rows]
