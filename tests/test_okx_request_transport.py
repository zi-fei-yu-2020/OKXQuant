"""503 resilience is read-only, bounded, freshly signed and credential-safe."""
import base64,hashlib,hmac,io,json,ssl,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import MagicMock,Mock,patch
from scripts.okx_runtime import OKXEnvironment
from scripts import strategy_evidence
from okxquant_backend import okx_request_transport as tr,okx_trade_service as service
REAL_EMIT=tr.emit

class OKXRequestTransportTests(unittest.TestCase):
    def setUp(self):
        self.env=OKXEnvironment('demo','PRIVATE-KEY','PRIVATE-SECRET','PRIVATE-PASS')
        self.patch=patch.object(tr,'emit');self.emit=self.patch.start();self.addCleanup(self.patch.stop)
    def test_missing_order_is_a_structured_business_error_not_transport_failure(self):
        with patch('urllib.request.urlopen',return_value=self.response({'code':'51603','msg':'Order does not exist','data':[]})) as wire:
            with self.assertRaises(service.OKXAPIError) as caught:
                service._request('GET','/api/v5/trade/order',{'instId':'SUI-USDT-SWAP','clOrdId':'test'},self.env)
            self.assertEqual(caught.exception.code,'51603')
            self.assertIsInstance(caught.exception,RuntimeError)
            wire.assert_called_once()
    def test_other_business_error_keeps_its_code(self):
        with patch('urllib.request.urlopen',return_value=self.response({'code':'50113','msg':'signature','data':[]})):
            with self.assertRaises(service.OKXAPIError) as caught:
                service._request('GET','/api/v5/trade/order',{},self.env)
            self.assertEqual(caught.exception.code,'50113')
    def response(self,payload=None):
        return io.BytesIO(json.dumps(payload if payload is not None else {'code':'0','data':[]}).encode())
    def error(self,status=503,headers=None):
        return urllib.error.HTTPError('https://www.okx.com/api/v5/account/positions',status,'PRIVATE-ERROR',headers or {},io.BytesIO(b'PRIVATE-BODY'))
    def get(self,timeout=10):
        return service._request('GET','/api/v5/account/positions',{'instType':'SWAP'},self.env,timeout=timeout)
    def test_503_then_success_is_retried_with_fresh_signature_in_same_account(self):
        stamps=['2026-09-18T00:00:00.000Z','2026-09-18T00:00:01.000Z']
        with patch.object(service,'_timestamp',side_effect=stamps),patch('urllib.request.urlopen',side_effect=[self.error(),self.response()]) as wire,patch.object(tr.time,'sleep') as sleep:
            self.assertEqual(self.get(),[])
        self.assertEqual(wire.call_count,2);sleep.assert_called_once_with(.3)
        for call,stamp in zip(wire.call_args_list,stamps):
            r=call.args[0];headers={k.lower():v for k,v in r.header_items()}
            self.assertEqual(r.full_url,'https://www.okx.com/api/v5/account/positions?instType=SWAP')
            self.assertEqual(r.get_method(),'GET');self.assertIsNone(r.data)
            self.assertEqual(headers['x-simulated-trading'],'1');self.assertEqual(headers['ok-access-key'],'PRIVATE-KEY')
            expected=base64.b64encode(hmac.new(b'PRIVATE-SECRET',(stamp+'GET/api/v5/account/positions?instType=SWAP').encode(),hashlib.sha256).digest()).decode()
            self.assertEqual(headers['ok-access-sign'],expected)
        self.assertEqual(self.emit.call_args.args[1],'exchange_read_recovered')
        self.assertEqual(self.emit.call_args.args[2]['attempts'],2)
    def test_exhaustion_is_unknown_not_an_empty_positions_snapshot(self):
        with patch('urllib.request.urlopen',side_effect=[self.error() for _ in range(3)]) as wire,patch.object(tr.time,'sleep'):
            with self.assertRaises(tr.OKXRequestError) as caught:self.get()
        self.assertEqual(wire.call_count,3);self.assertEqual(caught.exception.attempts,3)
        self.assertEqual(caught.exception.status_code,503)
        self.assertIn('503',str(caught.exception));self.assertIn('3 次',str(caught.exception))
        self.assertEqual(self.emit.call_args.args[1],'exchange_read_failure')
    def test_auth_permission_and_parameter_errors_are_not_retried(self):
        for code in (400,401,403,404):
            with self.subTest(code=code),patch('urllib.request.urlopen',side_effect=self.error(code)) as wire,patch.object(tr.time,'sleep') as sleep:
                with self.assertRaises(tr.OKXRequestError):self.get()
                wire.assert_called_once();sleep.assert_not_called()
    def test_order_close_amend_cancel_and_leverage_writes_are_never_retried(self):
        for path in ('/api/v5/trade/order','/api/v5/trade/close-position','/api/v5/trade/amend-order','/api/v5/trade/cancel-order','/api/v5/account/set-leverage'):
            with self.subTest(path=path),patch('urllib.request.urlopen',side_effect=self.error()) as wire,patch.object(tr.time,'sleep') as sleep:
                with self.assertRaises(tr.OKXRequestError) as caught:
                    service._request('POST',path,{'instId':'BTC-USDT-SWAP'},self.env)
                wire.assert_called_once();sleep.assert_not_called();self.assertEqual(caught.exception.attempts,1)
    def test_retry_after_respected_and_not_shortened_to_fit_budget(self):
        with patch('urllib.request.urlopen',side_effect=[self.error(429,{'Retry-After':'2'}),self.response()]),patch.object(tr.time,'sleep') as sleep:
            self.get();sleep.assert_called_once_with(2)
        with patch('urllib.request.urlopen',side_effect=self.error(503,{'Retry-After':'30'})) as wire,patch.object(tr.time,'sleep') as sleep:
            with self.assertRaises(tr.OKXRequestError):self.get(timeout=5)
            wire.assert_called_once();sleep.assert_not_called()
    def test_network_timeout_can_retry_but_certificate_failure_cannot(self):
        with patch('urllib.request.urlopen',side_effect=[urllib.error.URLError(TimeoutError('PRIVATE-CAUSE')),self.response()]) as wire,patch.object(tr.time,'sleep'):
            self.assertEqual(self.get(),[]);self.assertEqual(wire.call_count,2)
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError(ssl.SSLCertVerificationError('PRIVATE-CAUSE'))) as wire:
            with self.assertRaises(tr.OKXRequestError) as caught:self.get()
        wire.assert_called_once();self.assertEqual(caught.exception.category,'certificate_error')
    def test_total_budget_and_per_attempt_timeout_leave_room_for_retry(self):
        clock=[0.];limits=[]
        def send(req,timeout):limits.append(timeout);clock[0]+=timeout;raise TimeoutError('PRIVATE-CAUSE')
        with patch('urllib.request.urlopen',side_effect=send),patch.object(tr.time,'monotonic',side_effect=lambda:clock[0]),patch.object(tr.time,'sleep',side_effect=lambda d:clock.__setitem__(0,clock[0]+d)):
            with self.assertRaises(tr.OKXRequestError) as caught:self.get(timeout=10)
        self.assertEqual(limits,[5.,4.7]);self.assertEqual(caught.exception.attempts,2);self.assertAlmostEqual(clock[0],10)
    def test_late_response_is_not_accepted(self):
        clock=[0.]
        def send(*args,**kwargs):clock[0]=11.;return self.response()
        with patch('urllib.request.urlopen',side_effect=send),patch.object(tr.time,'monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(tr.OKXRequestError) as caught:self.get(timeout=10)
        self.assertEqual(caught.exception.category,'deadline_exceeded')
    def test_malformed_success_payload_never_becomes_empty_account(self):
        for payload in ({},{'code':'0'},{'code':'0','data':None},{'code':'0','data':'oops'},{'code':'0','data':[None]}):
            with self.subTest(payload=payload),patch('urllib.request.urlopen',return_value=self.response(payload)) as wire:
                with self.assertRaises(tr.OKXRequestError):self.get()
                wire.assert_called_once()
    def test_business_rate_limit_only_retries_reads(self):
        with patch('urllib.request.urlopen',side_effect=[self.response({'code':'50011','msg':'rate','data':[]}),self.response()]) as wire,patch.object(tr.time,'sleep'):
            self.get();self.assertEqual(wire.call_count,2)
        with patch('urllib.request.urlopen',return_value=self.response({'code':'50011','msg':'rate','data':[]})) as wire:
            with self.assertRaisesRegex(RuntimeError,'50011'):service._request('POST','/api/v5/account/set-leverage',{},self.env)
            wire.assert_called_once()
    def test_account_binding_is_rechecked_before_each_attempt(self):
        with patch('okxquant_backend.account_connections.assert_current',side_effect=[None,None,ValueError('account changed')]),patch('urllib.request.urlopen',side_effect=self.error()) as wire,patch.object(tr.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'account changed'):self.get()
        wire.assert_called_once()
    def test_error_headers_and_evidence_are_allowlisted(self):
        ray='a3c9816b0be70723-HKG'
        with patch('urllib.request.urlopen',side_effect=[self.error(503,{'CF-Ray':ray,'Server':'cloudflare','Content-Type':'text/html','X-Secret':'PRIVATE-HEADER'}) for _ in range(3)]),patch.object(tr.time,'sleep'),self.assertLogs(tr.LOG,level='WARNING') as logs:
            with self.assertRaises(tr.OKXRequestError) as caught:self.get()
        d=caught.exception.details;self.assertEqual(d['edge_request_id'],ray);self.assertEqual(d['response_type'],'html')
        text=json.dumps(d)+str(caught.exception)+' '.join(logs.output)
        for secret in ('PRIVATE-KEY','PRIVATE-SECRET','PRIVATE-PASS','PRIVATE-BODY','PRIVATE-HEADER','PRIVATE-ERROR'):self.assertNotIn(secret,text)
    def test_diagnostics_failure_does_not_repeat_successful_request(self):
        with patch('urllib.request.urlopen',side_effect=[self.error(),self.response()]) as wire,patch.object(tr.time,'sleep'),patch.object(tr,'emit',side_effect=REAL_EMIT),patch.object(strategy_evidence,'best_effort',side_effect=RuntimeError('storage unavailable')):
            self.assertEqual(self.get(),[])
        self.assertEqual(wire.call_count,2)
    def test_live_read_retry_never_adds_demo_header_or_switches_credentials(self):
        self.env=OKXEnvironment('live','LIVE-KEY','LIVE-SECRET','LIVE-PASS')
        with patch('urllib.request.urlopen',side_effect=[self.error(),self.response()]) as wire,patch.object(tr.time,'sleep'):
            self.get()
        for call in wire.call_args_list:
            headers={k.lower():v for k,v in call.args[0].header_items()}
            self.assertNotIn('x-simulated-trading',headers);self.assertEqual(headers['ok-access-key'],'LIVE-KEY')
    def test_api_auth_error_after_transient_failure_is_not_reported_recovered(self):
        with patch('urllib.request.urlopen',side_effect=[self.error(),self.response({'code':'50113','msg':'signature invalid','data':[]})]) as wire,patch.object(tr.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'50113'):self.get()
        self.assertEqual(wire.call_count,2);self.emit.assert_not_called()
    def test_final_failure_event_is_saved_in_account_scope(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(strategy_evidence,'DB_PATH',Path(temp)/'events.db'),patch('urllib.request.urlopen',side_effect=[self.error() for _ in range(3)]),patch.object(tr.time,'sleep'):
            self.patch.stop()
            try:
                with self.assertRaises(tr.OKXRequestError):self.get()
                events=strategy_evidence.export_events(self.env.identity,'exchange_read_failure')
                self.assertEqual(len(events),1);self.assertEqual(events[0]['payload']['http_status'],503)
                self.assertEqual(events[0]['payload']['attempts'],3)
            finally:self.patch.start()
