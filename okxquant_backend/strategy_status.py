"""Admin-only strategy health projection; never requests exchange/model data."""
import json
import sqlite3
from pathlib import Path
from dataclasses import asdict
from scripts.risk_policy import load_policy
from scripts.strategy_evidence import DB_PATH
from scripts.okx_runtime import selected_environment
ROOT=Path(__file__).resolve().parents[1]

def strategy_status():
    env=selected_environment()
    result={'environment':env.mode,'auto_strategy_promotion':False,'risk_policy':asdict(load_policy()),
            'evidence_counts':{},'equity_state':None,'last_guard':None,'unresolved_entries':0,
            'research_status':'insufficient_evidence','memory_candidates':[]}
    if DB_PATH.exists():
        db=sqlite3.connect('file:'+DB_PATH.as_posix()+'?mode=ro',uri=True,timeout=2)
        try:
            result['evidence_counts']=dict(db.execute('SELECT kind,count(*) FROM events WHERE scope=? GROUP BY kind',(env.identity,)).fetchall())
            row=db.execute('SELECT payload FROM equity_state WHERE scope=?',(env.identity,)).fetchone()
            if row:result['equity_state']=json.loads(row[0])
            row=db.execute("SELECT payload FROM events WHERE scope=? AND kind='position_guard' ORDER BY at DESC LIMIT 1",(env.identity,)).fetchone()
            if row:result['last_guard']=json.loads(row[0])
            result['unresolved_entries']=db.execute("SELECT count(*) FROM intents WHERE scope=? AND state IN ('unknown','acknowledged')",(env.identity,)).fetchone()[0]
        finally:db.close()
    for filename,key in [('memory_candidates.json','memory_candidates'),('research_report.json','research_status')]:
        path=ROOT/'data'/filename
        if path.exists():
            payload=json.loads(path.read_text(encoding='utf-8'))
            result[key]=payload.get('candidates',[]) if key=='memory_candidates' else payload.get('status','insufficient_evidence')
    from scripts.memory_registry import view
    memory=view(ROOT/'data',env.identity,admin=True)
    if memory.get('managed'):result['memory_candidates']=memory.get('candidates',[])
    return result
