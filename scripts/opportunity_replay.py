"""Read-only, same-frame comparison. No model calls, orders, or future-price labels.
Run on a COPY of evidence DB, or a readonly SQLite URI. Outputs opportunity
coverage, not simulated profit, fills, or proof that rejected entries had edge.
"""
import argparse,json,sqlite3
from collections import Counter
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.entry_candidates import catalog
from scripts.entry_opportunities import scan,VERSION
from scripts.risk_policy import Policy


def compare(records, policy=None):
    policy=policy or vars(Policy());out=[];seen=set()
    for record in records:
        p=record.get('features') or {};key=(p.get('instId'),p.get('data_as_of'))
        if key in seen:continue
        seen.add(key)
        baseline=catalog(p,policy);shadow=scan(p,policy)
        out.append({'instrument':p.get('instId'),'as_of':p.get('data_as_of'),
            'baseline_count':len(baseline['plans']),'shadow_count':shadow.get('ready_count',0),
            'baseline_error':baseline.get('error'),'shadow_error':shadow.get('error'),
            'baseline_reasons':dict(Counter(c['reason'] for c in baseline.get('checks',[]))),
            'shadow_states':dict(Counter(o['state'] for o in shadow['opportunities'])),
            'shadow_reasons':dict(Counter(c['reason'] for c in shadow['checks'])),
            'old_model_action':record.get('decision',{}).get('model_action'),
            'old_final_status':record.get('decision',{}).get('decision_status'),
            'generation_latency_seconds':max(0,(record.get('generated_at_ms',0)-record.get('as_of_ms',0))/1000)})
    return {'version':VERSION,'mode':'shadow','read_only':True,'risk_policy':policy,
        'frames':len(out),'baseline_candidates':sum(r['baseline_count'] for r in out),
        'shadow_candidates':sum(r['shadow_count'] for r in out),
        'unknown_frames':sum(bool(r['baseline_error'] or r['shadow_error']) for r in out),
        'no_future_data':True,'outcome_evaluation':'not_performed','auto_promote':False,'items':out}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--db',type=Path,required=True)
    ap.add_argument('--scope',required=True);ap.add_argument('--version',required=True)
    ap.add_argument('--limit',type=int,default=200);a=ap.parse_args()
    if not 1<=a.limit<=5000:ap.error('limit must be 1..5000')
    with sqlite3.connect(a.db.resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute("SELECT payload FROM events WHERE scope=? AND kind='decision' AND json_extract(payload,'$.strategy_version')=? ORDER BY at DESC LIMIT ?",(a.scope,a.version,a.limit)).fetchall()
    result=compare([json.loads(r[0]) for r in rows]);result['source_version']=a.version
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
