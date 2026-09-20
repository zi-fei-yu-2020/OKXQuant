"""DEMO / explicitly authorized LIVE minute new-position leverage policy.
Existing positions are never re-leveraged. Leverage is not a risk budget.
"""
from dataclasses import replace
import math
from scripts import risk_policy as risk, strategy_evidence as evidence
from scripts.strategy_modes import mode_for, leverage_for



def _live_binding(env, horizon):
    """Read-only opt-in; unavailable/invalid status never expands LIVE authority."""
    if env.mode != 'live' or horizon != 'scalp':
        return None
    try:
        from scripts.strategy_engine_runtime import status, ENGINE_VERSION, BINDING_FIELDS
        binding = status(env)
        if (binding.get('enabled') is not True or binding.get('authorized') is not True
                or binding.get('status') != 'ready' or binding.get('environment') != 'live'
                or binding.get('engine_version') != ENGINE_VERSION
                or binding.get('account_scope') != env.identity
                or binding.get('connection_id') != getattr(env, 'connection_id', '')
                or type(binding.get('binding_version')) is not int
                or binding['binding_version'] != getattr(env, 'binding_version', 0)
                or any(not isinstance(binding.get(key), str) or not binding[key]
                       for key in ('profile_signature', 'execution_signature', 'policy_signature', 'record_id'))):
            return None
        return binding
    except Exception:
        return None


def _check_live_decision(env, inst_id, decision_id, binding):
    """Only a durable minute-engine decision can consume minute leverage consent."""
    import json
    import sqlite3
    from scripts.strategy_engine_runtime import BINDING_FIELDS, ENGINE_ID
    try:
        with sqlite3.connect(evidence.DB_PATH.resolve().as_uri()+'?mode=ro', uri=True) as db:
            row = db.execute("SELECT payload FROM events WHERE id=? AND scope=? AND kind='decision'",
                             (decision_id, env.identity)).fetchone()
        record = json.loads(row[0]) if row else {}
        decision = record.get('decision', {})
        frozen = decision.get('engine_binding', {})
        if (record.get('instrument') != inst_id or decision.get('horizon') != 'scalp'
                or record.get('features', {}).get('strategy_engine') != ENGINE_ID
                or record.get('execution_profile_signature') != binding['execution_signature']
                or any(frozen.get(key) != binding[key] for key in (*BINDING_FIELDS, 'record_id'))):
            raise ValueError('Minute decision binding differs')
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        raise risk.RiskRejected('Current same-account minute decision required for LIVE leverage') from None


def mode_policy(policy, horizon, env):
    mode=mode_for(horizon)
    minute_authorized=horizon=='scalp' and (env.mode=='demo' or _live_binding(env,horizon) is not None)
    ceiling=policy.scalp_max_leverage if minute_authorized else policy.max_leverage
    return replace(policy,max_leverage=min(float(ceiling),mode['max_leverage']),
                   per_trade_equity_pct=min(policy.per_trade_equity_pct,mode['risk_per_trade_equity_pct']))


def choose(policy, horizon, env, current, existing, decision):
    current=risk.number(current,positive=True)
    if existing:
        return current
    if env.mode!='demo' and _live_binding(env,horizon) is None:
        return current
    # Explicit proposals are bounded by the horizon, never by an AI confidence score.
    requested=decision.get('leverage')
    if isinstance(requested,bool):raise risk.RiskRejected('Invalid leverage proposal')
    if requested is not None and not math.isfinite(risk.number(requested,positive=True)):
        raise risk.RiskRejected('Invalid leverage proposal')
    target=leverage_for(horizon,requested)
    if not math.isfinite(target):raise risk.RiskRejected('Invalid leverage proposal')
    return max(1.,min(target,policy.max_leverage))


def apply(env, inst_id, side, target, old, decision_id, request, *, horizon="swing"):
    if math.isclose(target,float(old),abs_tol=1e-9):return float(old)
    live_binding=None
    if env.mode!='demo':
        live_binding=_live_binding(env,horizon)
        if live_binding is None:raise risk.RiskRejected('Automatic LIVE leverage requires current minute consent')
        target=risk.number(target,positive=True)
        limit=risk.number(live_binding.get('limits',{}).get('max_leverage'),positive=True)
        if not 1<=target<=min(limit,mode_for('scalp')['max_leverage']):
            raise risk.RiskRejected('LIVE minute leverage exceeds effective policy')
        _check_live_decision(env,inst_id,decision_id,live_binding)
    from okxquant_backend.account_connections import assert_current
    assert_current(env)
    # SWAP cross leverage is instrument-level; do not touch either side's existing risk.
    positions=request('GET','/api/v5/account/positions',{'instId':inst_id},env)
    pending=request('GET','/api/v5/trade/orders-pending',{'instId':inst_id},env)
    if any(p.get('instId')==inst_id and abs(risk.number(p.get('pos') or 0)) for p in positions) or pending:
        raise risk.RiskRejected('Instrument exposure appeared before leverage change')
    payload={'instId':inst_id,'mgnMode':'cross','lever':format(target,'g')}
    consent={}
    if live_binding is not None:
        from scripts.strategy_engine_runtime import BINDING_FIELDS
        consent={'live_binding':{key:live_binding[key] for key in (*BINDING_FIELDS,'record_id')}}
    evidence.append(env.identity,'leverage_change_intent',{'decision_id':decision_id,'instrument':inst_id,'previous':float(old),'target':target,**consent})
    if live_binding is not None:
        fresh=_live_binding(env,horizon)
        if fresh is None or any(fresh.get(key)!=live_binding[key] for key in (*BINDING_FIELDS,'record_id')):
            raise risk.RiskRejected('LIVE minute consent changed before leverage dispatch')
        assert_current(env)
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
                    'target':target,'actual':actual,'verified':verified,'transport_error':error,**consent})
    if not verified:raise risk.RiskRejected('Leverage change not verified; no entry order authorized')
    return actual
