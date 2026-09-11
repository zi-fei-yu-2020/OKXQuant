"""Fresh read-only preflight, then durable reservation. Caller sends one existing order."""
import json
import time
from scripts import strategy_evidence as evidence
from scripts import risk_policy as risk
from scripts import capital_pool
from scripts.algo_reader import read_algo_orders
from scripts import public_market
from okxquant_backend.okx_trade_service import _request


def reconcile_intents(env):
    pending=evidence.unresolved(env.identity)
    if len(pending)>20: raise risk.RiskRejected('Too many unresolved order intents')
    for client_id,plan in pending:
        try: rows=_request('GET','/api/v5/trade/order',{'instId':plan['instId'],'clOrdId':client_id},env)
        except Exception: raise risk.RiskRejected('Prior entry outcome unknown; read reconciliation required') from None
        if len(rows)!=1: raise risk.RiskRejected('Prior entry not located; no blind resend')
        order=rows[0]; status=str(order.get('state'))
        evidence.best_effort(env.identity,'exchange_order',order,event_id='order:'+env.identity+':'+client_id+':'+str(order.get('uTime',time.time_ns())))
        if status in {'filled','canceled','mmp_canceled'}: evidence.finish_intent(client_id,status,order)
        elif status in {'live','partially_filled'}: evidence.finish_intent(client_id,'pending',order)
        else: raise risk.RiskRejected('Unknown prior order state')


def equity_guard(env, balance, policy):
    with evidence.connection() as db:
        row=db.execute('SELECT payload FROM equity_state WHERE scope=?',(env.identity,)).fetchone()
    previous=json.loads(row[0]) if row else None
    equity=risk.number(balance.get('totalEq'),positive=True)
    at=risk.number(balance.get('uTime'),positive=True)/1000
    flow=0.; complete=previous is None
    if previous:
        if at==previous['at']:
            if abs(equity-previous['equity'])>1e-8: raise risk.RiskRejected('Contradictory equity timestamp')
            previous.setdefault('external_flow_total',0.)
            previous.setdefault('external_flow_origin',previous['at'])
            capital_pool.observe_existing(env,previous,policy,balance=balance)
            if previous['blocked']: raise risk.RiskRejected('Equity drawdown circuit breaker active')
            return previous
        after=None; seen=set()
        for _ in range(10):
            params={'limit':'100'}
            if after: params['after']=after
            rows=_request('GET','/api/v5/account/bills',params,env)
            for bill in rows:
                ident=str(bill.get('billId') or '')
                if not ident: raise risk.RiskRejected('Cash-flow bill has no identity')
                if ident in seen: continue
                seen.add(ident)
                ts=risk.number(bill.get('ts'),positive=True)/1000
                if ts<=previous['at'] or ts>at: continue
                kind=str(bill.get('type'))
                if kind=='1':
                    if bill.get('ccy')!='USDT': raise risk.RiskRejected('Non-USDT external flow requires reconciliation')
                    flow+=risk.number(bill.get('balChg'))
                elif kind not in {'2','8'}:
                    raise risk.RiskRejected('Unclassified account bill; manual flow review required')
            if len(rows)<100 or any(risk.number(b.get('ts'),positive=True)/1000<=previous['at'] for b in rows):
                complete=True; break
            cursor=str(rows[-1].get('billId'))
            if cursor==after: break
            after=cursor
    state=risk.update_equity_state(previous,equity=equity,at=at,cash_flow=flow,complete=complete,policy=policy)
    with evidence.connection() as db:
        db.execute('INSERT OR REPLACE INTO equity_state VALUES (?,?)',(env.identity,evidence.canonical(state)))
    evidence.append(env.identity,'equity',state)
    capital_pool.observe_existing(env,state,policy,balance=balance)
    if state['blocked']: raise risk.RiskRejected('Equity drawdown circuit breaker active; protection remains enabled')
    return state


def prepare(env, *, inst_id, side, entry, stop, take_profit, requested_size, budget, decision_id, decision_at, horizon='swing'):
    from scripts.trade_lock import writer
    with writer(account=env.identity, inst_id=inst_id, side=side):
        return _prepare(env, inst_id=inst_id, side=side, entry=entry, stop=stop, take_profit=take_profit, requested_size=requested_size, budget=budget, decision_id=decision_id, decision_at=decision_at, horizon=horizon)


def _prepare(env, *, inst_id, side, entry, stop, take_profit, requested_size, budget, decision_id, decision_at, horizon='swing'):
    from okxquant_backend.account_connections import assert_current
    assert_current(env)
    if not env.configured: raise risk.RiskRejected('Final risk preflight requires current account static credentials')
    age=time.time()-risk.number(decision_at,positive=True)
    if not 0<=age<=300: raise risk.RiskRejected('Decision is stale or future-dated')
    with evidence.connection() as db:
        recorded=db.execute("SELECT payload FROM events WHERE id=? AND scope=? AND kind='decision'",(decision_id,env.identity)).fetchone()
    if not recorded: raise risk.RiskRejected('Decision evidence not found for this account')
    record=json.loads(recorded[0])
    if getattr(env,'connection_id','') and (record.get('connection_id')!=env.connection_id or record.get('binding_version')!=env.binding_version):
        raise risk.RiskRejected('Account binding changed after decision; fresh inference required')
    decision=record.get('decision',{})
    from scripts.trading_prompt import VERSION
    if decision.get('contract_version')!=VERSION or decision.get('contract_valid') is not True:
        raise risk.RiskRejected('Decision output contract was not validated')
    if time.time() >= risk.number(decision.get('valid_until'),positive=True):
        raise risk.RiskRejected('Candidate validity expired')
    memory_basis=decision.get('memory_publication')
    if isinstance(memory_basis,dict) and memory_basis.get('prompt_hash'):
        from scripts.memory_registry import view as memory_view
        current_memory=memory_view(scope=env.identity)
        if memory_basis.get('scope')!=env.identity or current_memory['prompt_hash']!=memory_basis['prompt_hash']:
            raise risk.RiskRejected('Published memory changed after inference; fresh decision required')
    candidate_id = decision.get('candidate_id')
    if not candidate_id:
        # Compatibility path for validated model-independent proposals. They
        # still go through independent direction, quote, geometry, account and
        # risk checks below; candidate_id is required only for immutable plans.
        from scripts.entry_candidates import validate_independent_direction
        try:
            validate_independent_direction(record.get('features',{}), decision.get('action'))
        except (ValueError,TypeError,KeyError) as exc:
            raise risk.RiskRejected('Final entry direction rejected: '+str(exc)) from None
    expected='BUY_LONG' if side=='long' else 'SELL_SHORT'
    if record.get('instrument')!=inst_id or record.get('decision',{}).get('action')!=expected:
        raise risk.RiskRejected('Decision evidence does not authorize this instrument/direction')
    from scripts.execution_profiles import runtime as execution_runtime, cap_allocation
    active_execution=execution_runtime()
    recorded_execution=record.get("execution_profile_signature")
    if (recorded_execution and recorded_execution!=active_execution["signature"]) or (active_execution["execution"]["id"]=="small300" and not recorded_execution):
        raise risk.RiskRejected("Execution preset changed since inference; require a fresh decision")
    policy=risk.load_policy()
    reconcile_intents(env)
    from pathlib import Path
    cooldown_file=Path(__file__).resolve().parents[1]/'data'/'stop_cooldown.json'
    if cooldown_file.exists():
        cooldowns=json.loads(cooldown_file.read_text(encoding='utf-8'))
        item=cooldowns.get(f'{inst_id}_{side}', {})
        if isinstance(item,dict):
            expires=float(item.get('expires_at',item.get('expires_at_ts',0)) or 0)
            if not expires:
                expires=float(item.get('ts',0) or 0)+int(item.get('cooldown_seconds',1800) or 1800)
            if expires>time.time(): raise risk.RiskRejected('Stop cooldown still active')
    instruments=public_market.get_json('https://www.okx.com/api/v5/public/instruments?instType=SWAP',simulated=env.simulated)['data']
    metadata={i['instId']:i for i in instruments}
    if inst_id not in metadata: raise risk.RiskRejected('Missing exchange instrument metadata')
    # The exchange tick grid, not a stale instrument-pool decimal precision,
    # determines final prices. Program geometry is checked against frozen values
    # after this bounded rounding; risk is recalculated on the same prices sent.
    from decimal import Decimal, ROUND_HALF_EVEN
    tick = risk.linear_metadata(metadata[inst_id])[3]
    def tick_price(value):
        value = risk.number(value, positive=True)
        step = Decimal(str(tick))
        return float((Decimal(str(value))/step).to_integral_value(rounding=ROUND_HALF_EVEN)*step)
    entry, stop, take_profit = [tick_price(v) for v in (entry, stop, take_profit)]
    positions=_request('GET','/api/v5/account/positions',{'instType':'SWAP'},env)
    pending=_request('GET','/api/v5/trade/orders-pending',{'instType':'SWAP'},env)
    balances=_request('GET','/api/v5/account/balance',{},env)
    if len(balances)!=1: raise risk.RiskRejected('Invalid account balance snapshot')
    observed_equity=equity_guard(env,balances[0],policy)
    usdt=next((d for d in balances[0].get('details',[]) if d.get('ccy')=='USDT'),{})
    available=risk.number(usdt.get('availEq') or usdt.get('availBal'),positive=True)
    algos=read_algo_orders(env,priority='risk',force=True) if any(abs(risk.number(p.get('pos') or 0))>0 for p in positions) else []
    portfolio=risk.exposure(positions,pending,algos,metadata,policy)
    existing=[p for p in positions if p.get('instId')==inst_id and abs(risk.number(p.get('pos') or 0))>0]
    basis=record.get('position_basis',{})
    if abs(sum(abs(risk.number(p.get('pos') or 0)) for p in existing)-abs(risk.number(basis.get('size') or 0)))>1e-9:
        raise risk.RiskRejected('Position basis changed since inference; no stale scale/entry reinterpretation')
    if any(p.get('posSide')!=side for p in existing): raise risk.RiskRejected('Opposing/unsupported position appeared during inference')
    if any(p.get('instId')==inst_id and str(p.get('reduceOnly','false')).lower() not in {'true','1'} for p in pending):
        raise risk.RiskRejected('Instrument already has pending entry exposure')
    leverage_rows=_request('GET','/api/v5/account/leverage-info',{'instId':inst_id,'mgnMode':'cross'},env)
    lev=next((p for p in leverage_rows if p.get('posSide') in {side,'net',''}),None)
    if not lev: raise risk.RiskRejected('Actual exchange leverage unavailable')
    ticker=public_market.get_json('https://www.okx.com/api/v5/market/ticker?instId='+inst_id,simulated=env.simulated)['data'][0]
    current=risk.number(ticker.get('last'),positive=True)
    if not 0 <= time.time()*1000-risk.number(ticker.get('ts'),positive=True) <= 15000:
        raise risk.RiskRejected('Final execution quote is stale or future-dated')
    if abs(entry-current)/current>policy.max_entry_distance_pct: raise risk.RiskRejected('Final limit too far from current market')
    if decision.get('candidate_id'):
        from scripts.entry_candidates import validate_live_quote
        try:
            frozen=validate_live_quote(record.get('features',{}),decision['candidate_id'],current,vars(policy))
            tick=risk.number(metadata[inst_id].get('tickSz'),positive=True)
            for key,actual in [('entry_price',entry),('stop_loss_price',stop),('take_profit_price',take_profit)]:
                if abs(risk.number(actual)-frozen[key])>tick+1e-9:
                    raise ValueError('program_geometry_changed_beyond_tick_rounding')
        except (ValueError,TypeError,KeyError) as exc:
            raise risk.RiskRejected('Final program plan check rejected: '+str(exc)) from None
    def pending_leverage(pending_inst,pending_side):
        if pending_inst==inst_id and pending_side in (side,'net','',None):return lev['lever']
        rows=_request('GET','/api/v5/account/leverage-info',{'instId':pending_inst,'mgnMode':'cross'},env)
        found=next((r for r in rows if r.get('posSide') in (pending_side,'net','')),None)
        if not found:raise risk.RiskRejected('Pending leverage unknown; pool cannot reserve guessed margin')
        return found['lever']
    allocation=capital_pool.admit(env,observed_equity,balances[0],positions,pending,metadata,
        inst_id=inst_id,available=available,policy=policy,leverage_reader=pending_leverage)
    allocation=cap_allocation(allocation,active_execution["execution"],positions,pending,metadata,inst_id,pending_leverage,env=env,observation=observed_equity,balance=balances[0])
    plan=risk.order_plan(metadata=metadata[inst_id],side=side,entry=entry,stop=stop,take_profit=take_profit,
                         requested_size=requested_size,budget_usdt=budget,equity=allocation.equity,available=allocation.available,
                         leverage=lev['lever'],policy=allocation.policy,existing_margin=sum(risk.number(p.get('imr') if p.get('imr') not in (None,'') else p.get('margin') or 0) for p in existing),portfolio=portfolio)
    # Include the proposed order's realized risk in the correlated cluster check.
    if active_execution['execution']['id']=='small300' and str(inst_id).split('-')[0].upper() in {'BTC','ETH','SOL','DOGE'}:
        if portfolio.get('correlated',0.0) + plan['risk_usdt'] > allocation.equity*0.04:
            raise risk.RiskRejected('Correlated BTC/ETH/SOL/DOGE cluster risk budget exhausted')
    if allocation.enabled:plan['capital_pool']=allocation.detail
    latest_positions=_request('GET','/api/v5/account/positions',{'instType':'SWAP'},env)
    def identities(rows):
        return sorted((str(p.get('instId')),str(p.get('posSide')),str(p.get('posId')),str(p.get('cTime')),str(p.get('pos')),str(p.get('avgPx'))) for p in rows if abs(risk.number(p.get('pos') or 0))>0)
    if identities(latest_positions)!=identities(positions):
        raise risk.RiskRejected('Positions changed during preflight; defer to a fresh decision cycle')
    if allocation.enabled:
        fresh_pending=_request('GET','/api/v5/trade/orders-pending',{'instType':'SWAP'},env)
        def pending_identity(rows):
            return sorted((str(r.get('ordId')),str(r.get('instId')),str(r.get('posSide')),str(r.get('sz')),str(r.get('accFillSz')),str(r.get('px'))) for r in capital_pool.entry_orders(rows))
        if pending_identity(fresh_pending)!=pending_identity(pending):raise risk.RiskRejected('Pending reservations changed during pool preflight')
    if capital_pool.config_signature()!=allocation.config_signature:raise risk.RiskRejected('Capital pool configuration changed during preflight; no order authorized')
    if execution_runtime()['signature']!=active_execution['signature']:
        raise risk.RiskRejected('Execution preset changed during preflight')
    plan['execution_profile']=active_execution
    plan['horizon'] = horizon if horizon in {'scalp','swing'} else 'swing'
    plan['portfolio_before']=portfolio
    plan['decision_id']=decision_id
    plan['scope']=env.identity
    client_id=evidence.begin_intent(env.identity,decision_id,inst_id,plan)
    return plan,client_id
