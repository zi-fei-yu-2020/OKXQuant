#!/usr/bin/env python3
"""Opt-in DEMO-only minute strategy. No LLM call and no blind order retry.
Both this runner and legacy AI use the same durable entry/risk gateway and writer lock.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT/'scripts'):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from scripts import strategy_evidence as evidence
from scripts import entry_candidates, trading_prompt
from scripts.risk_policy import load_policy
from scripts.trade_lock import writer

CONFIG=ROOT/'data'/'demo_scalp.json'


def enabled(env):
    if env.mode != 'demo': return False
    from scripts.okx_runtime import _load_dotenv
    if _load_dotenv().get('OKXQUANT_AUTOTRADE_ENABLED','1') != '1': return False
    try: cfg=json.loads(CONFIG.read_text(encoding='utf8'))
    except (OSError,ValueError): return False
    return cfg.get('enabled') is True and cfg.get('version') == 'demo-scalp-v2'


def chosen_entries(packages, policy):
    candidates=[]; diagnostics=[]
    for p in packages:
        p['strategy_engine']='demo_scalp_v2'
        result=entry_candidates.catalog(p, vars(policy))
        diagnostics.append({'instrument':p['instId'],'checks':result['checks'],'error':result.get('error'),
                            'candidate_count':len(result['plans'])})
        for plan in result['plans']:
            candidates.append((p, plan))
    # A deterministic rank, not an empirical win probability. Rank BEFORE submission.
    candidates.sort(key=lambda item:(-item[1]['net_rr'],item[0]['instId'],item[1]['id']))
    return candidates,diagnostics


def materialize(package, plan, now, policy=None):
    proposal={'action':plan['action'],'candidate_id':plan['id'],'confidence':0,
              'summary_reason':'程序短线：5M结构确认后，1M收盘触发，按费用后盈亏比择优',
              'counter_evidence_status':'none_observed','counter_evidence':[],
              'uncertainty':'模拟盘实验：目标为波动率投影，存在假突破、滑点和信号失效风险'}
    checked=trading_prompt.candidate(package,proposal,trading_prompt.facts_for(package),risk_contract=vars(policy) if policy else None)
    if checked.get('contract_valid') is not True: raise ValueError(checked.get('validation_reason','Invalid minute candidate'))
    checked.update(contract_version=trading_prompt.VERSION,entry_plans=entry_candidates.catalog(package,vars(policy) if policy else None),
                   horizon='scalp',strategy_engine='demo_scalp_v2',strategy_mode=plan['strategy_mode'],
                   valid_until=min(package['data_as_of']+60,now+60))
    return {'instId':package['instId'],'name':package.get('name'),
            'data_as_of':package['data_as_of'],'position_basis':{'side':None,'size':0},'decision':checked}


def run(*, observe_only=False):
    from scripts.okx_runtime import freeze_environment, unfreeze_environment
    import ai_factor_trader as trader
    import public_market as market
    from scripts.ai_brain_trader import fetch_single_instrument_package
    from scripts.instrument_pool import load_instruments
    from scripts.instrument_support import pool_support
    from scripts.news_connection import load_strategy_snapshot
    env=freeze_environment()
    try:
        if not enabled(env): return {'status':'disabled','mode':env.mode}
        execution_env=trader.freeze_okx_environment()
        if execution_env.identity!=env.identity or execution_env.mode!='demo':
            raise ValueError('DEMO execution binding changed')
        now=time.time(); anchor=int(now)//60
        # Wait for the just-closed minute to be published. Next scheduler tick re-evaluates.
        if now%60<3: return {'status':'awaiting_closed_minute'}
        market.begin_signal_frame(now)
        items=load_instruments(); support=pool_support(items,env.mode,refresh=True)
        tradable=[i for i in items if support['items'].get(i['instId'],{}).get('can_open')]
        with ThreadPoolExecutor(max_workers=4) as pool:
            packages=list(pool.map(fetch_single_instrument_package,tradable))
        news=load_strategy_snapshot(ROOT/'data'/'news_sentiment.json',[p['instId'].split('-')[0] for p in packages])
        for p in packages:
            p['environment_support']=support['items'][p['instId']];p['news_snapshot']=news
        policy=load_policy(); candidates,diagnostics=chosen_entries(packages,policy)
        result={'status':'observed' if observe_only else 'evaluated','engine':'demo_scalp_v2',
                'at':now,'candidate_count':len(candidates),'checks':diagnostics,'selected':None}
        if observe_only: return result
        with writer(timeout=5):
            # Idempotent minute claim before any submission, survives process restart.
            key='demo-scalp-round:'+hashlib.sha256((env.identity+':'+str(anchor)).encode()).hexdigest()
            with evidence.connection() as db:
                if db.execute('SELECT 1 FROM events WHERE id=?',(key,)).fetchone():
                    return {'status':'already_evaluated','minute':anchor}
            evidence.append(env.identity,'demo_scalp_round',{'minute':anchor,'at':now},key)
            if not enabled(env): return {'status':'disabled'}
            if time.time()-now >= 60:
                result.update(status='frame_expired')
                evidence.best_effort(env.identity,'demo_scalp_cycle',result)
                return result
            from okxquant_backend.account_connections import assert_current
            assert_current(env)
            circuit,reason=trader.is_circuit_breaker_active()
            if circuit:
                result.update(status='risk_blocked',reason=reason)
            else:
                ok,positions,error=trader.query_positions()
                pending=trader.run_cmd_result(trader.okx_private_command('okx swap orders --json'),timeout=10)
                if not ok or not pending['ok'] or not isinstance(pending.get('data'),list):
                    result.update(status='account_unavailable')
                else:
                    occupied={p['instId'] for p in positions if abs(float(p.get('pos') or 0))>0}
                    occupied.update(o['instId'] for o in pending['data'])
                    limits=trader.execution_limits()
                    eligible=[(p,q) for p,q in candidates if p['instId'] not in occupied]
                    if len(occupied)>=limits['max_positions']:
                        result.update(status='position_limit')
                    elif eligible:
                        p,plan=eligible[0]; result['selected']={'instrument':p['instId'],'candidate_id':plan['id'],'net_rr':plan['net_rr']}
                        row=materialize(p,plan,time.time(),policy)
                        cache={p['instId']:row}
                        evidence.record_decisions(env.identity,cache,[p],'program:demo-scalp-v2',
                            hashlib.sha256(b'demo-scalp-v2').hexdigest(),time.time(),news_snapshot=news)
                        # Gateway calculates exact contract size from stop risk; request is only a ceiling.
                        item=next(i for i in items if i['instId']==p['instId'])
                        budget=min(float(item.get('risk_per_trade_usd',15)),15.)
                        ct=float(item['ctVal'])
                        unit=ct*(abs(plan['entry_price']-plan['stop_loss_price'])+plan['entry_price']*(policy.maker_fee+policy.taker_fee+policy.slippage))
                        requested=budget/unit
                        side='long' if plan['action']=='BUY_LONG' else 'short'
                        accepted,detail=trader.submit_protected_limit_order(p['instId'],'buy' if side=='long' else 'sell',
                            side,requested,plan['entry_price'],plan['take_profit_price'],plan['stop_loss_price'],
                            risk_budget_usdt=budget,decision_id=row['decision_id'],decision_at=p['data_as_of'],
                            allow_demo_translation=False,horizon='scalp')
                        result.update(status='submitted' if accepted else 'execution_rejected',detail=detail,decision_id=row['decision_id'])
            evidence.best_effort(env.identity,'demo_scalp_cycle',result)
            from scripts.ledger_monitor import atomic
            atomic('demo_scalp_status.json',result)
        return result
    finally:
        trader.unfreeze_okx_environment()
        unfreeze_environment()

if __name__=='__main__':
    print(json.dumps(run(observe_only='--observe' in sys.argv),ensure_ascii=False))
