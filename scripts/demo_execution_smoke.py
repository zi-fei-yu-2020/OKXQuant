"""Explicit tiny DEMO execution acceptance; not an AI signal or strategy backtest.

Uses real exchange reads, the production cost/risk sizing policy, a native REST
protected limit order, OCO reads, native amendment, and confirmed-close/ledger path.
This does NOT validate the CLI or the production AI/entry-gateway strategy path.
Never writes model-decision evidence or relaxes production entry gates. This is
an operator-authorized execution diagnostic, not a production strategy cycle.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import json
import math
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


class SafetyError(RuntimeError):
    """Locally authored, non-secret safety diagnostic suitable for the report."""


def error_record(exc):
    return {'error_type': type(exc).__name__,
            'detail': str(exc) if isinstance(exc, SafetyError) else 'External error; raw detail withheld'}


def validate_options(environment, confirmation, max_notional):
    if environment.mode != "demo" or environment.simulated is not True or environment.configured is not True:
        raise ValueError("Smoke orders require configured static DEMO credentials")
    if not environment.identity or environment.base_url != "https://www.okx.com":
        raise ValueError("Untrusted account identity or REST host")
    if confirmation != "DEMO EXECUTION TEST":
        raise ValueError("Explicit DEMO EXECUTION TEST confirmation required")
    if isinstance(max_notional, bool) or not math.isfinite(float(max_notional)) or not 1 <= float(max_notional) <= 100:
        raise ValueError("Demo diagnostic notional must be between 1 and 100 USDT")


def grid(value, tick, *, up=False):
    value, tick = Decimal(str(value)), Decimal(str(tick))
    return float((value/tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*tick)


def quantity(value, *, positive=False, signed=False):
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise ValueError('Missing/invalid diagnostic quantity')
    number = Decimal(str(value))
    if not number.is_finite() or (not signed and number < 0) or (positive and number <= 0):
        raise ValueError('Non-finite or out-of-range diagnostic quantity')
    return number


def own_order(rows, inst_id, client_id, size, order_id=None):
    """A filtered API query alone is not proof that its response belongs to us."""
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise SafetyError('Unknown order; cleanup requires operator review')
    row = rows[0]
    expected = {'instId': inst_id, 'clOrdId': client_id, 'side': 'buy',
                'posSide': 'long', 'tdMode': 'cross', 'ordType': 'limit'}
    if any(row.get(key) != value for key, value in expected.items()):
        raise SafetyError('Order ownership mismatch; no automatic mutation')
    if not isinstance(row.get('ordId'), str) or not row['ordId'].strip() or (order_id and row['ordId'] != order_id):
        raise SafetyError('Order ID mismatch')
    if quantity(row.get('sz'), positive=True) != quantity(size, positive=True):
        raise SafetyError('Order size differs from submitted size')
    fill = quantity(row.get('accFillSz'))
    if fill > quantity(size) or row.get('state') not in ('live', 'partially_filled', 'filled', 'canceled'):
        raise SafetyError('Invalid order fill/state')
    if row['state'] == 'filled' and fill != quantity(size):
        raise SafetyError('Filled order size is inconsistent')
    return row


def own_position(positions, fill, pos_id=None):
    if len(positions) != 1:
        raise SafetyError('Ambiguous target position; automatic cleanup refused')
    position = positions[0]
    if (position.get('posSide') != 'long' or position.get('mgnMode') != 'cross'
            or not position.get('posId')
            or (pos_id is not None and position['posId'] != pos_id)
            or quantity(position.get('pos'), positive=True) != quantity(fill, positive=True)):
        raise SafetyError('Position differs from own fill; automatic cleanup refused')
    return position


def own_algorithms(rows, order, inst_id):
    if not isinstance(rows, list) or any(not isinstance(a, dict) for a in rows):
        raise SafetyError('Unknown algorithms snapshot')
    target_rows = [a for a in rows if a.get('instId') == inst_id]
    if not target_rows:
        return []
    # Generated algoId is NOT attachAlgoId, and pending algo ordId may be empty.
    # Require the marker we submitted AND its echo on the reconciled parent order.
    parent_id = order.get('clOrdId')
    expected = parent_id + 'p' if isinstance(parent_id, str) and parent_id else None
    attachments = order.get('attachAlgoOrds')
    if (expected is None or not isinstance(attachments, list) or len(attachments) != 1
            or not isinstance(attachments[0], dict)
            or attachments[0].get('attachAlgoClOrdId') != expected):
        raise SafetyError('Submitted attachment client ID not confirmed by parent order')
    if any(not isinstance(a.get('algoId'), str) or not a['algoId'].strip()
           or a.get('algoClOrdId') != expected for a in target_rows):
        raise SafetyError('Unowned target algorithm; automatic mutation refused')
    return target_rows


def run(inst_id, *, confirmation, max_notional=20):
    report = {"kind":"manual_demo_execution_acceptance", "strategy_signal":False,
              "execution_transport":"native_rest", "cli_validation":False,
              "production_entry_gateway":False, "environment":"demo", "instrument":inst_id, "started_at":time.time(),
              "max_notional_usdt":max_notional, "steps":[], "status":"running"}
    client_id = 'oqsmoke' + uuid.uuid4().hex[:16]
    order_id = None
    sent = False
    env = None
    baseline = None
    plan = None
    position_id = None
    unfreeze_environment = None
    locks = ExitStack()
    output = ROOT/'logs'/f"{client_id}.json"

    def checkpoint(step, **details):
        report['steps'].append({"step":step,"at":time.time(),**details})
        try:
            output.parent.mkdir(parents=True,exist_ok=True)
            with output.open('w', encoding='utf-8') as handle:
                output.chmod(0o600)
                handle.write(json.dumps(report,ensure_ascii=False,indent=2))
        except OSError as exc:
            # Logging failure must never skip cleanup/unfreezing after a possible write.
            report.setdefault('report_errors', []).append(type(exc).__name__)
            if report['status']=='passed':report['status']='failed'

    def guard():
        validate_options(env, confirmation, max_notional)
        current = selected_environment()
        validate_options(current, confirmation, max_notional)
        if current.identity != env.identity:
            raise SafetyError('Account changed; no diagnostic write sent')

    def read(path, params):
        return _request('GET',path,params,env,timeout=12)

    def write(path, params):
        guard()
        checkpoint('write_started', path=path)
        result = _request('POST',path,params,env,timeout=12)
        checkpoint('write_returned', path=path)
        return result

    def held():
        rows = read('/api/v5/account/positions',{'instType':'SWAP'})
        if not isinstance(rows, list):
            raise SafetyError('Unknown positions snapshot')
        result = []
        for position in rows:
            if not isinstance(position, dict) or not position.get('instId'):
                raise SafetyError('Malformed position snapshot')
            raw = position.get('pos')
            # Signed net positions are valid baseline exposure, but never cleanup targets.
            size = abs(quantity(raw, signed=True))
            if size > 0:
                result.append(position)
        return result

    def target():
        return [p for p in held() if p.get('instId')==inst_id]

    try:
        # All config/I/O imports remain inside run; offline tests replace every boundary.
        from okxquant_backend.config import refresh_settings
        from scripts.okx_runtime import freeze_environment, unfreeze_environment, selected_environment
        from okxquant_backend.okx_trade_service import _request, _create_intent, fast_close_confirmed
        from scripts import risk_policy, trade_lock, public_market
        from scripts.algo_reader import read_algo_orders
        refresh_settings()
        validate_options(selected_environment(), confirmation, max_notional)
        if inst_id not in {"BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"}:
            raise ValueError("Unsupported diagnostic instrument")
        env = freeze_environment()
        guard()  # Freeze may have observed a different environment: recheck before any I/O.
        locks.enter_context(trade_lock.writer())
        baseline = held()
        if any(p.get('instId')==inst_id for p in baseline):
            raise SafetyError('Existing target position: smoke test refuses to touch it')
        if read('/api/v5/trade/orders-pending',{'instType':'SWAP','instId':inst_id}):
            raise SafetyError('Existing target orders: smoke test refuses to touch them')
        algos = read_algo_orders(env,priority='risk',force=True)
        if any(a.get('instId')==inst_id for a in algos):
            raise SafetyError('Existing target algorithms: choose another instrument')
        checkpoint('baseline_verified',preserved_positions=[{'instId':p['instId'],'side':p['posSide'],'size':p['pos']} for p in baseline])
        metadata = public_market.get_json('https://www.okx.com/api/v5/public/instruments?instType=SWAP',simulated=True)['data']
        by_id = {m['instId']:m for m in metadata}
        meta=by_id[inst_id];ct,lot,minimum,tick=risk_policy.linear_metadata(meta)
        quote=public_market.get_json('https://www.okx.com/api/v5/market/ticker?instId='+inst_id,simulated=True)['data'][0]
        if not 0<=time.time()*1000-float(quote['ts'])<=15000:raise SafetyError('Stale execution quote')
        entry=grid(float(quote['askPx'])*1.0005,tick,up=True)
        stop=grid(entry*.99,tick);tp=grid(entry*1.04,tick,up=True)
        requested=risk_policy.floor_step(float(max_notional)/(ct*entry),lot)
        if requested<minimum:raise SafetyError('Notional limit cannot fund minimum lot; not rounded up')
        balance=read('/api/v5/account/balance',{})[0]
        available=next(d for d in balance['details'] if d['ccy']=='USDT')
        leverage=next(r for r in read('/api/v5/account/leverage-info',{'instId':inst_id,'mgnMode':'cross'}) if r.get('posSide') in ('long','net',''))['lever']
        policy=risk_policy.load_policy()
        exposure=risk_policy.exposure(baseline,read('/api/v5/trade/orders-pending',{'instType':'SWAP'}),algos,by_id,policy)
        plan=risk_policy.order_plan(metadata=meta,side='long',entry=entry,stop=stop,take_profit=tp,
            requested_size=requested,budget_usdt=2,equity=float(balance['totalEq']),
            available=float(available['availEq'] if available.get('availEq') is not None else available['availBal']),leverage=leverage,policy=policy,portfolio=exposure)
        if quantity(plan['notional_usdt'], positive=True)>quantity(max_notional) or quantity(plan['size'], positive=True)*quantity(ct, positive=True)*quantity(entry, positive=True)>quantity(max_notional):raise SafetyError('Notional ceiling exceeded')
        checkpoint('sizing_passed',plan=plan)
        # Explicit manual REST diagnostic, not an AI signal or gateway entry.
        attachment_id = client_id + 'p'
        body = {'instId': inst_id, 'tdMode': 'cross', 'side': 'buy', 'posSide': 'long',
                'clOrdId': client_id, 'ordType': 'limit', 'px': str(entry), 'sz': str(plan['size']),
                'attachAlgoOrds': [{'attachAlgoClOrdId': attachment_id,
                                    'tpTriggerPx': str(tp), 'tpOrdPx': '-1',
                                    'slTriggerPx': str(stop), 'slOrdPx': '-1'}]}
        guard()
        if target() or read('/api/v5/trade/orders-pending',{'instType':'SWAP','instId':inst_id}):
            raise SafetyError('Target no longer empty before submit')
        if any(a.get('instId')==inst_id for a in read_algo_orders(env,priority='risk',force=True)):
            raise SafetyError('Target algorithms appeared before submit')
        if not 0<=time.time()*1000-float(quote['ts'])<=15000:
            raise SafetyError('Execution quote expired during preflight')
        checkpoint('submission_started',client_id=client_id,attachment_client_id=attachment_id,
                   method='POST',path='/api/v5/trade/order')
        if report.get('report_errors'):
            raise SafetyError('Report cannot be persisted; no diagnostic submission')
        guard()
        sent=True
        try:
            # _request supplies the production writer/algo mutation barriers. ONE attempt.
            _request('POST','/api/v5/trade/order',body,env,timeout=20)
            checkpoint('submission_returned',transport='native_rest')
        except Exception as exc:
            checkpoint('submission_unknown',**error_record(exc))
        if report.get('report_errors'):
            raise SafetyError('Report persistence failed; stop diagnostic and attempt safe cleanup')
        # Never persist raw transport errors; reconcile the same ID using GET only,
        # regardless of POST acknowledgment or timeout. NEVER resend the entry.
        deadline=time.monotonic()+25
        order=None
        while time.monotonic()<deadline:
            try:
                rows=read('/api/v5/trade/order',{'instId':inst_id,'clOrdId':client_id})
                if rows:
                    order=own_order(rows,inst_id,client_id,plan['size'],order_id)
                    order_id=order['ordId']
                    if order.get('state') in ('filled','canceled'):break
            except Exception as exc:
                checkpoint('reconciliation_read_failed',**error_record(exc))
                order=None
            time.sleep(1)
        if not order:raise SafetyError('Order outcome unknown; no resend; inspect client ID')
        checkpoint('order_reconciled',order_id=order_id,state=order.get('state'),filled_size=order.get('accFillSz'))
        if order.get('state')!='filled':raise SafetyError('Diagnostic limit not fully filled; cleanup required')
        positions=target()
        position_id=own_position(positions, plan['size'])['posId']
        protected=False
        for _ in range(8):
            algos=read_algo_orders(env,priority='risk',force=True)
            matching=own_algorithms(algos,order,inst_id)
            from scripts.protection_policy import oco_coverage
            coverage=oco_coverage(matching,'long',float(positions[0]['markPx']),float(positions[0]['avgPx']))
            if not coverage.unknown and quantity(coverage.size)==quantity(plan['size']):
                protected=True;break
            time.sleep(1)
        if not protected:raise SafetyError('Attached OCO not confirmed; closing diagnostic position')
        checkpoint('oco_verified',coverage_size=coverage.size,segments=len(coverage.orders))
        # Tighten just one tick, not to breakeven; this is a manual execution test.
        for algo in coverage.orders:
            current_position=own_position(target(), plan['size'], position_id)
            current_mark=quantity(current_position.get('markPx'), positive=True)
            own_algorithms(read_algo_orders(env,priority='risk',force=True),order,inst_id)
            new_stop=grid(float(algo['slTriggerPx'])+tick,tick,up=True)
            if quantity(new_stop, positive=True)>=current_mark:raise SafetyError('Proposed diagnostic stop crosses market')
            write('/api/v5/trade/amend-algos',{'instId':inst_id,'algoId':algo['algoId'],'newSlTriggerPx':str(new_stop),'newSlOrdPx':'-1'})
            updated=read_algo_orders(env,priority='risk',force=True)
            actual=next((a for a in updated if a.get('algoId')==algo['algoId']),None)
            if actual is None or float(actual.get('slTriggerPx') or 0)!=new_stop:raise SafetyError('Amendment not confirmed; no blind retry')
        checkpoint('stop_amendment_verified')
        report['status']='passed'
    except (Exception, KeyboardInterrupt, SystemExit) as exc:
        report['status']='failed';checkpoint('failure',**error_record(exc))
    finally:
        try:
            # Only the initially empty target, a positively identified own order,
            # and exactly its finite fill size may reach the native close path.
            if sent:
                try:
                    guard()
                    rows=read('/api/v5/trade/order',{'instId':inst_id,'clOrdId':client_id})
                    own=own_order(rows,inst_id,client_id,plan['size'],order_id)
                    if own['state'] in ('live','partially_filled'):
                        write('/api/v5/trade/cancel-order',{'instId':inst_id,'ordId':own['ordId']})
                        own=own_order(read('/api/v5/trade/order',{'instId':inst_id,'clOrdId':client_id}),
                                      inst_id,client_id,plan['size'],own['ordId'])
                        if own['state'] not in ('filled','canceled'):
                            raise SafetyError('Cancel not confirmed; no retry')
                        checkpoint('pending_cancellation_verified',state=own['state'])
                    positions=target()
                    if positions:
                        position=own_position(positions,own['accFillSz'],position_id)
                        # Native confirmed-close cancels entry orders and uses autoCxl.
                        # Never let it consume a new external order/algo on this symbol.
                        if read('/api/v5/trade/orders-pending',{'instType':'SWAP','instId':inst_id}):
                            raise SafetyError('Target pending orders remain; native cleanup refused')
                        own_algorithms(read_algo_orders(env,priority='risk',force=True),own,inst_id)
                        guard()
                        token,phrase=_create_intent(env,position)
                        checkpoint('native_close_started')
                        guard()
                        result=fast_close_confirmed(token,phrase)  # Exactly once, even on timeout.
                        if result.get('status')!='confirmed_closed':
                            raise SafetyError('Native close not confirmed; no retry')
                        checkpoint('production_confirmed_close',status=result['status'],
                                   ledger_refresh=result.get('ledger_refresh'))
                    elif report['status']=='passed':
                        report['status']='failed'
                        checkpoint('already_flat_native_close_not_exercised')
                    if target():raise SafetyError('Diagnostic position remains')
                    if read('/api/v5/trade/orders-pending',{'instType':'SWAP','instId':inst_id}) or any(a.get('instId')==inst_id for a in read_algo_orders(env,priority='risk',force=True)):
                        raise SafetyError('Residual target orders/algorithms; operator review required')
                    checkpoint('target_flat_confirmed')
                except (Exception, KeyboardInterrupt, SystemExit) as exc:
                    report['status']='cleanup_required'
                    checkpoint('cleanup_failure',**error_record(exc))
            if baseline is not None:
                try:
                    def preserved(rows):
                        return sorted((p['instId'],str(p.get('posSide')),str(p.get('posId')),
                                       str(p.get('mgnMode')),Decimal(str(p['pos'])))
                                      for p in rows if p['instId']!=inst_id)
                    if preserved(held()) != preserved(baseline):
                        raise SafetyError('Non-target exposure changed; never auto-repair it')
                    checkpoint('existing_positions_preserved')
                except Exception as exc:
                    if report['status']!='cleanup_required':report['status']='failed'
                    checkpoint('baseline_verification_failed',**error_record(exc))
            if report.get('report_errors') and report['status']=='passed':
                report['status']='failed'
            report['finished_at']=time.time()
            checkpoint('finished',status=report['status'])
        finally:
            try:
                locks.close()
            finally:
                if env is not None and unfreeze_environment is not None:
                    unfreeze_environment()
    return report,output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inst-id',default='BTC-USDT-SWAP',choices=['BTC-USDT-SWAP','ETH-USDT-SWAP','SOL-USDT-SWAP'])
    p.add_argument('--max-notional',type=float,default=20)
    p.add_argument('--confirm',required=True)
    args=p.parse_args()
    sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
    report,path=run(args.inst_id,confirmation=args.confirm,max_notional=args.max_notional)
    print(json.dumps({'status':report['status'],'environment':report['environment'],'execution_transport':report['execution_transport'],'report':str(path),'report_errors':report.get('report_errors',[]),'steps':[s['step'] for s in report['steps']]}))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
