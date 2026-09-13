"""Optional account-scoped risk allocation. NOT exchange-level fund isolation.

Defaults off. Enabling requires explicit account ownership/risk acknowledgement,
and initial observation must be flat. Losses survive restarts and deposits cannot
reset them. Call admission/observation under the existing position writer gate.
No exchange writes or model calls exist in this module.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, replace
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from scripts import strategy_evidence as evidence
from scripts.risk_policy import Policy, RiskRejected, number, update_equity_state, linear_metadata

CONFIG_FILE=Path(__file__).resolve().parents[1]/'data'/'capital_pool.json'
VERSION='capital-pool-v1'

@dataclass(frozen=True)
class Config:
    enabled: bool=False
    environment: str='demo'
    allocation_id: str='small-account-300-v1'
    budget_usdt: float=300.
    max_active_instruments: int=2
    single_asset_margin_fraction: float=.2
    total_margin_fraction: float=.5
    dedicated_account_confirmed: bool=False
    virtual_cap_acknowledged: bool=False
    allow_live: bool=False

    def __post_init__(self):
        for key in ('enabled','dedicated_account_confirmed','virtual_cap_acknowledged','allow_live'):
            if type(getattr(self,key)) is not bool:raise RiskRejected('Capital pool flags must be explicit booleans')
        if self.environment not in ('demo','live') or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',self.allocation_id):raise RiskRejected('Invalid capital pool identity')
        if isinstance(self.budget_usdt,bool) or not 1<=number(self.budget_usdt,positive=True)<=1_000_000:raise RiskRejected('Invalid capital budget')
        if type(self.max_active_instruments) is not int or not 1<=self.max_active_instruments<=6:raise RiskRejected('Invalid capital pool instrument cap')
        for key in ('single_asset_margin_fraction','total_margin_fraction'):
            value=getattr(self,key)
            if isinstance(value,bool) or not 0<number(value)<1:raise RiskRejected('Invalid capital pool margin fraction')
        if self.single_asset_margin_fraction>self.total_margin_fraction:raise RiskRejected('Single margin cap exceeds total cap')
        if self.enabled and not (self.dedicated_account_confirmed and self.virtual_cap_acknowledged):raise RiskRejected('Capital pool requires dedicated-account and virtual-cap acknowledgement')
        if self.enabled and self.environment=='live' and not self.allow_live:raise RiskRejected('Live capital pool requires separate explicit opt-in')

    @property
    def allocation_fingerprint(self):
        return hashlib.sha256(evidence.canonical({'allocation_id':self.allocation_id,'environment':self.environment,'budget_usdt':float(self.budget_usdt)}).encode()).hexdigest()


def load_config():
    if not CONFIG_FILE.exists():return Config()
    try:
        value=json.loads(CONFIG_FILE.read_text(encoding='utf8'))
        if not isinstance(value,dict) or set(value)-set(Config.__dataclass_fields__):raise ValueError('Unknown config field')
        return Config(**value)
    except RiskRejected:raise
    except (OSError,ValueError,TypeError) as exc:raise RiskRejected('Invalid capital pool configuration: '+type(exc).__name__) from None


def config_signature(config=None):
    value=asdict(config or load_config());value['budget_usdt']=float(value['budget_usdt'])
    return hashlib.sha256(evidence.canonical(value).encode()).hexdigest()


def _state(scope):
    if not evidence.DB_PATH.exists():return None
    try:
        with sqlite3.connect(evidence.DB_PATH.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capital_pool_state'").fetchone():return None
            row=db.execute('SELECT payload FROM capital_pool_state WHERE scope=?',(scope,)).fetchone()
        if not row:return None
        state=json.loads(row[0])
        if not isinstance(state,dict) or state.get('scope')!=scope or state.get('version')!=VERSION or state.get('currency')!='USDT':raise ValueError('Invalid pool state')
        for key in ('baseline_adjusted_equity','initial_budget','account_equity','external_flow_total','external_flow_origin','at','pool_nav','risk_equity'):
            number(state[key])
        if not isinstance(state.get('drawdown'),dict) or type(state['drawdown'].get('blocked')) is not bool:raise ValueError('Missing pool drawdown state')
        if not isinstance(state.get('allocation_fingerprint'),str) or not re.fullmatch(r'[0-9a-f]{64}',state['allocation_fingerprint']):raise ValueError('Invalid allocation fingerprint')
        return state
    except (sqlite3.Error,ValueError,TypeError,KeyError):raise RiskRejected('Capital pool state unreadable; never reset losses automatically') from None


def assert_new_scope(scope,config):
    # Credential fingerprints are not stable exchange UIDs. A key rotation must
    # not silently create a fresh budget and erase the previous allocation loss.
    if not evidence.DB_PATH.exists():return
    try:
        with sqlite3.connect(evidence.DB_PATH.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capital_pool_state'").fetchone():return
            rows=db.execute('SELECT scope,payload FROM capital_pool_state WHERE scope<>?',(scope,)).fetchall()
        for old_scope,payload in rows:
            state=json.loads(payload)
            if state.get('allocation_id')==config.allocation_id:raise RiskRejected('Credentials/account identity changed; explicit allocation migration required')
    except RiskRejected:raise
    except (sqlite3.Error,ValueError,TypeError,AttributeError):raise RiskRejected('Allocation identity registry requires review; no fresh budget created') from None


def _save(state, *, archive=True):
    state={**state,'checked_at':time.time()}
    with evidence.connection() as db:
        db.execute('INSERT OR REPLACE INTO capital_pool_state VALUES (?,?)',(state['scope'],evidence.canonical(state)))
    if archive:evidence.best_effort(state['scope'],'capital_pool_observation',state)


def advance(previous,config,scope,observation,*,flat,policy):
    """Pure NAV transition: budget + account PnL, excluding reconciled external flows."""
    if observation.get('equity_currency')!='USDT':raise RiskRejected('Capital pool requires USDT equity, not converted USD totalEq')
    equity=number(observation.get('equity'),positive=True);at=number(observation.get('at'),positive=True)
    if 'external_flow_total' not in observation:raise RiskRejected('Pool requires reconciled cumulative external flows')
    flow=number(observation['external_flow_total']);adjusted=equity-flow
    origin=number(observation.get('external_flow_origin'),positive=True)
    if origin>at:raise RiskRejected('Invalid external-flow observation origin')
    if previous:
        if previous.get('external_flow_origin')!=origin:raise RiskRejected('External-flow continuity changed; allocation reconciliation required')
        if previous['allocation_fingerprint']!=config.allocation_fingerprint:raise RiskRejected('Allocation changed; reviewed reallocation required, no automatic loss reset')
        if at<previous['at']:raise RiskRejected('Capital pool observation moved backwards')
        if at==previous['at']:
            if abs(equity-previous['account_equity'])>1e-8 or abs(flow-previous['external_flow_total'])>1e-8:raise RiskRejected('Contradictory capital pool observation')
            return previous
        initial=previous['initial_budget'];baseline=previous['baseline_adjusted_equity']
    else:
        if not flat:raise RiskRejected('Capital pool initialization requires no positions or pending entries')
        initial=min(config.budget_usdt,equity);baseline=adjusted
    nav=initial+adjusted-baseline
    risk_equity=max(0.,min(config.budget_usdt,nav,equity))
    if nav<=0:
        drawdown={**((previous or {}).get('drawdown') or {}),'blocked':True,'reason':'Capital pool depleted'}
    else:
        drawdown=update_equity_state((previous or {}).get('drawdown'),equity=nav,at=at,cash_flow=0,complete=True,policy=policy)
    return {'scope':scope,'version':VERSION,'currency':'USDT','allocation_id':config.allocation_id,'allocation_fingerprint':config.allocation_fingerprint,
            'initial_budget':initial,'configured_cap':config.budget_usdt,'baseline_adjusted_equity':baseline,
            'account_equity':equity,'external_flow_total':flow,'external_flow_origin':origin,'at':at,'pool_nav':nav,'risk_equity':risk_equity,
            'strategy_pnl_since_allocation':nav-initial,'drawdown':drawdown,
            'warning':'Virtual risk budget only; cross-margin losses are not physically isolated'}


def observe_existing(env,observation,policy,*,balance=None):
    config=load_config()
    if not config.enabled:return None
    if config.environment!=env.mode:raise RiskRejected('Capital pool environment mismatch')
    previous=_state(env.identity)
    if previous is None:return None  # The read-only guard never initializes an allocation with unknown positions.
    if balance is not None:observation=currency_observation(observation,balance)
    state=advance(previous,config,env.identity,observation,flat=False,policy=policy)
    _save(state,archive=state is not previous)
    if state['drawdown']['blocked'] or state['risk_equity']<=0:raise RiskRejected('Capital pool drawdown/depletion gate; independent protection remains active')
    return state


def active_positions(positions):
    return [p for p in positions if abs(number(p.get('pos') or 0))>0]


def entry_orders(orders):
    return [o for o in orders if str(o.get('reduceOnly','false')).lower() not in ('true','1') and number(o.get('sz') or 0)-number(o.get('accFillSz') or 0)>0]


def reserved_margin(positions,orders,metadata,leverage_reader):
    total=0.
    for p in active_positions(positions):
        raw=p.get('imr') if p.get('imr') not in (None,'') else p.get('margin')
        total+=number(raw,positive=True)  # No guessed leverage for unknown existing margin.
    for order in entry_orders(orders):
        meta=metadata.get(order.get('instId'))
        if not meta:raise RiskRejected('Pending entry metadata unavailable for pool reservation')
        ct,*_=linear_metadata(meta)
        leverage=order.get('lever')
        if leverage in (None,'','0'):
            if order.get('tdMode','cross')!='cross':raise RiskRejected('Pending non-cross leverage unknown')
            leverage=leverage_reader(order['instId'],order.get('posSide'))
        leverage=number(leverage,positive=True)
        total+=(number(order['sz'])-number(order.get('accFillSz') or 0))*ct*number(order.get('px'),positive=True)/leverage
    return total


@dataclass
class Budget:
    enabled: bool
    equity: float
    available: float
    policy: Policy
    detail: dict
    config_signature: str


def check_account(balance):
    details=balance.get('details')
    if not isinstance(details,list) or not details or any(not isinstance(d,dict) or not d.get('ccy') or d.get('eq') in (None,'') for d in details):raise RiskRejected('Currency equity breakdown unavailable for capital allocation')
    if sum(d['ccy']=='USDT' for d in details)!=1:raise RiskRejected('Capital pool requires a verified USDT equity row')
    for item in details:
        if item.get('ccy')!='USDT' and any(abs(number(item.get(k) or 0))>1e-8 for k in ('eq','cashBal','liab')):raise RiskRejected('Capital pool requires a dedicated USDT account; foreign assets are not attributed')
        if abs(number(item.get('liab') or 0))>1e-8:raise RiskRejected('Borrowed balances cannot fund the capital pool')


def currency_observation(observation,balance):
    check_account(balance)
    usdt=next(item for item in balance['details'] if item['ccy']=='USDT')
    return {**observation,'equity':number(usdt['eq'],positive=True),'equity_currency':'USDT'}


def needs_initialization(env):
    config=load_config()
    if not config.enabled:return False
    if config.environment!=env.mode:raise RiskRejected('Capital pool environment mismatch')
    return _state(env.identity) is None


def initialize_flat(env,observation,balance,positions,orders,policy):
    config=load_config()
    if not config.enabled:return None
    if config.environment!=env.mode:raise RiskRejected('Capital pool environment mismatch')
    check_account(balance)
    observation=currency_observation(observation,balance)
    if active_positions(positions) or entry_orders(orders):return None
    previous=_state(env.identity)
    if previous is None:assert_new_scope(env.identity,config)
    state=advance(previous,config,env.identity,observation,flat=True,policy=policy)
    _save(state,archive=state is not previous)
    return state


def admit(env,observation,balance,positions,orders,metadata,*,inst_id,available,policy,leverage_reader):
    config=load_config()
    if not config.enabled:return Budget(False,number(balance['totalEq'],positive=True),available,policy,{},config_signature(config))
    if config.environment!=env.mode:raise RiskRejected('Capital pool environment mismatch')
    check_account(balance)
    observation=currency_observation(observation,balance)
    held=active_positions(positions);pending=entry_orders(orders)
    previous=_state(env.identity)
    if previous is None:assert_new_scope(env.identity,config)
    state=advance(previous,config,env.identity,observation,flat=not held and not pending,policy=policy)
    if state is not previous:_save(state)
    if state['drawdown']['blocked'] or state['risk_equity']<=0:raise RiskRejected('Capital pool drawdown/depletion gate')
    instruments={p['instId'] for p in held}|{p['instId'] for p in pending}
    if len(instruments|{inst_id})>config.max_active_instruments:raise RiskRejected('Capital pool active/pending instrument cap')
    committed=reserved_margin(held,pending,metadata,leverage_reader)
    equity=state['risk_equity'];total_cap=equity*config.total_margin_fraction
    free=min(number(available),max(0.,total_cap-committed))
    if free<=0:raise RiskRejected('Capital pool margin budget exhausted')
    effective_policy=replace(policy,single_asset_margin_usdt=min(policy.single_asset_margin_usdt,equity*config.single_asset_margin_fraction))
    detail={'allocation_id':config.allocation_id,'pool_nav':state['pool_nav'],'risk_equity':equity,
            'account_equity':state['account_equity'],'reserved_margin':committed,'total_margin_cap':total_cap,
            'single_asset_margin_cap':effective_policy.single_asset_margin_usdt,'max_active_instruments':config.max_active_instruments,
            'warning':state['warning']}
    return Budget(True,equity,free,effective_policy,detail,config_signature(config))


def status(env):
    try:
        config=load_config()
        if not config.enabled:return {'enabled':False,'status':'disabled','version':VERSION}
        if config.environment!=env.mode:return {'enabled':True,'status':'environment_mismatch','version':VERSION}
        state=_state(env.identity)
        if state is None:
            assert_new_scope(env.identity,config)
            return {'enabled':True,'status':'awaiting_flat_initialization','configured_cap':config.budget_usdt,'version':VERSION}
        if state['allocation_fingerprint']!=config.allocation_fingerprint:raise RiskRejected('Allocation changed; reviewed reallocation required')
        return {'enabled':True,'status':'blocked' if state['drawdown']['blocked'] else 'active','version':VERSION,
                'currency':'USDT','configured_cap':config.budget_usdt,'pool_nav':state['pool_nav'],'risk_equity':state['risk_equity'],
                'strategy_pnl_since_allocation':state['strategy_pnl_since_allocation'],'account_equity':state['account_equity'],
                'updated_at':state.get('checked_at',state['at']),'balance_version_at':state['at'],'stale':time.time()-state.get('checked_at',state['at'])>120,'drawdown':state['drawdown'],
                'max_active_instruments':config.max_active_instruments,'warning':state['warning']}
    except RiskRejected as exc:return {'enabled':True,'status':'error','version':VERSION,'message':str(exc)}
    except (KeyError,TypeError):return {'enabled':True,'status':'error','version':VERSION,'message':'Capital allocation state/config unavailable; new exposure blocked, protection remains active'}
