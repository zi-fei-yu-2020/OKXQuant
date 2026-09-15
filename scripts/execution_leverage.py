"""DEMO new-position leverage policy and one-write/read-back application.
Existing positions are never re-leveraged. Leverage is not a risk budget.
"""
from dataclasses import replace
import math
from scripts import risk_policy as risk, strategy_evidence as evidence
from scripts.strategy_modes import mode_for, leverage_for


def mode_policy(policy, horizon, env):
    mode=mode_for(horizon)
    ceiling=policy.scalp_max_leverage if horizon=='scalp' and env.mode=='demo' else policy.max_leverage
    return replace(policy,max_leverage=min(float(ceiling),mode['max_leverage']),
                   per_trade_equity_pct=min(policy.per_trade_equity_pct,mode['risk_per_trade_equity_pct']))


def choose(policy, horizon, env, current, existing, decision):
    current=risk.number(current,positive=True)
    if existing or env.mode!='demo':
        return current
    # Explicit proposals are bounded by the horizon, never by an AI confidence score.
    requested=decision.get('leverage')
    if isinstance(requested,bool):raise risk.RiskRejected('Invalid leverage proposal')
    if requested is not None and not math.isfinite(risk.number(requested,positive=True)):
        raise risk.RiskRejected('Invalid leverage proposal')
    target=leverage_for(horizon,requested)
    if not math.isfinite(target):raise risk.RiskRejected('Invalid leverage proposal')
    return max(1.,min(target,policy.max_leverage))


def apply(env, inst_id, side, target, old, decision_id, request):
    if math.isclose(target,float(old),abs_tol=1e-9):return float(old)
    if env.mode!='demo':raise risk.RiskRejected('Automatic leverage changes are DEMO-only')
    from okxquant_backend.account_connections import assert_current
    assert_current(env)
    # SWAP cross leverage is instrument-level; do not touch either side's existing risk.
    positions=request('GET','/api/v5/account/positions',{'instId':inst_id},env)
    pending=request('GET','/api/v5/trade/orders-pending',{'instId':inst_id},env)
    if any(p.get('instId')==inst_id and abs(risk.number(p.get('pos') or 0)) for p in positions) or pending:
        raise risk.RiskRejected('Instrument exposure appeared before leverage change')
    payload={'instId':inst_id,'mgnMode':'cross','lever':format(target,'g')}
    evidence.append(env.identity,'leverage_change_intent',{'decision_id':decision_id,'instrument':inst_id,'previous':float(old),'target':target})
    error=None
    try:request('POST','/api/v5/account/set-leverage',payload,env)
    except Exception as exc:error=type(exc).__name__  # No blind write retry.
    try:
        rows=request('GET','/api/v5/account/leverage-info',{'instId':inst_id,'mgnMode':'cross'},env)
        actual=next((risk.number(r.get('lever'),positive=True) for r in rows if r.get('posSide') in (side,'net','')),None)
    except Exception:
        actual=None
    verified=actual is not None and math.isclose(actual,target,abs_tol=1e-9)
    evidence.append(env.identity,'leverage_change_result',{'decision_id':decision_id,'instrument':inst_id,
                    'target':target,'actual':actual,'verified':verified,'transport_error':error})
    if not verified:raise risk.RiskRejected('Leverage change not verified; no entry order authorized')
    return actual
