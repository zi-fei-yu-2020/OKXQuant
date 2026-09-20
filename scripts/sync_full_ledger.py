#!/usr/bin/env python3
"""
OKXQuant Authentic OKX Positions-History Ledger Synchronizer (sync_full_ledger.py)
Directly reads OKX official `account positions-history` & `account positions` API.
Eliminates bills heuristic split-error, accurately records real position-level trades!
"""

# Standalone scheduler children must not depend on an inherited PYTHONPATH.
import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[1])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)


from okx_runtime import selected_environment, replace_cli_prefix as okx_private_command
import subprocess
import json
import os
import datetime
import tempfile
from scripts import ledger_monitor
from scripts.ledger_accounting import lifecycle_id, matches_lifecycle, retain_verified_evidence
from scripts.ledger_duration import duration_seconds, format_duration, duration_bucket
from scripts.close_attribution import reason as close_reason
from scripts.close_evidence import load_inputs as close_inputs
from scripts.fill_accounting import read_archive as read_fill_archive, reconcile as reconcile_fill_fees

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
INITIAL_STATE_FILE = os.path.join(DATA_DIR, "account_initial_state.json")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")

from instrument_pool import load_instruments

TARGET_INSTRUMENTS = load_instruments()

def get_ct_val(inst_name):
    for item in TARGET_INSTRUMENTS:
        if item["name"] == inst_name or item["instId"] == inst_name:
            return item["ctVal"]
    return 1.0

def read_cli_list(command, *, environment=None):
    env = environment or selected_environment()
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=20, env=env.cli_env())
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError('Ledger source unavailable; existing ledger is preserved')
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise RuntimeError('Ledger source malformed; existing ledger is preserved')
    return rows


def read_snapshot(env, path, command, params):
    if env.configured:
        from okxquant_backend.okx_trade_service import _request
        rows = _request('GET',path,params,env,timeout=8)
        if not isinstance(rows,list) or any(not isinstance(x,dict) for x in rows):
            raise RuntimeError('Invalid ledger source; previous ledger preserved')
        return rows
    return read_cli_list(okx_private_command(command), environment=env)


@ledger_monitor.serialized
def build_lifecycle_ledger(*, notify=True):
    env=selected_environment()
    from okxquant_backend.account_baseline import load_account_baseline
    reset_time=load_account_baseline(scope=env.identity, path=INITIAL_STATE_FILE)['reset_time']

    existing_closed_ids = set()
    existing_closed_rows = []
    old_trades = []
    if os.path.exists(LEDGER_JSON_FILE):
        # An unreadable ledger may contain unresolved evidence. Never overwrite
        # it with a successful-looking partial reconstruction.
        with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
            old_trades = json.load(f)
        if not isinstance(old_trades, list) or any(not isinstance(t, dict) for t in old_trades):
            raise ValueError('Invalid existing ledger; preserved for reconciliation')
        existing_closed_rows = [t for t in old_trades if t.get("status") == "closed" and t.get("id") and str(t.get("close_time", "")) >= reset_time]
        existing_closed_ids = {t["id"] for t in existing_closed_rows}

    trackers = {}
    if os.path.exists(POSITION_TRACKER_FILE):
        try:
            with open(POSITION_TRACKER_FILE, "r", encoding="utf-8") as f:
                trackers = json.load(f)
        except Exception:
            pass

    tz_bj = datetime.timezone(datetime.timedelta(hours=8))

    # 1. Fetch OKX Official Positions History (Official position-level closed trades)
    pos_history=read_snapshot(env,'/api/v5/account/positions-history','okx account positions-history --limit 100 --json',{'instType':'SWAP','limit':'100'})
    cycle_start=datetime.datetime.strptime(reset_time,'%Y-%m-%d %H:%M:%S').replace(tzinfo=tz_bj).timestamp()*1000
    pos_history=[row for row in pos_history if float(row.get('uTime') or row.get('cTime') or 0) >= cycle_start]
    pos_data=read_snapshot(env,'/api/v5/account/positions','okx account positions --json',{'instType':'SWAP'})
    orders_history=read_snapshot(env,'/api/v5/trade/orders-history','okx swap orders --history --limit 100 --json',{'instType':'SWAP','limit':'100'})

    from scripts import strategy_evidence
    for receipt in pos_history:
        strategy_evidence.best_effort(env.identity, "position_receipt", receipt)

    # API overlap or updated settlement receipts are snapshots, not new trades.
    latest = {}
    for receipt in pos_history:
        key = lifecycle_id(receipt, env.identity)
        prior = latest.get(key)
        if prior is None or int(receipt.get('uTime') or 0) > int(prior.get('uTime') or 0):
            latest[key] = receipt
        elif receipt.get('uTime') == prior.get('uTime') and receipt != prior:
            raise ValueError('Conflicting lifecycle receipts; existing ledger preserved')
    pos_history = list(latest.values())

    try:
        attribution_inputs=close_inputs(env,orders_history)
    except Exception:
        attribution_inputs={'orders':orders_history,'algos':[],'executions':[]}
    fill_archive = read_fill_archive(env.identity)
    trades_lifecycle = []
    replaced_rows = set()

    from scripts.strategy_origin import index as strategy_index, resolve as strategy_origin
    origins=strategy_index(env.identity)
    from scripts.trade_quality import observation_index, annotate
    observations=observation_index(env.identity)

    live_lifecycles = {lifecycle_id(p, env.identity) for p in pos_data if abs(float(p.get('pos') or 0)) > 0}
    partial_receipts = {lifecycle_id(h, env.identity): h for h in pos_history
                        if lifecycle_id(h, env.identity) in live_lifecycles}

    # Process Active Holding Positions FIRST
    for p in pos_data:
        pos_sz = float(p.get("pos", 0.0) or 0.0)
        if pos_sz == 0.0:
            continue
        inst_id = p.get("instId", "")
        if inst_id not in {item["instId"] for item in TARGET_INSTRUMENTS}:
            continue
        inst = inst_id.replace("-USDT-SWAP", "")
        side_raw = p.get("posSide", p.get("side", "")).lower()
        side = "多" if "long" in side_raw else "空"
        avg_px = float(p.get("avgPx", 0) or 0)
        mark_px = float(p.get("markPx", 0) or 0)
        upl = float(p.get("upl", 0) or 0)
        lever = int(p.get("lever", "3") or 3)
        fee = float(p.get("fee", 0.0) or 0.0)
        ct_val = get_ct_val(inst)

        notional = pos_sz * ct_val * mark_px
        margin_usdt = round(notional / lever, 2)
        roi_pct = round((upl / margin_usdt * 100) if margin_usdt > 0 else 0.0, 2)

        # Time calculation
        c_ts = int(p.get("cTime", 0) or 0) / 1000.0
        open_time = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"

        pos_k = f"{inst_id}_{'long' if side=='多' else 'short'}"
        t_info = trackers.get(pos_k, {})
        strat_tag = strategy_origin(p,fill_archive,origins)["strategy"]

        held_seconds = duration_seconds(c_ts, datetime.datetime.now(tz_bj).timestamp())
        duration_str = format_duration(held_seconds)

        replaced_rows.update(id(row) for row in old_trades
                            if row.get('status') in {'holding', 'closed_pending'}
                            and matches_lifecycle(row, p, env.identity))
        trades_lifecycle.append({
            "id": "holding_" + lifecycle_id(p, env.identity),
            "position_created_at": str(p.get("cTime") or ""),
            "instId":inst_id,"pos_id":str(p.get("posId") or ""),"environment_id":env.identity,"environment":env.mode,
            "inst": inst,
            "side": side,
            "lever": f"{lever:g}x" if lever is not None else "--",
            "strategy": strat_tag,
            "margin": margin_usdt,
            "sz": pos_sz,
            "open_time": open_time,
            "open_px": avg_px,
            "close_time": "持仓中...",
            "close_px": mark_px,
            "gross_pnl": round(upl, 2),
            "open_fee": round(fee, 4),
            "close_fee": 0.0,
            "fee": round(fee, 2),
            "pnl": round(upl, 2),
            "net_pnl": round(upl, 2),
            "roi_pct": roi_pct,
            "duration": duration_str,
            "duration_seconds": held_seconds,
            "status": "holding",
            "exit_reason": "⏳ 运行监控中"
        })

        partial = partial_receipts.get(lifecycle_id(p, env.identity))
        if partial is None:
            partial = next((row['partial_close_receipt'] for row in old_trades
                            if matches_lifecycle(row, p, env.identity) and row.get('partial_close_receipt')), None)
        if partial is not None:
            trades_lifecycle[-1]['partial_close_receipt'] = partial

    # Process Official Closed Positions
    for h in pos_history:
        # Realized partial exits can have a history receipt while inventory remains.
        # Keep that receipt on the holding row, not in completed-trade statistics.
        if lifecycle_id(h, env.identity) in live_lifecycles:
            continue
        c_ts = int(h.get("cTime", 0) or 0) / 1000.0
        u_ts = int(h.get("uTime", 0) or 0) / 1000.0
        open_time = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
        close_time = datetime.datetime.fromtimestamp(u_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if u_ts > 0 else "--"

        if close_time < reset_time:
            continue

        inst_id = h.get("instId", "")
        if inst_id not in {item["instId"] for item in TARGET_INSTRUMENTS}:
            continue
        inst = inst_id.replace("-USDT-SWAP", "")
        direction = str(h.get("direction", "")).lower()
        side = "多" if "long" in direction else "空"
        
        open_px = float(h.get("openAvgPx", 0) or 0)
        close_px = float(h.get("closeAvgPx", 0) or 0)
        from scripts.ledger_accounting import receipt_financials
        from scripts.evolution_evidence import observed
        actual_leverage = observed(h.get("lever"))
        lever = actual_leverage if actual_leverage is not None and actual_leverage > 0 else None
        ct_val = get_ct_val(inst)
        close_pos_sz = observed(h.get("closeTotalPos"))
        amounts = receipt_financials(h, entry_price=open_px, size=close_pos_sz,
                                    contract_value=ct_val, leverage=lever)
        gross_pnl, fee, funding_fee, net_pnl, margin_usdt, roi_pct = (
            amounts[k] for k in ('gross_pnl', 'fee', 'funding_fee', 'net_pnl', 'margin', 'roi_pct'))

        # Preserve sub-minute elapsed time from the exchange's millisecond receipts.
        held_seconds = duration_seconds(c_ts, u_ts)
        duration_str = format_duration(held_seconds)

        # Strategy tag
        # Strategy source is resolved after complete lifecycle fill reconciliation.
        
        attribution=close_reason(h,attribution_inputs['orders'],algos=attribution_inputs['algos'],executions=attribution_inputs['executions'],scope=env.identity)
        matching_previous = [row for row in old_trades if matches_lifecycle(row, h, env.identity)]
        replaced_rows.update(id(row) for row in matching_previous)
        previous = max((row for row in matching_previous if row.get('status') == 'closed'),
                       key=lambda row: int((row.get('exit_snapshot') or {}).get('uTime') or 0), default={})
        if int((previous.get('exit_snapshot') or {}).get('uTime') or 0) > int(h.get('uTime') or 0):
            # A delayed/overlapping API page must not roll settlement backward.
            trades_lifecycle.append(previous)
            continue
        exit_snapshot = {k: h.get(k) for k in ("instId", "direction", "posId", "openAvgPx", "closeAvgPx", "closeTotalPos", "lever", "cTime", "uTime", "pnl", "fee", "fundingFee", "realizedPnl") if h.get(k) is not None}
        tier={'unknown':0,'partial':1,'corroborated':2,'mixed':3,'verified':3}
        if previous.get('exit_snapshot') == exit_snapshot and tier.get(previous.get('attribution_status'),0)>tier.get(attribution['attribution_status'],0):
            for field in ('exit_reason','exit_source','exit_evidence','attribution_status','close_order_ids','close_order_sources','attribution_note'):
                if field in previous:attribution[field]=previous[field]

        allocation = reconcile_fill_fees(h, fill_archive, peers=pos_history, active_positions=pos_data)
        origin = strategy_origin(h,fill_archive,origins,allocation.get("fee_reconciliation"))
        strat_tag = origin["strategy"]
        opening_order_ids = list((allocation.get("fee_reconciliation") or {}).get("opening_order_ids") or [])
        archived_fills = [fill for rows in fill_archive.by_instrument.values() for fill in rows]
        opening_trade_ids = [str(f.get("tradeId")) for f in archived_fills if isinstance(f, dict)
                             and str(f.get("ordId") or "") in set(opening_order_ids) and f.get("tradeId")]
        linked_decisions = [origin.get("decision_id")] if origin.get("decision_id") else []
        evidence_status = "complete" if origin.get("strategy_evidence") == "opening_fill_order_decision_link" and opening_order_ids and linked_decisions else "partial"
        trades_lifecycle.append({
            "id": "pos_hist_" + lifecycle_id(h, env.identity),
            "position_created_at": str(h.get("cTime") or ""),
            "instId":inst_id,"pos_id":str(h.get("posId") or ""),"closed_size":close_pos_sz,"environment_id":env.identity,"environment":env.mode,
            "inst": inst,
            "side": side,
            "lever": f"{lever:g}x" if lever is not None else "--",
            "strategy": strat_tag,
            "strategy_evidence": origin["strategy_evidence"],
            "source_status": origin.get("source_status") or ("linked" if origin.get("strategy_evidence") == "opening_fill_order_decision_link" else "external_or_unlinked"),
            "strategy_decision_id": origin.get("decision_id"),
            "decision_id": origin.get("decision_id"),
            "candidate_id": origin.get("candidate_id"),
            "strategy_version": origin.get("strategy_version") or origin.get("candidate_version"),
            "setup": origin.get("setup") or origin.get("strategy_type"),
            "opening_order_ids": opening_order_ids,
            "opening_trade_ids": sorted(set(opening_trade_ids)),
            "opening_features": origin.get("opening_features") or {},
            "news_snapshot": origin.get("news_snapshot") or {},
            "evidence_status": evidence_status,
            "decision_horizon": origin.get("decision_horizon"),
            "execution_horizon": origin.get("execution_horizon"),
            "strategy_engine": origin.get("strategy_engine"),
            "execution_cost_model": origin.get("execution_cost_model"),
            "selection_research": origin.get("selection_research"),
            "strategy_type": origin.get("strategy_type") or origin.get("setup") or "unknown",
            "horizon": origin.get("horizon", "unknown"),
            "margin": margin_usdt,
            "sz": 0,
            "open_time": open_time,
            "open_px": round(open_px, 4),
            "close_time": close_time,
            "close_px": round(close_px, 4),
            "exit_snapshot": exit_snapshot,
            "gross_pnl": gross_pnl,
            "open_fee": None,
            "close_fee": None,
            "funding_fee": funding_fee,
            "accounting_basis": amounts["accounting_basis"],
            "fee_allocation": "unknown_until_fill_reconciliation",
            "fee": fee,
            "pnl": net_pnl,
            "net_pnl": net_pnl,
            "roi": roi_pct,
            "roi_pct": roi_pct,
            "duration": duration_str,
            "duration_seconds": held_seconds,
            "status": "closed",
            "close_notification_status": previous.get("close_notification_status", "legacy") if previous else "pending",
            **attribution,
            **allocation
        })
        aliases = {row['id'] for row in matching_previous if isinstance(row.get('id'), str)}
        aliases.update(alias for row in matching_previous for alias in row.get('superseded_ledger_ids', []) if isinstance(alias, str))
        aliases.discard(trades_lifecycle[-1]['id'])
        if aliases:
            # Proven lifecycle aliases only; SQLite removes these projections in
            # the same transaction as insertion. Raw receipts remain archived.
            trades_lifecycle[-1]['superseded_ledger_ids'] = sorted(aliases)
        retain_verified_evidence(trades_lifecycle[-1], previous, fill_archive.status)
        annotate(trades_lifecycle[-1],h,observations,attribution_inputs['executions'])

    # Preserve old finalized history; replace stale holding rows only with verified data.
    fresh_ids={row['id'] for row in trades_lifecycle}
    for row in old_trades:
        if row.get('id') in fresh_ids or id(row) in replaced_rows:continue
        if row.get('status')=='closed' and str(row.get('close_time',''))>=reset_time:
            trades_lifecycle.append(row)
        elif row.get('status') in {'holding','closed_pending'}:
            # A new position on the same side does not settle the previous one.
            trades_lifecycle.append(row)
    trades_lifecycle=ledger_monitor.project_rows(trades_lifecycle,env.identity,positions=pos_data)
    trades_lifecycle.sort(key=lambda row:(row.get('status')=='holding',row.get('confirmed_close_at') or row.get('close_time') or row.get('open_time') or ''),reverse=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(trades_lifecycle, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, LEDGER_JSON_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    try:
        from scripts.horizon_stats import write as write_horizon_stats
        write_horizon_stats(trades_lifecycle, scope=env.identity)
    except Exception:
        pass

    # The monitoring worker never emits trade notifications.
    if not notify:
        return trades_lifecycle
    # Notify newly closed trades via QQ
    try:
        from qq_notifier import notify_trade_close
        for t in trades_lifecycle:
            if t.get("status") == "closed" and (t.get("close_notification_status") == "pending" or t["id"] not in existing_closed_ids):
                if t.get("net_pnl") is None:
                    continue  # Keep notification pending until settlement is observable.
                notify_trade_close(
                    inst=t.get("inst", "CRYPTO"),
                    pnl=float(t.get("pnl", 0.0) or 0.0),
                    stage=t.get("exit_reason", "平仓结清"),
                    exit_px=float(t.get("close_px", 0.0) or 0.0),
                    roi_pct=t.get("roi_pct"),
                    duration_str=str(t.get("duration", "")),
                )
                t["close_notification_status"] = "sent"
                # The read-only worker may discover the close first; persist notification acknowledgement.
                ledger_monitor.atomic("trading_ledger.json", trades_lifecycle)
    except Exception as e:
        print(f"[Ledger Sync Notify Warning] {e}")

    print(f"✅ Authentic OKX Positions-History Ledger Generated: {len(trades_lifecycle)} total trades.")
    return trades_lifecycle

if __name__ == "__main__":
    build_lifecycle_ledger()
