"""Pure receipt accounting; absence is not a zero or an invented margin."""
from scripts.evolution_evidence import observed


def receipt_financials(receipt, *, entry_price, size, contract_value, leverage):
    gross, fee, funding = (observed(receipt.get(k)) for k in ('pnl', 'fee', 'fundingFee'))
    if 'realizedPnl' in receipt:
        net = observed(receipt['realizedPnl'])
        basis = 'exchange_realized_pnl' if net is not None else 'unavailable'
    elif all(v is not None for v in (gross, fee, funding)):
        net = gross + fee + funding
        basis = 'gross_plus_fee_plus_funding'
    else:
        net, basis = None, 'unavailable'
    values = [observed(v) for v in (entry_price, size, contract_value, leverage)]
    margin = None
    if all(v is not None and v > 0 for v in values):
        entry, amount, ct, lev = values
        margin = observed(entry * amount * ct / lev)
    # Never use a fictional 500U balance or PnL-derived margin as an observation.
    roi = observed(net / margin * 100) if net is not None and margin is not None and margin > 0 else None
    return {'gross_pnl': gross, 'fee': fee, 'funding_fee': funding, 'net_pnl': observed(net),
            'margin': margin, 'roi_pct': roi, 'accounting_basis': basis}
