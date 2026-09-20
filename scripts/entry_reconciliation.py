"""Read-only reconciliation of uncertain entries; never resends an order."""
import math
import json
import time
from scripts import risk_policy as risk
from okxquant_backend.okx_trade_service import OKXAPIError

MAX_PAGES = 5
GRACE_SECONDS = 120
HISTORY_SECONDS = 7 * 86400


def recoverable_intents(scope):
    """Include previously observed live/partial orders until exchange terminality.

    Pending is positive acceptance evidence, not absence. This projection also
    binds the exact durable receipt IDs needed after a client-ID lookup miss.
    """
    from scripts import strategy_evidence as evidence
    with evidence.connection() as db:
        rows = db.execute("SELECT id,payload,at,state FROM intents WHERE scope=? "
                          "AND state IN ('unknown','acknowledged','pending') ORDER BY at,id",
                          (scope,)).fetchall()
        receipts = {key: set() for key, _, _, _ in rows}
        if rows:
            for (raw,) in db.execute("SELECT payload FROM events WHERE scope=? AND kind='order_status' AND at>=?",
                                     (scope, min(row[2] for row in rows))):
                event = json.loads(raw)
                client = event.get('client_id')
                result = event.get('result') or {}
                if client in receipts and isinstance(result, dict) and event.get('state') in {'acknowledged', 'pending'}:
                    order_id = result.get('order_id') or result.get('ordId')
                    if isinstance(order_id, (str, int)) and not isinstance(order_id, bool) and str(order_id):
                        receipts[client].add(str(order_id))
    return [(key, {**json.loads(raw), 'intent_created_at': created, 'intent_state': state,
                   'intent_order_ids': sorted(receipts[key])})
            for key, raw, created, state in rows]


def locate(env, client_id, plan, request, *, pending_orders=None):
    inst = plan['instId']
    started = time.monotonic()
    created = float(plan.get('intent_created_at') or 0)
    age = time.time() - created
    known_other_orders = set()
    reads = []
    accepted_ids = set(plan.get('intent_order_ids') or [])
    if len(accepted_ids) > 1:
        raise risk.RiskRejected('Conflicting prior entry receipts; reservation retained')

    def get(path, params):
        remaining = 25 - (time.monotonic() - started)
        if remaining <= 0:
            raise risk.RiskRejected('Reconciliation read budget exhausted; reservation retained')
        rows = request('GET', path, params, env, timeout=min(8, remaining))
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise risk.RiskRejected('Invalid reconciliation response; reservation retained')
        reads.append({'endpoint': path, 'rows': len(rows)})
        return rows

    def exact(rows):
        matches = [r for r in rows if str(r.get('clOrdId') or '') == client_id]
        if any(r.get('instId') != inst or not r.get('ordId') for r in matches):
            raise risk.RiskRejected('Prior entry identity mismatch; reservation retained')
        if len(matches) > 1:
            raise risk.RiskRejected('Prior entry reconciliation returned multiple matches')
        if matches and accepted_ids and str(matches[0]['ordId']) not in accepted_ids:
            raise risk.RiskRejected('Prior entry receipt/order identity mismatch; reservation retained')
        return matches[0] if matches else None

    # A pending order already confirmed by this preflight's fresh account read
    # does not need a redundant per-order query that could stall all instruments.
    if plan.get('intent_state') == 'pending' and pending_orders is not None:
        if not isinstance(pending_orders, list) or any(not isinstance(r, dict) for r in pending_orders):
            raise risk.RiskRejected('Invalid current pending snapshot; reservation retained')
        match = exact(pending_orders)
        if match is not None and match.get('state') in {'live', 'partially_filled'}:
            return match, {'reconciliation': 'fresh_preflight_pending_snapshot', 'reads': reads}

    if len(client_id) <= 32:
        try:
            rows = get('/api/v5/trade/order', {'instId': inst, 'clOrdId': client_id})
        except OKXAPIError as exc:
            if exc.code != '51603':
                raise
            rows = []
            reads.append({'endpoint': '/api/v5/trade/order', 'code': exc.code})
        if rows:
            match = exact(rows)
            if match is None:
                raise risk.RiskRejected('Prior entry identity mismatch; reservation retained')
            return match, {'reads': reads}

    # Reuse positive durable receipt identity after a definite client-ID miss.
    # A timeout/auth error above never enters this alternative lookup path.
    if accepted_ids:
        order_id = next(iter(accepted_ids))
        try:
            rows = get('/api/v5/trade/order', {'instId': inst, 'ordId': order_id})
        except OKXAPIError as exc:
            if exc.code != '51603':
                raise
            rows = []
            reads.append({'endpoint': '/api/v5/trade/order', 'code': exc.code, 'lookup': 'ordId'})
        if rows:
            match = exact(rows)
            if match is None:
                raise risk.RiskRejected('Prior entry receipt/order identity mismatch; reservation retained')
            return match, {'reads': reads, 'reconciliation': 'durable_receipt_order_id'}

    # Only a definite missing order (or successful empty response) reaches here.
    # Never treat timeout, 503, authentication or malformed responses as absence.
    if not math.isfinite(created) or created <= 0 or not GRACE_SECONDS <= age < HISTORY_SECONDS - GRACE_SECONDS:
        raise risk.RiskRejected('Prior entry outside safe reconciliation window; reservation retained')

    for endpoint in ('orders-pending', 'orders-history'):
        after = None
        cursors = set()
        for page in range(MAX_PAGES):
            params = {'instType': 'SWAP', 'instId': inst, 'limit': '100'}
            if after:
                params['after'] = after
            # clOrdId is NOT a server-side filter for these list endpoints.
            rows = get('/api/v5/trade/' + endpoint, params)
            if any(r.get('instId') != inst for r in rows):
                raise risk.RiskRejected('Reconciliation instrument mismatch; reservation retained')
            match = exact(rows)
            if match:
                return match, {'reads': reads}
            known_other_orders.update(str(r['ordId']) for r in rows if r.get('ordId') and r.get('clOrdId'))
            if len(rows) < 100:
                break
            after = str(rows[-1].get('ordId') or '')
            if not after or after in cursors:
                raise risk.RiskRejected('Reconciliation pagination incomplete; reservation retained')
            cursors.add(after)
        else:
            raise risk.RiskRejected('Reconciliation page budget exhausted; reservation retained')

    # Canceled orders may have shorter retention than fills. Require a complete
    # execution scan as well; absent clOrdId is NOT proof a fill belongs elsewhere.
    after = None
    cursors = set()
    for page in range(MAX_PAGES):
        params = {'instType': 'SWAP', 'instId': inst, 'limit': '100',
                  'begin': str(int((created - 2) * 1000))}
        if after:
            params['after'] = after
        rows = get('/api/v5/trade/fills-history', params)
        for row in rows:
            if row.get('instId') != inst:
                raise risk.RiskRejected('Reconciliation fill identity mismatch; reservation retained')
            cid = str(row.get('clOrdId') or '')
            if cid == client_id:
                order_id = str(row.get('ordId') or '')
                if not order_id:
                    raise risk.RiskRejected('Prior entry fill has no order identity; reservation retained')
                # A client-ID lookup can be absent while executions still expose
                # the exact exchange ID. Read that order; never replay the entry.
                detail = get('/api/v5/trade/order', {'instId': inst, 'ordId': order_id})
                match = exact(detail)
                if match is None or str(match.get('ordId')) != order_id:
                    raise risk.RiskRejected('Prior entry fill/order identity mismatch; reservation retained')
                return match, {'reads': reads, 'reconciliation': 'exact_fill_order_id'}
            if not cid and str(row.get('ordId') or '') not in known_other_orders:
                raise risk.RiskRejected('Prior entry fill requires order reconciliation; reservation retained')
        if len(rows) < 100:
            break
        after = str(rows[-1].get('billId') or '')
        if not after or after in cursors:
            raise risk.RiskRejected('Reconciliation fill pagination incomplete; reservation retained')
        cursors.add(after)
    else:
        raise risk.RiskRejected('Reconciliation fill page budget exhausted; reservation retained')

    positions = get('/api/v5/account/positions', {'instId': inst})
    for row in positions:
        size = float(row['pos'])
        if row.get('instId') != inst or not math.isfinite(size) or size != 0:
            raise risk.RiskRejected('Prior entry position requires reconciliation; reservation retained')
    # Acknowledged order IDs are positive evidence of acceptance, even if old
    # exchange history has disappeared. Do not erase that evidence via absence.
    if plan.get('intent_state') in {'acknowledged', 'pending'}:
        raise risk.RiskRejected('Acknowledged entry not located; reservation retained')
    return None, {'reconciliation': 'verified_no_open_order_or_execution',
                  'reads': reads, 'reconciled_at': time.time(),
                  'note': 'No current exposure or located execution; not proof of never accepted'}
