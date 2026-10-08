"""Bounded Maker-first entry routing. Unknown write/read outcomes never trigger a resend."""
from __future__ import annotations
import time

WAIT_SECONDS = 2.5


def explicit_exchange_rejection(result, diagnostics):
    """A complete business rejection is safe to fallback; transport is not."""
    return (result.get('returncode') == 0 and result.get('data') is not None
            and diagnostics.get('exchange_code') not in (None, '', '0'))


def wait_once():
    time.sleep(WAIT_SECONDS)


def status_row(request, env, inst_id, order_id):
    rows = request('GET', '/api/v5/trade/order', {'instId': inst_id, 'ordId': str(order_id)}, env, timeout=8)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError('Maker order status is incomplete')
    row = rows[0]
    if str(row.get('instId') or '') != inst_id or str(row.get('ordId') or '') != str(order_id):
        raise RuntimeError('Maker order identity mismatch')
    return row


def cancel(request, env, inst_id, order_id):
    rows = request('POST', '/api/v5/trade/cancel-order', {'instId': inst_id, 'ordId': str(order_id)}, env, timeout=8)
    if not isinstance(rows, list):
        raise RuntimeError('Maker cancel response is incomplete')
    return rows


def canceled_fill_size(row):
    """A cancel acknowledgement is not proof that no fill raced the cancellation."""
    from scripts.risk_policy import number, RiskRejected
    if row.get('state') not in {'canceled','mmp_canceled'}:
        raise RiskRejected('Maker cancellation is not terminal')
    size=number(row.get('accFillSz'))
    if size<0:raise RiskRejected('Maker cumulative fill size is invalid')
    return size


def create_decision(scope, parent_client_id, decision_id, inst_id, side, proof):
    """One bounded child decision, not permission to replay the original intent.

    Parent stays immutable for accounting. The deterministic child identity plus
    intents' existing UNIQUE(scope,decision_id,inst_id) permits at most one send.
    No new inference, TTL extension, account change or risk exemption is created.
    """
    import hashlib,json
    from scripts import strategy_evidence as evidence
    from scripts.risk_policy import RiskRejected
    with evidence.connection() as db:
        parent=db.execute('SELECT scope,decision_id,inst_id,state,payload FROM intents WHERE id=?',(parent_client_id,)).fetchone()
        original=db.execute("SELECT payload FROM events WHERE id=? AND scope=? AND kind='decision'",(decision_id,scope)).fetchone()
    if not parent or parent[:3]!=(scope,decision_id,inst_id) or not original:
        raise RiskRejected('Fallback parent decision/account/instrument mismatch')
    plan=json.loads(parent[4]);record=json.loads(original[0])
    if (plan.get('side')!=side or plan.get('entry_execution_mode')!='maker_first'
            or record.get('fallback_parent')):
        raise RiskRejected('Only an original Maker attempt may have one fallback')
    if parent[3]=='canceled':
        if (proof.get('instId')!=inst_id or proof.get('clOrdId')!=parent_client_id
                or not proof.get('ordId') or canceled_fill_size(proof)!=0):
            raise RiskRejected('Fallback requires exact terminal zero-fill Maker evidence')
    elif parent[3]=='not_submitted':
        if proof.get('kind')!='explicit_business_rejection' or proof.get('exchange_code') in (None,'','0'):
            raise RiskRejected('Fallback requires explicit Maker business rejection')
    else:
        raise RiskRejected('Maker exposure is unresolved; fallback forbidden')
    child='maker-fallback-decision:'+hashlib.sha256((scope+':'+decision_id+':'+inst_id).encode()).hexdigest()
    record={**record,'fallback_parent':{'client_id':parent_client_id,'decision_id':decision_id,
            'inst_id':inst_id,'side':side,'state':parent[3],'proof':proof}}
    evidence.append(scope,'decision',record,child)
    return child


def validated_parent(record, scope, inst_id, side):
    """Recheck durable linkage; only this canceled Maker is exempt from its own slot."""
    import json
    from scripts import strategy_evidence as evidence
    from scripts.risk_policy import RiskRejected
    link=record.get('fallback_parent')
    if not isinstance(link,dict):raise RiskRejected('Bounded fallback has no durable parent')
    with evidence.connection() as db:
        parent=db.execute('SELECT scope,decision_id,inst_id,state,payload FROM intents WHERE id=?',(link.get('client_id'),)).fetchone()
    if not parent or parent[:3]!=(scope,link.get('decision_id'),inst_id) or parent[3]!=link.get('state'):
        raise RiskRejected('Fallback parent changed or is not current')
    plan=json.loads(parent[4]);proof=link.get('proof') or {}
    if plan.get('side')!=side or link.get('side')!=side or plan.get('entry_execution_mode')!='maker_first':
        raise RiskRejected('Fallback side/parent mismatch')
    if parent[3]=='canceled':
        if (proof.get('clOrdId')!=link['client_id'] or proof.get('instId')!=inst_id
                or not proof.get('ordId') or canceled_fill_size(proof)!=0):
            raise RiskRejected('Fallback zero-fill proof unavailable')
    elif parent[3]!='not_submitted' or proof.get('kind')!='explicit_business_rejection' or proof.get('exchange_code') in (None,'','0'):
        raise RiskRejected('Fallback Maker exposure unresolved')
    return link['client_id']
