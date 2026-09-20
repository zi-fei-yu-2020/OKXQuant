"""Read-only projections of effective execution limits and account risk evidence."""
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
import sqlite3
import time


def execution_snapshot():
    from scripts.execution_profiles import runtime
    from scripts.risk_policy import load_policy
    from scripts.execution_leverage import mode_policy
    from scripts.okx_runtime import selected_environment
    from scripts.instrument_pool import load_instruments
    value=runtime(); policy=load_policy(); env=selected_environment()
    execution=dict(value['execution'])
    for key in ('per_trade_equity_pct','single_asset_margin_usdt','max_leverage','daily_drawdown_pct'):
        execution.setdefault(key,getattr(policy,key))
    execution.setdefault('max_active_instruments',len(load_instruments()))
    execution.setdefault('max_same_direction_positions',execution['max_active_instruments'])
    # Standard has no fixed total 1800U margin cap. It uses actual free margin.
    execution.setdefault('total_margin_usdt',None)
    execution['available_margin_fraction']=policy.available_margin_fraction
    return {**value,'execution':execution,'environment':env.mode,
            'mode_limits':{h:{'max_leverage':mode_policy(policy,h,env).max_leverage,
                              'per_trade_equity_pct':mode_policy(policy,h,env).per_trade_equity_pct}
                           for h in ('scalp','swing')}}


def risk_snapshot(data_dir=None, *, env=None, now=None):
    from scripts.okx_runtime import selected_environment
    from scripts.risk_policy import load_policy,ledger_daily_drawdown
    from scripts.execution_profiles import runtime
    env=env or selected_environment();now=time.time() if now is None else now
    day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    policy=load_policy();root=Path(data_dir) if data_dir is not None else Path(__file__).resolve().parents[1]/'data'
    result={'account_scope':env.identity,'unresolved_entries':None,'daily_drawdown':None,
            'daily_threshold':policy.daily_drawdown_pct,'daily_blocked':None,'observed_at':None,
            'status':'unavailable','basis':'execution_evidence_not_dashboard_initial_capital'}
    db_path=root/'strategy_evidence.db'
    if not db_path.exists():return result
    db=sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
    try:
        result['unresolved_entries']=db.execute("SELECT count(*) FROM intents WHERE scope=? AND state IN ('unknown','acknowledged')",(env.identity,)).fetchone()[0]
        observations=[]
        row=db.execute('SELECT payload FROM equity_state WHERE scope=?',(env.identity,)).fetchone()
        if row:observations.append(json.loads(row[0]))
        scopes=[env.identity]
        if runtime()['execution']['id']=='small300':scopes.append(env.identity+':execution:small300')
        for scope in scopes:
            row=db.execute('SELECT payload FROM capital_pool_state WHERE scope=?',(scope,)).fetchone()
            if row:
                state=json.loads(row[0]);o=dict(state.get('drawdown') or {})
                o['at']=state.get('checked_at',state.get('at',o.get('at')));observations.append(o)
        observations=[o for o in observations if o.get('day')==day and isinstance(o.get('at'),(float,int)) and 0<=now-o['at']<=180]
        values=[float(o['daily_drawdown']) for o in observations if isinstance(o.get('daily_drawdown'),(float,int)) and math.isfinite(float(o['daily_drawdown']))]
        if not values:return result
        # Same optional ledger gate as the entry gateway; no network or writes.
        ledger=ledger_daily_drawdown(policy,scope=env.identity)
        if ledger.get('day')==day and ledger.get('reason')=='lifecycle_ledger_daily_loss':values.append(ledger['drawdown'])
        drawdown=max(values)
        result.update(status='observed',daily_drawdown=drawdown,daily_blocked=drawdown>=policy.daily_drawdown_pct,
                      observed_at=max(o['at'] for o in observations))
        return result
    except (sqlite3.Error,ValueError,TypeError,KeyError):return result
    finally:db.close()
