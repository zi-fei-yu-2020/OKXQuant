import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import okxquant_backend.app as app_module
from okxquant_backend.admin_auth import AdminAuthStore
from scripts import memory_registry as memory, trade_lock

class MemoryRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.scope='okx:demo:api-fixture'
        for obj,name,value in [(app_module,'DATA_DIR',self.root),(trade_lock,'PATH',self.root/'writer.lock')]:
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(memory,'scope_of',side_effect=lambda s=None:s or self.scope);p.start();self.addCleanup(p.stop)
        store=AdminAuthStore(self.root/'admins.db');store.initialize_from_legacy('MemoryFixture123456')
        p=patch.object(app_module,'admin_auth',store);p.start();self.addCleanup(p.stop)
        self.client=TestClient(app_module.app)
        self.headers=self.login('admin','MemoryFixture123456')
        (self.root/'AI_TRADING_MEMORY.md').write_text('OLD EFFECTIVE MEMORY',encoding='utf8')
        (self.root/'trading_ledger.json').write_text(json.dumps([{'id':v,'status':'closed','environment_id':self.scope,'net_pnl':1} for v in ['t1','t2']]))
    def login(self,name,password):
        r=self.client.post('/api/v1/admin/auth/login',json={'username':name,'password':password});self.assertEqual(r.status_code,200,r.text)
        return {'X-OKXQuant-Session':r.json()['session_token']}
    def state(self):
        r=self.client.get('/api/v1/admin/memory',headers=self.headers);self.assertEqual(r.status_code,200,r.text);return r.json()['publication']
    def create(self):
        r=self.client.post('/api/v1/admin/memory/candidates',headers=self.headers,json={'scope':self.scope,'action':'ADD','text':'量能变化仅作为观察证据，结合结构与成本进行复查，不预设收益。','request_id':'fixture-request-1'})
        self.assertEqual(r.status_code,200,r.text);return r.json()['candidate_id']
    def test_read_is_protected_and_never_initializes(self):
        self.assertEqual(self.client.get('/api/v1/admin/memory').status_code,401)
        self.assertEqual(self.state()['status'],'legacy_unmanaged')
        self.assertFalse((self.root/'memory_registry.db').exists())
    def test_operator_cannot_initialize_or_publish_or_use_retired_writers(self):
        r=self.client.post('/api/v1/admin/users',headers=self.headers,json={'username':'reader','password':'ReadFixture123456','role':'admin'});self.assertEqual(r.status_code,200,r.text)
        reader=self.login('reader','ReadFixture123456')
        self.assertEqual(self.client.get('/api/v1/admin/memory',headers=reader).status_code,200)
        self.assertEqual(self.client.post('/api/v1/admin/memory/initialize',headers=reader,json={'scope':self.scope,'confirmation':'INITIALIZE MEMORY'}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/admin/memory/rollback',headers=reader).status_code,403)
        self.assertEqual(self.client.post('/api/v1/admin/memory/toggle/old-rule',headers=reader).status_code,403)
    def test_candidate_publish_requires_confirmation_evidence_and_current_revision(self):
        before=self.state()['prompt_hash'];identity=self.create();state=self.state()
        self.assertEqual(before,state['prompt_hash'])
        body={'scope':self.scope,'expected_revision':state['revision'],'note':'已经核对两笔实际成交与对应反例，作为软经验记录。','supporting_trade_ids':['t1','t2'],'evidence_hash':state['evidence_hash'],'confirmation':'WRONG'}
        path=f'/api/v1/admin/memory/candidates/{identity}/publish'
        self.assertEqual(self.client.post(path,json=body).status_code,401)
        self.assertEqual(self.client.post(path,headers=self.headers,json=body).status_code,400)
        body['confirmation']='PUBLISH MEMORY';body['expected_revision']-=1
        self.assertEqual(self.client.post(path,headers=self.headers,json=body).status_code,409)
        body['expected_revision']=state['revision'];r=self.client.post(path,headers=self.headers,json=body)
        self.assertEqual(r.status_code,200,r.text)
        current=self.state();self.assertNotEqual(current['prompt_hash'],before)
        self.assertEqual(len([x for x in current['rules'] if x['enabled']]),1)
        self.assertEqual(self.client.post(path,headers=self.headers,json=body).status_code,409)
        version=self.client.get(f"/api/v1/admin/memory/versions/{current['active_version']}",headers=self.headers)
        self.assertEqual(version.status_code,200);self.assertEqual(version.json()['prompt_hash'],current['prompt_hash'])
    def test_legacy_toggle_bulk_and_baseline_routes_cannot_bypass_publication(self):
        before=self.state()['prompt_text']
        for method,path,body in [('post','/api/v1/admin/memory/rollback',{}),('post','/api/v1/admin/memory/toggle/old',{}),('delete','/api/v1/admin/memory/0',None),('put','/api/v1/admin/memory',{'items':['Unreviewed replacement']})]:
            r=self.client.request(method,path,headers=self.headers,json=body);self.assertEqual(r.status_code,410,r.text)
        self.assertEqual(self.state()['prompt_text'],before)
    def test_scope_and_authority_cannot_be_injected_in_candidate_payload(self):
        payload={'scope':'okx:live:foreign','action':'ADD','text':'量能变化只是反证，不能用于伪造交易优势。','request_id':'fixture-request-2'}
        self.assertEqual(self.client.post('/api/v1/admin/memory/candidates',headers=self.headers,json=payload).status_code,409)
        payload['scope']=self.scope;payload['actor']='forged-superadmin'
        self.assertEqual(self.client.post('/api/v1/admin/memory/candidates',headers=self.headers,json=payload).status_code,422)

if __name__=='__main__':unittest.main()
