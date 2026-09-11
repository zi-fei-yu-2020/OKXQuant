"""Versioned execution presets. Prompt activation is the single source of truth.

No model output changes these limits. A preset is a virtual risk cap, not a
segregated exchange wallet. Existing oversized positions are not forced closed.
"""
import hashlib
import json

SMALL_300 = {
    'id':'small300', 'label':'300U 小资金 · 风险预算型', 'equity_cap_usdt':300.0,
    'per_trade_equity_pct':0.02, 'single_asset_margin_usdt':150.0,
    'total_margin_usdt':270.0, 'max_active_instruments':2, 'max_same_direction_positions':1,
    'max_leverage':6.0, 'daily_drawdown_pct':0.08, 'portfolio_stop_pct':0.06,
    'direction_stop_pct':0.04, 'group_stop_pct':0.04, 'peak_drawdown_pct':0.15,
    'minimum_net_rr':2.2,
}

def settings_for(profile):
    # Operators can bind a stable prompt profile to the capital-aware preset
    # without rewriting prompt text. The exchange leverage remains an observed
    # account setting and order_plan sizes from actual stop distance.
    requested_mode = str(__import__('os').environ.get('OKXQUANT_CAPITAL_MODE','')).strip().lower()
    name = 'small300' if (profile.get('id')=='small300' or requested_mode in {'300','small300','small'}) else profile.get('execution_profile','standard')
    if name not in ('small300','standard'):
        raise ValueError('Unknown execution preset; new risk blocked')
    return dict(SMALL_300) if name=='small300' else {'id':'standard','label':'标准风控（独立配置）'}

def runtime(profile=None):
    if profile is None:
        from scripts.prompt_library import active_profile, LIBRARY_FILE
        # The prompt editor's legacy recovery must never silently expand a
        # small-account budget after a corrupt saved activation record.
        requested = None
        if LIBRARY_FILE.exists():
            try:
                raw=json.loads(LIBRARY_FILE.read_text(encoding='utf-8'))
                if not isinstance(raw,dict):raise ValueError('Invalid profile library')
                requested=raw.get('active_profile_id')
            except (OSError,ValueError,TypeError):
                raise ValueError('Execution profile activation unreadable; new risk blocked') from None
        profile=active_profile()
        if requested and requested!=profile['id']:
            raise ValueError('Execution profile activation inconsistent; new risk blocked')
    config=settings_for(profile)
    identity={'profile_id':profile['id'],'execution':config}
    return {**identity,'signature':hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()}

def cap_allocation(allocation, config, positions, pending, metadata, inst_id, leverage_reader, *, env=None, observation=None, balance=None):
    if config['id']!='small300':return allocation
    from dataclasses import replace
    from scripts import capital_pool
    from scripts.risk_policy import RiskRejected
    held=capital_pool.active_positions(positions); orders=capital_pool.entry_orders(pending)
    names={p['instId'] for p in held}|{p['instId'] for p in orders}|{inst_id}
    if len(names)>config['max_active_instruments']:raise RiskRejected('300U preset active/pending instrument cap')
    reserved=capital_pool.reserved_margin(held,orders,metadata,leverage_reader)
    free=min(allocation.available,max(0.,config['total_margin_usdt']-reserved))
    if free<=0:raise RiskRejected('300U preset total margin budget exhausted; existing positions unchanged')
    # Preserve the allocation's losses over restarts/profile switches. Deposits
    # cannot erase drawdown. This is still a virtual cap, not physical isolation.
    if env is None or observation is None or balance is None:raise RiskRejected('300U allocation requires reconciled account observation')
    scope=env.identity+':execution:small300'
    cfg=capital_pool.Config(environment=env.mode,allocation_id='execution-small300-v1',budget_usdt=config['equity_cap_usdt'])
    capital_pool.check_account(balance)
    observation=capital_pool.currency_observation(observation,balance)
    previous=capital_pool._state(scope)
    if previous is None:capital_pool.assert_new_scope(scope,cfg)
    state=capital_pool.advance(previous,cfg,scope,observation,flat=not held and not orders,policy=allocation.policy)
    capital_pool._save(state)
    if state['drawdown']['blocked'] or state['risk_equity']<=0:raise RiskRejected('300U allocation drawdown gate; existing protection continues')
    detail={**allocation.detail,'execution_allocation':{'nav':state['pool_nav'],'risk_equity':state['risk_equity'],'virtual_cap':True}}
    return replace(allocation,equity=min(allocation.equity,state['risk_equity']),available=free,detail=detail)
