"""One explicit reduce-only market close with durable order identity.
Cancel remaining entry quantity, read the position again, never replay a write.
"""
import time,uuid
from scripts import close_evidence
from scripts.risk_policy import number
from scripts.trade_lock import serialized


def same_lifecycle(a,b):
    return all(a.get(k) and str(a[k])==str(b.get(k) or '') for k in ('posId','cTime'))


@serialized
def close(env,inst_id,side,observed_size,position,reason,*,request=None):
    if request is None:
        from okxquant_backend.okx_trade_service import _request as request
    from okxquant_backend.account_connections import assert_current
    assert_current(env)
    attempt=uuid.uuid4().hex
    client_id='okxqc'+attempt[:27]
    started=time.time()
    current=dict(position);size=abs(number(observed_size,positive=True));ids=[]
    def journal(status,error=None):
        close_evidence.record_close(env,inst_id=inst_id,side=side,size=size,started_at=started,
            confirmed_at=time.time(),reason=reason,position=current,status=status,
            result=[{'ordId':i} for i in ids],attempt_id=attempt,transport_code=error,
            observed_size=observed_size,client_id=client_id,transport='rest_reduce_only_market')
    try:
        pending=request('GET','/api/v5/trade/orders-pending',{'instId':inst_id},env,timeout=5)
        for row in pending:
            if row.get('instId')!=inst_id or row.get('posSide') not in (side,'net'):continue
            # Persist each cancellation before dispatch, so a timeout is not retried here.
            close_evidence.evidence.best_effort(env.identity,'close_cancel_intent',
                {'attempt_id':attempt,'instId':inst_id,'ordId':row['ordId']})
            request('POST','/api/v5/trade/cancel-order',{'instId':inst_id,'ordId':row['ordId']},env,timeout=5)
        pending=request('GET','/api/v5/trade/orders-pending',{'instId':inst_id},env,timeout=5)
        if any(r.get('instId')==inst_id and r.get('posSide') in (side,'net') for r in pending):
            return False,'Pending orders are not yet terminal; fresh close required'
        rows=request('GET','/api/v5/account/positions',{'instId':inst_id},env,timeout=5)
        held=[p for p in rows if p.get('instId')==inst_id and p.get('posSide')==side and abs(number(p.get('pos') or 0))>0]
        if not held:
            journal('flat_observed');return True,'Position already flat before close; no order sent'
        if len(held)!=1 or not same_lifecycle(position,held[0]):
            return False,'Position lifecycle changed; stale close was not submitted'
        current=held[0];size=abs(number(current['pos'],positive=True))
        journal('submitted')
    except Exception as exc:
        return False,'Close preflight unavailable: '+type(exc).__name__
    payload={'instId':inst_id,'tdMode':'cross','posSide':side,'side':'sell' if side=='long' else 'buy',
             'ordType':'market','sz':format(size,'.15g'),'reduceOnly':True,'clOrdId':client_id}
    error=None
    try:
        result=request('POST','/api/v5/trade/order',payload,env,timeout=10)
        ids=[str(r['ordId']) for r in result if str(r.get('ordId') or '').isdigit() and str(r.get('sCode','0'))=='0']
    except Exception as exc:error=type(exc).__name__
    journal('accepted' if ids else 'unconfirmed',error)
    # Reads may reconcile an uncertain ACK; they never resubmit this market order.
    deadline=time.monotonic()+8
    for _ in range(4):
        try:
            if not ids:
                receipt=request('GET','/api/v5/trade/order',{'instId':inst_id,'clOrdId':client_id},env,timeout=2)
                ids=[str(r['ordId']) for r in receipt if r.get('clOrdId')==client_id and str(r.get('ordId') or '').isdigit()]
                if ids:journal('accepted',error)
            rows=request('GET','/api/v5/account/positions',{'instId':inst_id},env,timeout=2)
            held=[p for p in rows if p.get('instId')==inst_id and p.get('posSide')==side and abs(number(p.get('pos') or 0))>0]
            if not held:
                journal('confirmed' if ids else 'flat_observed',error)
                return True,'Exchange position closed; order identity recorded' if ids else 'Flat observed; close order evidence still pending'
            if any(not same_lifecycle(current,p) for p in held):
                journal('unconfirmed',error);return False,'New position lifecycle observed; no further close sent'
        except Exception:pass
        if time.monotonic()>=deadline:break
        time.sleep(.4)
    journal('unconfirmed',error)
    return False,'Close outcome unresolved; no write retry'
