import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from okxquant_backend import macro_store as store, macro_routes as routes
from okxquant_gateway import secrets
from scripts import macro_market as m

class OfficialMacroStoreTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for obj,name,value in [(store,'CONFIG_FILE',self.root/'config.json'),(m,'CACHE_FILE',self.root/'cache.json'),(secrets,'KEY_FILE',self.root/'vault.key'),(secrets,'STORE_FILE',self.root/'vault.enc')]:
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
    def test_settings_do_not_read_credentials_or_environment(self):
        with patch('scripts.okx_runtime._load_dotenv',side_effect=AssertionError('credential lookup')):
            out=store.status()
        self.assertEqual(set(out['configuration']),{'official_enabled','revision'})
    def test_legacy_provider_settings_ignored_and_removed_on_save(self):
        store.CONFIG_FILE.write_text(json.dumps({'official_enabled':False,'revision':2,'fmp_enabled':True,'calendar_timezone':'UTC','api_key':'old-secret'}))
        old=store.load_settings();self.assertFalse(old['official_enabled'])
        self.assertNotIn('api_key',old);self.assertNotIn('fmp_enabled',old)
        out=store.update(official_enabled=False,expected_revision=2)
        self.assertEqual(out['configuration'],{'official_enabled':False,'revision':3})
        self.assertEqual(set(json.loads(store.CONFIG_FILE.read_text())),{'official_enabled','revision'})
    def test_save_never_writes_vault_and_preserves_revision_conflicts(self):
        with patch.object(secrets,'save_secrets') as save,patch.object(secrets,'delete_secrets') as delete:
            store.update(official_enabled=True,expected_revision=0)
            with self.assertRaises(ValueError):store.update(official_enabled=False,expected_revision=0)
            save.assert_not_called();delete.assert_not_called()
        self.assertTrue(store.load_settings()['official_enabled'])
    def test_corrupt_configuration_fails_closed(self):
        store.CONFIG_FILE.write_text('{broken')
        with self.assertRaises(ValueError):store.load_settings()
    def test_retired_key_not_accepted_by_secret_registry(self):
        self.assertNotIn('FMP_API_KEY',secrets.SECRET_KEYS)
        secrets.save_secrets({'FMP_API_KEY':'old-key','LLM_API_KEY':'keep-key'})
        self.assertEqual(secrets.load_secrets(),{'LLM_API_KEY':'keep-key'})

class OfficialMacroRoutesTests(unittest.TestCase):
    def setUp(self):
        self.audits=[];app=FastAPI()
        def auth(token):
            if token!='super':raise HTTPException(403,'admin required')
            return {'username':'fixture'}
        routes.install(app,auth,lambda *args:self.audits.append(args))
        self.client=TestClient(app);self.headers={'X-OKXQuant-Session':'super'}
    def test_every_route_is_protected_before_side_effect(self):
        with patch.object(store,'status') as read,patch.object(store,'update') as write,patch.object(routes.subprocess,'run') as run:
            self.assertEqual(self.client.get('/api/v1/admin/macro-data').status_code,403)
            self.assertEqual(self.client.put('/api/v1/admin/macro-data',json={'official_enabled':True,'expected_revision':0}).status_code,403)
            self.assertEqual(self.client.post('/api/v1/admin/macro-data/refresh').status_code,403)
            read.assert_not_called();write.assert_not_called();run.assert_not_called()
    def test_retired_fields_rejected_without_echoing_secrets(self):
        for field,value in [('fmp_enabled',True),('api_key','do-not-echo'),('calendar_timezone','UTC'),('clear_api_key',True)]:
            with self.subTest(field=field),patch.object(store,'update') as write:
                r=self.client.put('/api/v1/admin/macro-data',headers=self.headers,json={'official_enabled':True,'expected_revision':0,field:value})
                self.assertEqual(r.status_code,422);self.assertNotIn('do-not-echo',r.text);write.assert_not_called()
    def test_official_configuration_update_is_supported(self):
        with patch.object(store,'update',return_value={'saved':True}) as update:
            r=self.client.put('/api/v1/admin/macro-data',headers=self.headers,json={'official_enabled':False,'expected_revision':0})
        self.assertEqual(r.status_code,200);update.assert_called_once_with(official_enabled=False,expected_revision=0)
    def test_refresh_is_a_bounded_official_collector_not_a_model_call(self):
        with patch.object(routes.subprocess,'run') as run,patch.object(store,'status',return_value={'snapshot':{}}):
            run.return_value.returncode=0
            r=self.client.post('/api/v1/admin/macro-data/refresh',headers=self.headers)
        self.assertEqual(r.status_code,200);self.assertEqual(run.call_args.kwargs['timeout'],110)
        self.assertTrue(run.call_args.args[0][-1].endswith('macro_market.py'))
    def test_unexpected_errors_never_echo_private_context(self):
        with patch.object(store,'status',side_effect=RuntimeError('private-value')):
            r=self.client.get('/api/v1/admin/macro-data',headers=self.headers)
        self.assertEqual(r.status_code,503);self.assertNotIn('private-value',r.text+json.dumps(self.audits))

if __name__=='__main__':unittest.main()
