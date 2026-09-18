"""Shared cost scenarios in price units per base unit. No leverage multiplication.
The maker-entry scenario is a compatibility admission reference, NOT a fill promise.
Funding is not a trading fee and is reconciled separately from actual receipts.
"""
import math
from collections.abc import Mapping

VERSION='execution-costs-v1'

def number(x,positive=False):
    if isinstance(x,bool):raise ValueError('Invalid cost input')
    try:x=float(x)
    except (TypeError,ValueError,OverflowError):raise ValueError('Invalid cost input') from None
    if not math.isfinite(x) or x<0 or (positive and x<=0):raise ValueError('Invalid cost input')
    return x


def estimate(entry,exit_price,*,maker_fee,taker_fee,slippage,spread=0):
    entry=number(entry,True);exit_price=number(exit_price,True)
    maker,taker,slip,spread=map(number,(maker_fee,taker_fee,slippage,spread))
    if max(maker,taker,slip)>=1:raise ValueError('Cost fractions must be below one')
    maker_open=entry*maker;taker_open=entry*taker;close=exit_price*taker;slip_cost=entry*slip
    reference=maker_open+close+slip_cost+spread
    conservative=taker_open+close+slip_cost+spread
    # Preserve the existing exit policy's equal-price arithmetic exactly.
    if entry==exit_price:conservative=entry*(2*taker+slip)+spread
    return {'version':VERSION,'units':'price_per_base_unit','entry_price':entry,'exit_reference_price':exit_price,
            'entry_maker_fee':maker_open,'entry_taker_fee':taker_open,'exit_taker_fee':close,
            'slippage_budget':slip_cost,'spread_budget':spread,
            'maker_taker_total':reference,'taker_taker_total':conservative,
            'admission_basis':'maker_taker_compatibility_reference_not_fill_promise',
            'conservative_basis':'taker_taker_with_slippage','maker_taker_rate_at_entry':maker+taker+slip,'funding_included':False}


def from_policy(entry,exit_price,policy,spread=0):
    p=policy if isinstance(policy,Mapping) else vars(policy)
    return estimate(entry,exit_price,maker_fee=p['maker_fee'],taker_fee=p['taker_fee'],slippage=p['slippage'],spread=spread)


def reference_at_entry(entry,policy):
    """Compatibility arithmetic for existing equal-price risk/exit budgets."""
    snapshot=from_policy(entry,entry,policy)
    return snapshot['entry_price']*snapshot['maker_taker_rate_at_entry']


def compare_actual(snapshot,history,fee):
    """Fee assumptions at actual receipt VWAP; never change official PnL or fee fields."""
    if not isinstance(snapshot,dict) or snapshot.get('version')!=VERSION:
        return {'status':'unavailable','reason':'missing_submission_cost_snapshot'}
    try:
        units=number(snapshot.get('contract_base_units'),True)*number(history.get('closeTotalPos'),True)
        entry=number(history.get('openAvgPx'),True);close=number(history.get('closeAvgPx'),True)
        planned=number(snapshot['entry_price'],True)
        maker=number(snapshot['entry_maker_fee'])/planned;taker=number(snapshot['entry_taker_fee'])/planned
        if isinstance(fee,bool):raise ValueError('Invalid fee')
        actual=-float(fee)
        if not math.isfinite(actual):raise ValueError('Invalid fee')
        reference=units*(entry*maker+close*taker);conservative=units*(entry*taker+close*taker)
        return {'status':'estimated_comparison','version':VERSION,'official_fee_cost':actual,
                'maker_taker_fee_at_receipt_prices':reference,'taker_taker_fee_at_receipt_prices':conservative,
                'actual_minus_reference_fee':actual-reference,'exit_liquidity_assumption':'taker',
                'within_fee_scenarios':min(reference,conservative)-1e-6<=actual<=max(reference,conservative)+1e-6,
                'includes_slippage_or_funding':False,'accounting_changed':False}
    except (ValueError,TypeError,KeyError,OverflowError):
        return {'status':'unavailable','reason':'incomplete_cost_or_settlement_evidence'}
