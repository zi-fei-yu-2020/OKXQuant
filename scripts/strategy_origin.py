"""Strategy attribution from exact opening-fill -> submitted order -> decision IDs.
Never infer a strategy from long/short direction or from the realized PnL.
"""
import json,sqlite3
from pathlib import Path


def index(scope):
    from scripts.strategy_evidence import DB_PATH
    try:
        with sqlite3.connect(Path(DB_PATH).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5) as db:
            rows=db.execute("SELECT payload FROM events WHERE scope=? AND kind='entry_submission' ORDER BY at DESC LIMIT 2000",(scope,)).fetchall()
            result={}
            for (raw,) in rows:
                submit=json.loads(raw);plan=submit.get('plan') or {};did=plan.get('decision_id')
                if not did:continue
                response=submit.get('response') or []
                if isinstance(response,dict):response=response.get('data') or [response]
                if isinstance(response,dict):response=[response]
                ids=[str(x['ordId']) for x in response if isinstance(x,dict) and x.get('ordId') and str(x.get('sCode','0'))=='0']
                if not ids:continue
                row=db.execute("SELECT payload FROM events WHERE id=? AND scope=? AND kind='decision'",(did,scope)).fetchone()
                if not row:continue
                record=json.loads(row[0]);d=record.get('decision',{});chosen=next((p for p in (d.get('entry_plans') or {}).get('plans',[]) if p.get('id')==d.get('candidate_id')),None)
                setup=chosen.get('setup') if chosen else 'model_independent'
                version=chosen.get('version') if chosen else d.get('candidate_origin')
                horizon=(chosen.get('horizon') if chosen else d.get('horizon')) or ('scalp' if setup=='model_independent' else 'swing')
                title={'pullback_reclaim':'趋势回踩' if version in {'closed-candle-plans-v3','closed-candle-plans-v4'} else '回收反弹（旧规则）',
                       'closed_range_breakout':'收盘突破','scalp_breakout_1m':'程序1分突破','scalp_pullback_1m':'程序1分回踩','model_independent':'模型独立方案'}.get(setup,'已关联模型方案')
                for oid in ids:result[oid]={'strategy':title,'strategy_evidence':'opening_fill_order_decision_link','decision_id':did,'candidate_id':d.get('candidate_id'),'setup':setup,'strategy_type':setup,'horizon':horizon,'candidate_version':version,'strategy_version':record.get('strategy_version'),'opening_features':record.get('features',{}),'news_snapshot':record.get('news_snapshot',{}),'instId':plan.get('instId'),'side':plan.get('side')}
            return result
    except (OSError,ValueError,TypeError,sqlite3.Error):return {}


def resolve(history,archive,origins,reconciliation=None):
    side='long' if history.get('direction',history.get('posSide'))=='long' else 'short'
    result={'strategy':'持仓来源待确认','strategy_evidence':'unlinked','source_status':'external_or_unlinked','horizon':'unknown','strategy_type':'unknown'}
    # Reuse complete lifecycle fill/fee reconciliation. Timestamp proximity alone
    # cannot prove which opening order belongs to a position.
    verified=reconciliation or {}
    if verified.get('status')!='verified':return result
    ids=verified.get('opening_order_ids')
    if not isinstance(ids,list) or not ids:return result
    # A verified exchange opening order without a matching local decision is
    # still an automatic OKXQuant order, not an external/manual holding.
    if len(ids)==1 and ids[0] not in origins:
        return {'strategy':'系统自动开仓（决策证据缺失）','strategy_evidence':'automatic_entry_unlinked','source_status':'automatic_unlinked','decision_id':None,'candidate_id':None,'horizon':'unknown','strategy_type':'unknown','opening_order_ids':ids}
    if len(set(ids))!=1:
        return {'strategy':'多次入场 · '+('多' if side=='long' else '空'),'strategy_evidence':'multiple_opening_orders','source_status':'automatic_unlinked','horizon':'unknown','strategy_type':'unknown'}
    source=origins.get(ids[0])
    if not source or source['instId']!=history.get('instId') or source['side']!=side:return result
    return {**source,'source_status':'linked','strategy':source['strategy']+' · '+('多' if side=='long' else '空')}
