import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from okxquant_backend import app as api
from okxquant_backend.admin_auth import AdminAuthStore
from okxquant_gateway.store import GatewayStore

class ControlBridgeAudit(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.auth=AdminAuthStore(self.root/'admin.db')
        self.auth.initialize_from_legacy('AuditLocalPassword123')
        for name,value in [('admin_auth',self.auth),('GATEWAY_DB_PATH',self.root/'gateway.db')]:
            p=patch.object(api,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(api,'audit_record');p.start();self.addCleanup(p.stop)
        self.client=TestClient(api.app)
        self.headers=self.login('admin','AuditLocalPassword123')
    def login(self,user,password):
        r=self.client.post('/api/v1/admin/auth/login',json={'username':user,'password':password})
        self.assertEqual(r.status_code,200,r.text)
        return {'X-OKXQuant-Session':r.json()['session_token']}
    def post(self,path,body,headers=None):return self.client.post('/api/v1/admin/'+path,headers=headers or self.headers,json=body)
    def test_review_request_is_persistent_and_duplicate_click_reuses_id(self):
        path='gateway/jobs/self_improvement/run';body={'confirmation':'RUN EVOLUTION'}
        first=self.post(path,body);second=self.post(path,body)
        self.assertEqual(first.status_code,202,first.text)
        self.assertEqual(first.json()['status'],'queued')
        self.assertEqual(first.json()['request_id'],second.json()['request_id'])
        self.assertTrue(second.json()['deduplicated'])
        self.assertEqual(len(GatewayStore(self.root/'gateway.db').job_requests()),1)
    def test_manual_review_cannot_be_used_to_trigger_trader(self):
        self.assertEqual(self.post('gateway/jobs/trader/run',{'confirmation':'RUN EVOLUTION'}).status_code,404)
        self.assertEqual(self.post('gateway/jobs/self_improvement/run',{'confirmation':'RUN JOB'}).status_code,400)
        self.assertFalse((self.root/'gateway.db').exists())
    def test_ordinary_admin_cannot_authorize_live_or_execute_trusted_rules(self):
        self.auth.create_user('operator','OperatorPassword123','admin')
        headers=self.login('operator','OperatorPassword123')
        for path,body in [('strategy/engine/live-authorization',{'enabled':True,'confirmation':'ENABLE LIVE SCALP'}),('gateway/jobs/self_improvement/run',{'confirmation':'RUN EVOLUTION'}),('interceptors/test',{})]:
            with self.subTest(path=path):self.assertEqual(self.post(path,body,headers).status_code,403)
    def test_live_actor_is_server_session_not_request_claim(self):
        env=SimpleNamespace(mode='live',identity='okx:live:test')
        with patch('scripts.okx_runtime.selected_environment',return_value=env),patch('scripts.strategy_engine_runtime.authorize_live',return_value={'environment':'live','status':'ready','enabled':True}) as call:
            r=self.post('strategy/engine/live-authorization',{'enabled':True,'confirmation':'ENABLE LIVE SCALP','actor':'forged','expected_binding':{'account_scope':env.identity}})
            self.assertEqual(r.status_code,200,r.text)
            call.assert_called_once_with(env,actor='admin',expected_binding={'account_scope':env.identity})
    def test_authorization_confirmation_checked_before_service(self):
        with patch('scripts.strategy_engine_runtime.authorize_live') as call:
            self.assertEqual(self.post('strategy/engine/live-authorization',{'enabled':True,'confirmation':'YES'}).status_code,400)
            call.assert_not_called()
