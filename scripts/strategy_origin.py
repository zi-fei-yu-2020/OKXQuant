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
                from scripts.execution_replay import geometry
                entry_geometry=plan.get('entry_geometry') or geometry(chosen,plan)
                setup=chosen.get('setup') if chosen else 'model_independent'
                version=chosen.get('version') if chosen else d.get('candidate_origin')
                horizon=(chosen.get('horizon') if chosen else d.get('horizon')) or ('scalp' if setup=='model_independent' else 'swing')
                decision_horizon=horizon
                execution_horizon=plan.get('horizon')
                if execution_horizon in {'scalp','swing'}:horizon=execution_horizon
                title={'pullback_reclaim':'趋势回踩' if version in {'closed-candle-plans-v3','closed-candle-plans-v4','closed-candle-plans-v5'} else '回收反弹（旧规则）',
                       'closed_range_breakout':'收盘突破','scalp_breakout_1m':'程序1分突破','scalp_pullback_1m':'程序1分回踩','scalp_reversal_1m':'程序1分转向','scalp_range_reversion_1m':'\u7a0b\u5e8f1\u5206\u533a\u95f4\u53cd\u8f6c','scalp_trend_pause_reclaim_1m':'\u7a0b\u5e8f1\u5206\u8d8b\u52bf\u6574\u7406\u56de\u6536','model_independent':'模型独立方案'}.get(setup,'已关联模型方案')
                for oid in ids:result[oid]={'strategy':title,'strategy_evidence':'opening_fill_order_decision_link','decision_id':did,'candidate_id':d.get('candidate_id'),'setup':setup,'strategy_type':setup,'horizon':horizon,'decision_horizon':decision_horizon,'execution_horizon':execution_horizon,'strategy_engine':plan.get('entry_engine') or 'ai_trader','candidate_version':version,'execution_cost_model':plan.get('cost_model'),'selection_research':plan.get('selection_research'),'strategy_version':record.get('strategy_version'),'opening_features':record.get('features',{}),'news_snapshot':record.get('news_snapshot',{}),'instId':plan.get('instId'),'side':plan.get('side')}
                for oid in ids:result[oid]['entry_geometry']=entry_geometry
            receipts=db.execute("SELECT id,payload FROM events WHERE scope=? AND kind='execution_receipt' ORDER BY at DESC LIMIT 10000",(scope,)).fetchall()
            markouts=db.execute("SELECT payload FROM events WHERE scope=? AND kind='post_fill_markout' ORDER BY at DESC LIMIT 50000",(scope,)).fetchall()
            receipt_map={}
            for receipt_id,raw in receipts:
                item=json.loads(raw);oid=str(item.get('order_id') or '')
                if oid:receipt_map.setdefault(oid,[]).append({**item,'receipt_id':receipt_id})
            markout_map={}
            for (raw,) in markouts:
                item=json.loads(raw);markout_map.setdefault(item.get('receipt_id'),[]).append(item)
            for oid,value in result.items():
                linked=receipt_map.get(oid,[])
                value['execution_receipts']=linked
                value['post_fill_markout']=[sample for item in linked for sample in markout_map.get(item.get('receipt_id'),[])]
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
    # Exchange fills prove an opening order, not which client submitted it.
    # Missing local submission/decision evidence must never imply automation.
    if len(ids)==1 and ids[0] not in origins:
        return {**result, 'opening_order_ids': ids}
    if len(set(ids))!=1:
        return {'strategy':'多次入场 · '+('多' if side=='long' else '空'),'strategy_evidence':'multiple_opening_orders','source_status':'external_or_unlinked','horizon':'unknown','strategy_type':'unknown'}
    source=origins.get(ids[0])
    if not source or source['instId']!=history.get('instId') or source['side']!=side:return result
    return {**source,'source_status':'linked','strategy':source['strategy']+' · '+('多' if side=='long' else '空')}
