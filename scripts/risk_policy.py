"""Deterministic final-price risk policy for linear USDT swaps; no model authority."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_FLOOR
import math

class RiskRejected(ValueError): pass

def number(value, *, positive=False):
    try: result = float(value)
    except (ValueError, TypeError): raise RiskRejected('Missing numeric risk input') from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise RiskRejected('Invalid numeric risk input')
    return result

@dataclass(frozen=True)
class Policy:
    # Conservative engineering defaults; configurable via validated risk_policy.json.
    per_trade_equity_pct: float = .005
    portfolio_stop_pct: float = .03
    direction_stop_pct: float = .02
    group_stop_pct: float = .02
    daily_drawdown_pct: float = .03
    peak_drawdown_pct: float = .08
    single_asset_margin_usdt: float = 600
    available_margin_fraction: float = .8
    taker_fee: float = .0005
    maker_fee: float = .0002
    slippage: float = .001
    minimum_net_rr: float = 2
    max_entry_distance_pct: float = .02
    max_leverage: float = 5

    def __post_init__(self):
        for name,value in vars(self).items():
            number(value,positive=True)
            if ('pct' in name or name in {'available_margin_fraction','taker_fee','maker_fee','slippage'}) and value >= 1:
                raise RiskRejected('Risk fractions must be below 1')
        if self.per_trade_equity_pct > self.portfolio_stop_pct:
            raise RiskRejected('Single trade budget exceeds portfolio budget')

def load_policy():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1]/'data'/'risk_policy.json'
    raw=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(raw,dict) or set(raw)-set(Policy.__dataclass_fields__): raise RiskRejected('Invalid risk policy')
    from scripts.execution_profiles import runtime
    preset=runtime()['execution']
    if preset['id']=='small300':
        raw.update({key:preset[key] for key in ('per_trade_equity_pct','single_asset_margin_usdt','max_leverage','daily_drawdown_pct','portfolio_stop_pct','direction_stop_pct','group_stop_pct','peak_drawdown_pct','minimum_net_rr')})
        # small300 remains risk-budgeted: these are ceilings, while order_plan
        # sizes from the actual stop distance and account equity.
        raw['per_trade_equity_pct'] = min(float(raw.get('per_trade_equity_pct', .02)), .02)
    return Policy(**raw)

def monotonic_stop(side, old, new, current):
    old,new,current = [number(x,positive=True) for x in (old,new,current)]
    return (old < new < current) if side=='long' else (current < new < old) if side=='short' else False

def linear_metadata(raw):
    if raw.get('ctType') != 'linear' or raw.get('settleCcy') != 'USDT' or raw.get('state') != 'live':
        raise RiskRejected('Only live linear USDT swap metadata is accepted')
    ct=number(raw.get('ctVal'),positive=True)*number(raw.get('ctMult') or 1,positive=True)
    lot=number(raw.get('lotSz'),positive=True); minimum=number(raw.get('minSz'),positive=True)
    tick=number(raw.get('tickSz'),positive=True)
    return ct,lot,minimum,tick

def floor_step(value, step):
    return float((Decimal(str(value))/Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR)*Decimal(str(step)))

def order_plan(*, metadata, side, entry, stop, take_profit, requested_size, budget_usdt,
               equity, available, leverage, policy=None, existing_margin=0, portfolio=None):
    policy=policy or Policy()
    ct,lot,minimum,tick=linear_metadata(metadata)
    entry,stop,take_profit,equity,available,leverage = [number(x,positive=True) for x in (entry,stop,take_profit,equity,available,leverage)]
    for price in (entry,stop,take_profit):
        units=Decimal(str(price))/Decimal(str(tick))
        if units != units.to_integral_value(): raise RiskRejected('Final price is not on tick grid')
    if not ((side=='long' and stop<entry<take_profit) or (side=='short' and take_profit<entry<stop)):
        raise RiskRejected('Invalid final order geometry')
    if leverage>policy.max_leverage: raise RiskRejected('Actual exchange leverage exceeds policy')
    distance=abs(entry-stop)
    if distance >= entry/leverage*.8: raise RiskRejected('Stop exceeds conservative leverage buffer')
    # Realistic cost: limit entry pays maker fee, OCO stop pays taker fee once, slippage on stop only.
    cost_per_contract=ct*(entry*policy.maker_fee+max(stop,take_profit)*policy.taker_fee+entry*policy.slippage)
    unit_risk=ct*distance+cost_per_contract
    unit_reward=ct*abs(take_profit-entry)-cost_per_contract
    if unit_reward/unit_risk < policy.minimum_net_rr: raise RiskRejected('Net-of-cost R:R below policy')
    budget=min(number(budget_usdt,positive=True),equity*policy.per_trade_equity_pct)
    total, directional, group=(portfolio or {}).get('total',0),(portfolio or {}).get(side,0),(portfolio or {}).get('group',0)
    budget=min(budget,equity*policy.portfolio_stop_pct-total,equity*policy.direction_stop_pct-directional,equity*policy.group_stop_pct-group)
    margin_room=min(available*policy.available_margin_fraction,policy.single_asset_margin_usdt-number(existing_margin))
    size=floor_step(min(number(requested_size,positive=True),budget/unit_risk,margin_room*leverage/(ct*entry)),lot)
    if size<minimum or size<=0: raise RiskRejected('Risk budget cannot fund minimum lot; skip, never round up')
    return {'size':size,'risk_usdt':size*unit_risk,'risk_budget_usdt':budget,'margin_usdt':size*ct*entry/leverage,
            'notional_usdt':size*ct*entry,'net_rr':unit_reward/unit_risk,'entry':entry,'stop':stop,'take_profit':take_profit,
            'side':side,'instId':metadata['instId'],'leverage':leverage}

def exposure(positions, pending, algos, metadata, policy=None):
    """Worst stop giveback from marked equity. Unknown coverage blocks new exposure."""
    policy=policy or Policy(); result={'total':0.,'long':0.,'short':0.,'group':0.,'correlated':0.}
    for p in positions:
        size=abs(number(p.get('pos',0)))
        if not size: continue
        inst=p['instId']; ct,*_=linear_metadata(metadata[inst]); side=p.get('posSide')
        if side=='net': side='long' if number(p['pos'])>0 else 'short'
        if side not in {'long','short'}: raise RiskRejected('Unknown position direction')
        mark=number(p.get('markPx'),positive=True)
        from scripts.protection_policy import oco_coverage
        if not isinstance(algos, (list, tuple)) or any(not isinstance(a, dict) or not a.get('instId') for a in algos):
            raise RiskRejected('Unknown/incomplete stop coverage on existing position')
        # Marked equity includes unrealized profit: a trailing stop may cross entry,
        # but ambiguous/triggered orders cannot authorize additional exposure.
        coverage = oco_coverage([a for a in algos if a.get('instId') == inst], side, mark_px=mark)
        if coverage.unknown or coverage.size < size:
            raise RiskRejected('Unknown/incomplete stop coverage on existing position')
        rows = coverage.orders
        stop=min(number(a['slTriggerPx']) for a in rows) if side=='long' else max(number(a['slTriggerPx']) for a in rows)
        loss=max(0,mark-stop if side=='long' else stop-mark)
        liq=number(p.get('liqPx') or 0)
        if liq and ((side=='long' and stop<=liq) or (side=='short' and stop>=liq)): raise RiskRejected('Existing stop beyond liquidation boundary')
        risk=size*ct*(loss+mark*(policy.maker_fee+policy.taker_fee+policy.slippage))
        result[side]+=risk; result['total']+=risk
        if inst.split('-')[0].upper() in {'BTC','ETH','SOL','DOGE'}: result['correlated']+=risk
    for p in pending:
        if str(p.get('reduceOnly','false')).lower() in {'true','1'}: continue
        side=p.get('posSide'); inst=p['instId']; ct,*_=linear_metadata(metadata[inst])
        if side not in {'long','short'}: raise RiskRejected('Unknown pending direction')
        size=max(0,number(p.get('sz'))-number(p.get('accFillSz') or 0)); entry=number(p.get('px'),positive=True)
        attachments=p.get('attachAlgoOrds') or [p]
        stops=[number(a.get('slTriggerPx') or 0) for a in attachments if number(a.get('slTriggerPx') or 0)>0]
        if not stops: raise RiskRejected('Pending order stop unavailable; reserve unknown risk by blocking')
        stop=min(stops) if side=='long' else max(stops)
        risk=size*ct*(abs(entry-stop)+entry*(policy.maker_fee+policy.taker_fee+policy.slippage))
        result[side]+=risk; result['total']+=risk
        if inst.split('-')[0].upper() in {'BTC','ETH','SOL','DOGE'}: result['correlated']+=risk
    # Until measured groups are approved, treat all configured crypto swaps as one group.
    result['group']=result['total']
    return result

def update_equity_state(previous, *, equity, at, cash_flow, complete, policy=None):
    policy=policy or Policy(); equity=number(equity,positive=True); at=number(at,positive=True)
    if not complete: raise RiskRejected('External cash-flow reconciliation incomplete')
    day=datetime.fromtimestamp(at,timezone(timedelta(hours=8))).date().isoformat()
    old=previous or {}; flow=number(cash_flow)
    if old and at<=old['at']: raise RiskRejected('Equity observation did not advance')
    # Add external flows to historical anchors, not to performance.
    anchor=number(old.get('day_anchor',equity))+flow if old.get('day')==day else equity
    peak=max(equity,number(old.get('peak',equity))+flow)
    if anchor<=0 or peak<=0: raise RiskRejected('Invalid adjusted equity anchor')
    daily=max(0,(anchor-equity)/anchor); drawdown=max(0,(peak-equity)/peak)
    return {'external_flow_origin':number(old.get('external_flow_origin',old.get('at',at)),positive=True),'external_flow_total':number(old.get('external_flow_total',0))+flow,'day':day,'at':at,'equity':equity,'day_anchor':anchor,'peak':peak,'daily_drawdown':daily,
            'peak_drawdown':drawdown,'blocked':daily>=policy.daily_drawdown_pct or drawdown>=policy.peak_drawdown_pct,
            'baseline':'observed_equity_not_reconstructed_history'}
