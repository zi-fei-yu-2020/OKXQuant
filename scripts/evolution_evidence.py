"""Read-only settled review evidence. No orders, model calls or database creation.

Only an exact archived fill -> client intent -> decision chain supplies features.
Time/instrument proximity is never used to infer a missing decision. Descriptive
cohort outcomes are feedback, not a causal test or authorization to promote rules.
"""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3


def observed(value):
    if value is None or isinstance(value, bool) or not str(value).strip():
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def canonical_value(row, key, legacy=None):
    # An explicit unknown is authoritative, even if an old fallback is populated.
    return observed(row[key] if key in row else row.get(legacy) if legacy else None)


def review_rows(raw, scope, instruments, reset_time='1970-01-01 00:00:00'):
    if not isinstance(raw, list):
        raise ValueError('Review ledger must be a list')
    identities = Counter(str(r.get('id')) for r in raw if isinstance(r, dict)
                         and r.get('id') not in (None, '') and r.get('status') == 'closed'
                         and {r[k] for k in ('environment_id', 'account_source_id') if r.get(k)} == {scope})
    result, excluded = [], Counter()
    for row in raw:
        if not isinstance(row, dict):
            excluded['malformed_row'] += 1
            continue
        if row.get('status') != 'closed':
            continue
        reason = None
        identity = str(row.get('id') or '')
        inst = str(row.get('inst') or row.get('name') or '').removesuffix('-USDT-SWAP')
        stamp = str(row.get('close_time') or row.get('time') or '')
        pnl = canonical_value(row, 'net_pnl', 'pnl')
        if {row[k] for k in ('environment_id', 'account_source_id') if row.get(k)} != {scope}:
            reason = 'unverified_scope'
        elif not identity or identities[identity] != 1:
            reason = 'missing_or_duplicate_trade_id'
        elif row.get('settlement_status') in ('pending', 'unknown', 'estimated', 'unverified'):
            reason = 'unsettled'
        elif pnl is None:
            reason = 'unknown_net_pnl'
        elif not stamp or stamp == '--' or stamp < reset_time:
            reason = 'outside_review_window'
        elif inst not in instruments:
            reason = 'outside_instrument_pool'
        if reason:
            excluded[reason] += 1
            continue
        result.append({'inst': inst, 'trade_id': identity, 'time': stamp,
                       'open_time': row.get('open_time', ''), 'strategy': row.get('strategy') or '未归档策略',
                       'side': row.get('side'), 'margin': observed(row.get('margin')),
                       'gross_pnl': observed(row.get('gross_pnl')), 'fee': observed(row.get('fee')),
                       'funding_fee': observed(row.get('funding_fee')), 'net_pnl': pnl,
                       'exit_reason': row.get('exit_reason') or row.get('remark') or '',
                       'fee_allocation': row.get('fee_allocation'),
                       'fee_reconciliation': row.get('fee_reconciliation', {})})
    return sorted(result, key=lambda r: (r['time'], r['trade_id'])), dict(excluded)


def _event(db, scope, identity, kind):
    row = db.execute('SELECT payload,digest FROM events WHERE scope=? AND id=? AND kind=?',
                     (scope, identity, kind)).fetchone()
    if row is None or hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
        raise ValueError('Missing or invalid archived event')
    payload = json.loads(row[0])
    if not isinstance(payload, dict):
        raise ValueError('Invalid event payload')
    return payload


def decision_context(db, scope, trade):
    receipt = trade.get('fee_reconciliation') or {}
    ids = receipt.get('bill_ids') if isinstance(receipt, dict) else None
    if (trade.get('fee_allocation') != 'verified_from_archived_fills' or not isinstance(receipt, dict)
            or receipt.get('status') != 'verified' or not isinstance(ids, list) or not 1 <= len(ids) <= 80):
        return {'status': 'unobservable', 'reason': 'missing_verified_lifecycle_fills'}
    inst = trade['inst'] + '-USDT-SWAP'
    zone = timezone(timedelta(hours=8))
    opened = datetime.strptime(trade['open_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=zone).timestamp()*1000
    closed = datetime.strptime(trade['time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=zone).timestamp()*1000 + 1000
    if opened >= closed:
        raise ValueError('Invalid lifecycle clock')
    side = {'多': 'long', '空': 'short', 'long': 'long', 'short': 'short'}.get(trade.get('side'))
    if side is None:
        return {'status': 'unobservable', 'reason': 'unknown_position_side'}
    opening = 'buy' if side == 'long' else 'sell'
    decisions, missing = {}, 0
    for bill in sorted(set(ids)):
        fill = _event(db, scope, 'fill:' + scope + ':' + str(bill), 'fill')
        fill_at = observed(fill.get('ts'))
        if (fill.get('instId') != inst or fill.get('posSide') != side or str(fill.get('billId')) != str(bill)
                or not fill.get('ordId') or fill_at is None or not opened <= fill_at < closed):
            raise ValueError('Fill scope/instrument/side mismatch')
        if fill.get('side') != opening:
            continue
        client = fill.get('clOrdId')
        intent = db.execute('SELECT decision_id,inst_id,at FROM intents WHERE scope=? AND id=?',
                            (scope, client)).fetchone() if client else None
        if intent is None:
            missing += 1
            continue
        decision = _event(db, scope, intent[0], 'decision')
        generated = observed(decision.get('generated_at_ms'))
        as_of = observed(decision.get('as_of_ms'))
        filled = observed(fill.get('ts'))
        if (intent[1] != inst or decision.get('instrument') != inst or decision.get('counterfactual') is not False
                or None in (generated, as_of, filled) or not 0 < as_of <= generated <= intent[2]*1000 <= filled):
            raise ValueError('Decision chronology or identity mismatch')
        d = decision.get('decision') or {}
        features = decision.get('features') or {}
        if not isinstance(d, dict) or not isinstance(features, dict):
            raise ValueError('Invalid decision features')
        if d.get('action') not in (('BUY_LONG', 'ADD_LONG') if side == 'long' else ('SELL_SHORT', 'ADD_SHORT')):
            raise ValueError('Decision action does not match opening fill')
        # Bounded numeric/structured archived observations; never use current market values.
        selected = {k: features[k] for k in ('price', 'atr', 'atr_1h', 'atr_15m', 'adx_1h', 'rsi_1h', 'calculus', 'calculus_1h',
                    'calculus_4h', 'smart_money', 'oiUsd', 'takerNetUsd', 'data_as_of') if k in features}
        if len(json.dumps(selected, allow_nan=False)) > 12000:
            raise ValueError('Oversized decision snapshot')
        decisions[intent[0]] = {'decision_id': intent[0], 'generated_at_ms': generated,
                              'as_of_ms': as_of, 'features': selected,
                              'action': d.get('action'), 'candidate_id': d.get('candidate_id'),
                              'memory_publication': d.get('memory_publication'),
                              'strategy_version': decision.get('strategy_version'),
                              'execution_profile_signature': decision.get('execution_profile_signature')}
    if len(json.dumps(decisions, allow_nan=False)) > 24000:
        return {'status': 'unobservable', 'reason': 'lifecycle_snapshot_budget_exceeded'}
    return {'status': 'linked' if decisions and not missing else 'partial' if decisions else 'unobservable',
            'reason': 'exact_fill_intent_decision_chain' if decisions else 'no_archived_automatic_entry',
            'missing_opening_links': missing, 'entries': [decisions[k] for k in sorted(decisions)]}


def enrich(rows, scope, data_dir):
    path = Path(data_dir) / 'strategy_evidence.db'
    result = [{**r, 'decision_evidence': {'status': 'unobservable', 'reason': 'archive_missing'}} for r in rows]
    if not path.is_file():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
            db.execute('BEGIN')
            for row in result:
                try:
                    row['decision_evidence'] = decision_context(db, scope, row)
                except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    row['decision_evidence'] = {'status': 'unobservable', 'reason': 'archive_chain_unverified'}
    except (sqlite3.Error, OSError):
        for row in result:
            row['decision_evidence'] = {'status': 'unobservable', 'reason': 'archive_unavailable'}
    return result


def feedback(rows):
    """Deterministic cost/outcome feedback, computed before the model's prose."""
    fees = [observed(r.get('fee')) for r in rows]
    gross = [observed(r.get('gross_pnl')) for r in rows]
    pnls = [observed(r.get('net_pnl')) for r in rows]
    pnls = [p for p in pnls if p is not None]
    groups = defaultdict(list)
    for row in rows:
        context = row.get('decision_evidence') or {}
        entries = context.get('entries') or []
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            continue
        triples = []
        for e in entries:
            memory = e.get('memory_publication')
            value = (memory.get('prompt_hash') if isinstance(memory, dict) else None,
                     e.get('strategy_version'), e.get('execution_profile_signature'))
            if not all(isinstance(v, str) and v for v in value):
                break
            triples.append(value)
        if context.get('status') == 'linked' and len(triples) == len(entries) and len(set(triples)) == 1:
            groups[triples[0]].append(row)
    loss = -sum(p for p in pnls if p < 0)
    return {'settled_samples': len(pnls), 'wins': sum(p > 0 for p in pnls), 'losses': sum(p < 0 for p in pnls),
            'breakeven': sum(p == 0 for p in pnls), 'net_pnl': sum(pnls),
            'profit_factor': sum(p for p in pnls if p > 0)/loss if loss > 0 else None,
            'known_fee_samples': sum(f is not None for f in fees),
            'fee_cashflow': sum(f for f in fees if f is not None) if all(f is not None for f in fees) else None,
            'fee_cost': sum(max(0, -f) for f in fees if f is not None) if all(f is not None for f in fees) else None,
            'rebates': sum(max(0, f) for f in fees if f is not None) if all(f is not None for f in fees) else None,
            'friction_reversed_trades': sum(g is not None and g > 0 and r['net_pnl'] <= 0 for r, g in zip(rows, gross)),
            'entry_snapshot_samples': sum((r.get('decision_evidence') or {}).get('status') == 'linked' for r in rows),
            'partial_snapshot_samples': sum((r.get('decision_evidence') or {}).get('status') == 'partial' for r in rows),
            'memory_cohorts': [{'prompt_hash': h, 'strategy_version': s, 'execution_profile_signature': x,
                               'samples': len(values), 'net_pnl': sum(r['net_pnl'] for r in values),
                               'status': 'descriptive_only'} for (h, s, x), values in sorted(groups.items())],
            'auto_promote': False, 'validation_status': 'forward_comparison_required'}
