"""Account-scoped statistical restarts; operational receipts and risk state stay intact.

No reset can enable trading, place/cancel an order, modify stops or erase day risk.
The atomic baseline record is the authoritative epoch; readers filter old cohorts
so delayed exchange reconciliation cannot restore historical display/review totals.
"""
from datetime import datetime
import json
import math
from pathlib import Path
import time
from scripts.dashboard_stats import normalize_timestamp, SHANGHAI
from scripts.json_projection_cache import VersionedJsonProjection


def finite(value):
    if value is None or isinstance(value, bool): return None
    try: value = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return value if math.isfinite(value) else None


def owner(row):
    marks = [row.get(k) for k in ('account_scope', 'environment_id', 'account_source_id') if row.get(k)]
    return marks[0] if marks and all(isinstance(v, str) and v == marks[0] for v in marks) else None


def _project(document):
    if not isinstance(document, dict): raise ValueError('Invalid statistics baseline')
    accounts = document.get('baselines') or {}
    if not isinstance(accounts, dict): raise ValueError('Invalid statistics accounts')
    records = dict(accounts)
    if document.get('account_scope'):
        records[document['account_scope']] = document
    result = {}
    for scope, record in records.items():
        if not isinstance(record, dict): raise ValueError('Invalid scoped statistics baseline')
        if not record.get('statistics_epoch_id'): continue
        started = finite(record.get('statistics_started_at'))
        capital = finite(record.get('initial_capital'))
        ids = record.get('statistics_excluded_ids')
        if not isinstance(scope, str) or record.get('account_scope') != scope or started is None or started <= 0 or capital is None or capital <= 0 or not isinstance(ids, list) or any(not isinstance(v, str) for v in ids):
            raise ValueError('Invalid statistics epoch')
        result[scope] = {'id': str(record['statistics_epoch_id']), 'scope': scope, 'started_at': started,
            'reset_time': datetime.fromtimestamp(started, SHANGHAI).strftime('%Y-%m-%d %H:%M:%S'),
            'initial_capital': capital, 'excluded_ids': frozenset(ids)}
    return result


_EPOCHS = VersionedJsonProjection(_project)


def epochs(data_dir=None):
    from okxquant_backend.account_baseline import BASELINE_FILE
    path = Path(data_dir) / 'account_initial_state.json' if data_dir is not None else BASELINE_FILE
    if not path.exists(): return {}
    return _EPOCHS.read(path)


def epoch(scope, data_dir=None):
    return epochs(data_dir).get(scope)


def public_epoch(scope, data_dir=None):
    value = epoch(scope, data_dir)
    if not value: return None
    return {k: v for k, v in value.items() if k != 'excluded_ids'}


def filter_rows(rows, *, scope=None, data_dir=None, keep_active=True):
    windows = epochs(data_dir)
    if not windows: return rows
    result = []
    for row in rows:
        if not isinstance(row, dict): continue
        who = owner(row); window = windows.get(who)
        if not window or scope is not None and who != scope:
            result.append(row); continue
        if keep_active and row.get('status') == 'holding':
            result.append(row); continue  # Never hide a real position for statistical cleanliness.
        if str(row.get('id') or '') in window['excluded_ids']: continue
        opened = normalize_timestamp(row.get('open_time'))
        # Late corrections to old/unknown openings are not new experiment samples.
        if opened is not None and opened.timestamp() >= window['started_at']:
            result.append(row)
    return result


def report_in_epoch(report, scope, data_dir=None):
    window = epoch(scope, data_dir)
    if not window: return report
    if not isinstance(report, dict) or owner(report) != scope: return {}
    at = normalize_timestamp(report.get('completed_at') or report.get('timestamp') or report.get('last_attempt_at'))
    return report if at and at.timestamp() >= window['started_at'] else {}


def performance(rows):
    outcomes = [(r, finite(r.get('net_pnl', r.get('pnl')))) for r in rows if r.get('status') == 'closed']
    known = [(r, pnl) for r, pnl in outcomes if pnl is not None]
    wins = [p for _, p in known if p > 0]; losses = [p for _, p in known if p < 0]
    win_amount = sum(wins); loss_amount = -sum(losses)
    insts = {}
    for row, pnl in known:
        name = row.get('inst') or row.get('instId')
        group = insts.setdefault(name, {'inst': name, 'trades': 0, 'wins': 0, 'losses': 0, 'net_pnl': 0.0})
        group['trades'] += 1; group['wins'] += pnl > 0; group['losses'] += pnl < 0; group['net_pnl'] += pnl
    for group in insts.values():
        group['win_rate'] = round(100*group['wins']/group['trades'], 1)
        group['net_pnl'] = round(group['net_pnl'], 8)
        group['pnl'] = group['net_pnl']
    return {'source': 'lifecycle_ledger_statistics_epoch', 'all_trades': len(known), 'win_trades': len(wins),
        'loss_trades': len(losses), 'breakeven_trades': len(known)-len(wins)-len(losses),
        'unknown_outcome_trades': len(outcomes)-len(known),
        'win_rate': round(100*len(wins)/len(known), 1) if known else 0.0,
        'profit_factor': round(win_amount/loss_amount, 2) if loss_amount else None,
        'total_win_amt': round(win_amount, 8), 'total_loss_amt': round(loss_amount, 8),
        'avg_win': win_amount/len(wins) if wins else 0.0, 'avg_loss': loss_amount/len(losses) if losses else 0.0,
        'leaderboard': sorted(insts.values(), key=lambda r: r['net_pnl'], reverse=True)}


def project_snapshot(data, rows, scope, data_dir=None):
    """Also apply to already-cached snapshots, so a reset is visible immediately."""
    window = epoch(scope, data_dir)
    if not window: return data
    from scripts.dashboard_stats import scoped_rows
    trades = scoped_rows(filter_rows(rows, scope=scope, data_dir=data_dir), scope)
    out = dict(data); out['statistics_epoch'] = public_epoch(scope, data_dir)
    out['trades'] = filter_rows(out.get('trades') or [], scope=scope, data_dir=data_dir)
    out['performance'] = performance(trades)
    from scripts.horizon_stats import rebuild
    periods=rebuild(trades,scope=scope)
    cached=out.get('horizon_stats') or {}
    if cached.get('statistics_epoch_id')==window['id']:
        for key in ('funnel','allocation'):
            if key in cached:periods[key]=cached[key]
    out['horizon_stats']={**periods,'statistics_epoch_id':window['id']}
    from scripts.dashboard_stats import today_lifecycle_stats
    today=today_lifecycle_stats(trades,datetime.now(SHANGHAI).strftime('%Y-%m-%d'))
    today.pop('settled_rows',None)
    floating=finite((data.get('account') or {}).get('pos_upl_total'))
    today['total_pnl']=round(today['net_realized']+floating,2) if today.get('net_realized') is not None and floating is not None else None
    out['today_stats']=today
    account = dict(out.get('account') or {}); capital = window['initial_capital']
    eq = finite(account.get('total_eq')); upl = finite(account.get('pos_upl_total'))
    observed_at = normalize_timestamp(str(data.get('timestamp') or '').split(' (', 1)[0])
    current = observed_at is not None and observed_at.timestamp() >= window['started_at']
    pnl = round(eq-capital, 2) if eq is not None and current else None
    account.update(initial_capital=capital, baseline_configured=True, cum_net_pnl=pnl,
        cum_roi_pct=round(pnl/capital*100, 2) if pnl is not None else None,
        cum_realized_pnl=round(pnl-upl, 2) if pnl is not None and upl is not None else None)
    fees = [finite(r.get('fee')) for r in trades if r.get('status') == 'closed']
    account['cum_total_fees'] = sum(fees) if all(v is not None for v in fees) else None
    out['account'] = account
    anchor = {'time': window['reset_time'], 'total_eq': capital, 'pnl': 0.0, 'roi': 0.0}
    snaps = [anchor]
    for row in out.get('snapshots') or []:
        at = normalize_timestamp(row.get('time'))
        total = finite(row.get('total_eq', row.get('equity')))
        if at and at.timestamp() > window['started_at'] and total is not None:
            snaps.append({**row, 'pnl': round(total-capital, 2), 'roi': round((total-capital)/capital*100, 2)})
    out['snapshots'] = snaps
    out['review'] = report_in_epoch(out.get('review') or {}, scope, data_dir)
    from scripts.evolution_status import public_status as review_status
    out['evolution_review'] = review_status(data_dir,scope=scope)
    settlement = dict(out.get('funding_settlements') or {})
    settlement['items'] = [r for r in settlement.get('items') or []
        if (at := normalize_timestamp(r.get('time'))) is not None and at.timestamp() >= window['started_at']]
    settlement['total_funding_pnl']=today.get('funding_paid')
    out['funding_settlements'] = settlement
    return out


def reset_demo_statistics(operation_id, *, expected_scope, expected_epoch_id=None):
    """Explicit maintenance operation. Must run paused/drained with a flat DEMO account.

    Updates only the statistical baseline. Raw receipts, live day-risk anchors,
    operational intents, model/error telemetry and published strategy rules remain.
    Repeating the same operation ID does not move the boundary a second time.
    """
    if not isinstance(operation_id, str) or not 8 <= len(operation_id) <= 100:
        raise ValueError('Invalid reset operation ID')
    from scripts.okx_runtime import selected_environment, _load_dotenv
    from scripts.trade_lock import writer
    from scripts.config_lock import configuration_write
    from okxquant_backend import account_baseline as baseline
    from okxquant_backend.okx_trade_service import _request
    env = selected_environment()
    if env.mode != 'demo' or env.identity != expected_scope or not env.configured:
        raise ValueError('Statistics reset requires the explicitly selected configured DEMO account')
    if _load_dotenv().get('OKXQUANT_AUTOTRADE_ENABLED') != '0':
        raise ValueError('Pause and drain automatic entry cycles before resetting statistics')
    def identity(e):return (e.identity, e.mode, getattr(e,'connection_id',''), getattr(e,'binding_version',0))
    with writer(timeout=30, account=env.identity), configuration_write(baseline.BASELINE_FILE):
        document = baseline._read(strict=True); old = baseline._project(document, env.identity)
        if old.get('statistics_epoch_id') == operation_id:
            return {'status': 'already_reset', **public_epoch(env.identity)}
        if old.get('statistics_epoch_id') != expected_epoch_id:
            raise ValueError('Statistics epoch changed; inspect before resetting again')
        positions = _request('GET', '/api/v5/account/positions', {}, env, timeout=8)
        pending = _request('GET', '/api/v5/trade/orders-pending', {}, env, timeout=8)
        balance = _request('GET', '/api/v5/account/balance', {}, env, timeout=8)
        if not isinstance(positions,list) or not isinstance(pending,list) or not isinstance(balance,list) or not balance:
            raise ValueError('Reset account snapshot is unavailable')
        if pending or any(not isinstance(p,dict) or finite(p.get('pos')) is None or abs(float(p['pos']))>0 for p in positions):
            raise ValueError('Account has positions or pending orders; no automatic close or cancellation performed')
        capital = finite(balance[0].get('totalEq')) if isinstance(balance[0],dict) else None
        if capital is None or not baseline.MIN_CAPITAL <= capital <= baseline.MAX_CAPITAL:
            raise ValueError('Verified opening account equity is unavailable')
        path = baseline.BASELINE_FILE.parent/'trading_ledger.json'
        if baseline.BASELINE_FILE.is_symlink() or path.is_symlink():
            raise ValueError('Linked baseline/ledger paths are not supported by reset')
        rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
            raise ValueError('Canonical ledger unreadable; no reset performed')
        own = [r for r in rows if owner(r)==env.identity]
        if any(r.get('status')=='holding' for r in own):
            raise ValueError('Ledger still reports a holding; reconcile before resetting statistics')
        excluded = sorted({str(r['id']) for r in own if r.get('id')})
        if identity(selected_environment()) != identity(env):
            raise ValueError('Account binding changed before statistics reset')
        now = int(time.time()); stamp = datetime.fromtimestamp(now, SHANGHAI).strftime('%Y-%m-%d %H:%M:%S')
        record = {**old, 'initial_capital': round(capital,2), 'baseline_configured': True, 'baseline_source':'scoped',
            'account_scope':env.identity, 'reset_time':stamp, 'statistics_epoch_id':operation_id,
            'statistics_started_at':now, 'statistics_excluded_ids':excluded, 'statistics_opening_equity':round(capital,2),
            'risk_reset_time':old.get('risk_reset_time',old.get('reset_time','1970-01-01 00:00:00')),
            'canonical_history_start':old.get('canonical_history_start',old.get('reset_time','1970-01-01 00:00:00')),
            'statistics_reset_basis':'authenticated_flat_account_total_equity', 'statistics_rows_archived':len(own)}
        baseline._store(document, record)
        return {'status':'reset', **public_epoch(env.identity), 'archived_trade_rows':len(own),
            'operational_records_deleted':False, 'positions_or_orders_modified':False,
            'risk_anchors_preserved':True, 'published_memory_preserved':True}
