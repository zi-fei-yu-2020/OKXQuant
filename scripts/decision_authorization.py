"""Authorize current AI instructions against their account, strategy and position."""
import math
import time
from scripts.risk_policy import RiskRejected


def assert_strategy(decision, profile=None):
    from scripts.prompt_library import active_profile
    from scripts.trading_prompt import profile_signature
    expected=decision.get('strategy_profile_hash')
    if expected is not None and expected != profile_signature(profile or active_profile()):
        raise RiskRejected('Strategy changed after inference; require a fresh decision')


def validate_management(payload, env, positions, *, target=None, now=None):
    from scripts.prompt_library import active_profile
    from scripts.execution_profiles import runtime
    from scripts.trading_prompt import profile_signature
    from okxquant_backend.account_connections import assert_current
    assert_current(env)
    if not isinstance(payload,dict):raise RiskRejected('Invalid management envelope')
    timestamp=payload.get('timestamp')
    if isinstance(timestamp,bool):raise RiskRejected('Invalid management timestamp')
    try: age=(time.time() if now is None else now)-float(timestamp)
    except (ValueError,TypeError):raise RiskRejected('Invalid management timestamp') from None
    if not math.isfinite(age) or not 0<=age<=300:
        raise RiskRejected('Management instruction is stale or future-dated')
    for key,value in {'account_scope':env.identity,'connection_id':getattr(env,'connection_id',''),
                      'binding_version':getattr(env,'binding_version',0)}.items():
        if payload.get(key)!=value:raise RiskRejected('Management account binding changed')
    profile=active_profile()
    if payload.get('strategy_profile_hash')!=profile_signature(profile):
        raise RiskRejected('Management strategy changed; require a fresh decision')
    if payload.get('execution_profile_signature')!=runtime(profile)['signature']:
        raise RiskRejected('Management execution configuration changed')
    if target is None:return
    basis=payload.get('position_basis')
    if not isinstance(basis,list):raise RiskRejected('Management position evidence unavailable')
    live=positions.get(target)
    if not isinstance(live,dict):raise RiskRejected('Management position no longer exists')
    def signature(row):
        side=str(row.get('posSide') or row.get('side') or '')
        try:size=abs(float(row.get('pos',row.get('size'))))
        except (ValueError,TypeError):raise RiskRejected('Management position size unavailable') from None
        if not math.isfinite(size) or size<=0 or side not in ('long','short'):
            raise RiskRejected('Management position size/direction unavailable')
        if not row.get('posId') or not row.get('cTime'):
            raise RiskRejected('Management position lifecycle identity unavailable')
        return (str(row.get('instId')),side,size,str(row['posId']),str(row['cTime']))
    live_sig=signature(live)
    original=[r for r in basis if isinstance(r,dict) and r.get('instId')==target
              and str(r.get('posSide') or r.get('side'))==live_sig[1]]
    if len(original)!=1 or signature(original[0])!=live_sig:
        raise RiskRejected('Management position changed since inference')


from contextlib import contextmanager, ExitStack

@contextmanager
def entry_dispatch_guard(env, plan):
    """Serialize local consent/config changes with entry dispatch, not recall sent requests."""
    from scripts.config_lock import configuration_write
    from scripts.prompt_library import LIBRARY_FILE
    from scripts import strategy_engine_runtime as engine, strategy_evidence as evidence
    from scripts.execution_profiles import runtime
    from okxquant_backend.account_connections import assert_current
    import json
    live_minute=env.mode=='live' and plan.get('entry_engine')=='demo_scalp_v2'
    with ExitStack() as stack:
        if live_minute:stack.enter_context(configuration_write(engine.STATE_FILE))
        if plan.get('execution_profile') or plan.get('strategy_profile_hash') or live_minute:
            stack.enter_context(configuration_write(LIBRARY_FILE))
        assert_current(env)
        assert_strategy(plan)
        expected_execution=(plan.get('execution_profile') or {}).get('signature')
        if expected_execution and expected_execution!=runtime()['signature']:
            raise RiskRejected('Execution configuration changed before dispatch')
        if live_minute:
            frozen=plan.get('engine_binding')
            if not isinstance(frozen,dict):
                with evidence.connection() as db:
                    row=db.execute("SELECT payload FROM events WHERE id=? AND scope=? AND kind='decision'",(plan.get('decision_id'),env.identity)).fetchone()
                frozen=(json.loads(row[0]).get('decision') or {}).get('engine_binding') if row else None
            current=engine.status(env)
            if not isinstance(frozen,dict) or not current.get('enabled') or any(frozen.get(k)!=current.get(k) for k in (*engine.BINDING_FIELDS,'record_id')):
                raise RiskRejected('LIVE minute consent changed before dispatch')
        yield
