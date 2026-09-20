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


def lifecycle_id(position, scope):
    """Stable account-scoped identity, independent of mutable receipt uTime."""
    import hashlib
    import json
    side = position.get('direction') or position.get('posSide')
    parts = [scope, position.get('instId'), side,
             str(position.get('posId') or ''), str(position.get('cTime') or '')]
    if not all(parts):
        # Do not collapse unidentifiable receipts into a fabricated lifecycle.
        parts.append(str(position.get('uTime') or ''))
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


def matches_lifecycle(row, position, scope):
    """Match legacy display rows only with account, position ID and opening time.

    New rows preserve millisecond creation time; legacy rows only stored seconds.
    Missing identity is not permission to retire pending evidence.
    """
    from scripts.ledger_monitor import bj
    side = position.get('direction') or position.get('posSide')
    display_side = {'long': '\u591a', 'short': '\u7a7a'}.get(side, side)
    if (row.get('environment_id') != scope or row.get('instId') != position.get('instId')
            or row.get('side') not in (side, display_side)
            or not row.get('pos_id') or not position.get('posId')
            or str(row['pos_id']) != str(position['posId']) or not position.get('cTime')):
        return False
    if row.get('position_created_at'):
        return str(row['position_created_at']) == str(position['cTime'])
    return bool(row.get('open_time')) and row['open_time'] == bj(position['cTime'])


FEE_EVIDENCE_FIELDS = ('open_fee', 'close_fee', 'fee_allocation', 'fee_reconciliation',
                       'opening_order_ids', 'opening_trade_ids')
ORIGIN_EVIDENCE_FIELDS = ('strategy', 'strategy_evidence', 'source_status', 'strategy_decision_id',
    'decision_id', 'candidate_id', 'strategy_version', 'setup', 'opening_features', 'news_snapshot',
    'evidence_status', 'decision_horizon', 'execution_horizon', 'strategy_engine',
    'execution_cost_model', 'selection_research', 'strategy_type', 'horizon')
ATTRIBUTION_EVIDENCE_FIELDS = ('exit_reason', 'exit_source', 'exit_evidence', 'attribution_status',
                              'close_order_ids', 'close_order_sources', 'attribution_note')


def retain_verified_evidence(row, previous, archive_status):
    """Keep prior proof without applying stale accounting to a changed receipt.

    Temporary archive/read-window loss does not refute already verified proof.
    Actual contradictions/invalid digests never upgrade current reconciliation.
    Retain one prior verified snapshot even when the receipt has changed.
    """
    from copy import deepcopy
    if not previous:
        return
    fields = FEE_EVIDENCE_FIELDS + ORIGIN_EVIDENCE_FIELDS + ATTRIBUTION_EVIDENCE_FIELDS + ('exit_snapshot',)
    verified_fee = (previous.get('fee_reconciliation') or {}).get('status') == 'verified'
    verified_origin = previous.get('source_status') == 'linked'
    verified_exit = previous.get('attribution_status') in ('verified', 'mixed', 'corroborated')
    prior = ({key: deepcopy(previous[key]) for key in fields if key in previous}
             if verified_fee or verified_origin or verified_exit else previous.get('prior_lifecycle_evidence'))
    if prior:
        row['prior_lifecycle_evidence'] = deepcopy(prior)
    if not row.get('exit_snapshot') or row['exit_snapshot'] != previous.get('exit_snapshot'):
        return
    restored = []
    if verified_fee and archive_status in ('missing', 'unavailable', 'capacity_exceeded'):
        for key in FEE_EVIDENCE_FIELDS:
            if key in previous: row[key] = deepcopy(previous[key])
        restored.append('fees')
    if (verified_origin and row.get('source_status') != 'linked'
            and row.get('opening_order_ids') == previous.get('opening_order_ids')
            and (row.get('fee_reconciliation') or {}).get('status') == 'verified'):
        for key in ORIGIN_EVIDENCE_FIELDS:
            if key in previous: row[key] = deepcopy(previous[key])
        restored.append('origin')
    if restored:
        row['evidence_refresh'] = {'basis': 'previous_verified_identical_receipt',
                                   'archive_status': archive_status, 'retained': restored}
