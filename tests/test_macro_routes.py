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


class MacroStoreTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for obj,name,value in [(store,'CONFIG_FILE',self.root/'config.json'),(m,'CACHE_FILE',self.root/'cache.json'),
                (secrets,'KEY_FILE',self.root/'vault.key'),(secrets,'STORE_FILE',self.root/'vault.enc')]:
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        # No real user secrets/configuration are consulted or modified.
        p=patch('scripts.okx_runtime._load_dotenv',side_effect=lambda:secrets.load_secrets(strict=True));p.start();self.addCleanup(p.stop)
    def update(self,**kwargs):
        return store.update(**{'official_enabled':True,'fmp_enabled':True,'calendar_timezone':'','expected_revision':0,**kwargs})
    def test_key_is_encrypted_and_not_returned(self):
        out=self.update(api_key='private-fixture-123')
        self.assertTrue(out['configuration']['api_key_configured'])
        self.assertNotIn('private-fixture-123',json.dumps(out))
        self.assertNotIn(b'private-fixture-123',secrets.STORE_FILE.read_bytes())
        self.assertNotIn('private-fixture-123',store.CONFIG_FILE.read_text())
        self.assertEqual(secrets.load_secrets()['FMP_API_KEY'],'private-fixture-123')
    def test_key_rotation_invalidates_binding(self):
        self.update(api_key='private-fixture-123');old=store.load_settings()['binding']
        self.update(api_key='private-fixture-456',expected_revision=1)
        self.assertNotEqual(old,store.load_settings()['binding'])
    def test_stale_edit_cannot_overwrite_config_or_key(self):
        self.update(api_key='private-fixture-123')
        with self.assertRaises(ValueError):self.update(api_key='private-fixture-456')
        self.assertEqual(secrets.load_secrets()['FMP_API_KEY'],'private-fixture-123')
    def test_blank_key_is_rejected_but_none_preserves_existing(self):
        self.update(api_key='private-fixture-123')
        with self.assertRaises(ValueError):self.update(api_key='',expected_revision=1)
        self.update(expected_revision=1)
        self.assertEqual(secrets.load_secrets()['FMP_API_KEY'],'private-fixture-123')
    def test_delete_disables_provider_and_does_not_leave_vault_key(self):
        self.update(api_key='private-fixture-123')
        self.update(clear_api_key=True,expected_revision=1)
        self.assertFalse(store.load_settings()['fmp_enabled'])
        self.assertNotIn('FMP_API_KEY',secrets.load_secrets())
    def test_corrupt_configuration_does_not_silently_enable_feeds(self):
        store.CONFIG_FILE.write_text('{broken')
        with self.assertRaises(ValueError):store.load_settings()
    def test_secret_format_error_never_echoes_value(self):
        value='private\nsecret!'
        with self.assertRaises(ValueError) as e:self.update(api_key=value)
        self.assertNotIn(value,str(e.exception))


class MacroRoutesTests(unittest.TestCase):
    def setUp(self):
        self.audits=[];app=FastAPI()
        def auth(token):
            if token!='super':raise HTTPException(403,'admin required')
            return {'username':'fixture'}
        routes.install(app,auth,lambda *args:self.audits.append(args))
        self.client=TestClient(app);self.headers={'X-OKXQuant-Session':'super'}
    def test_every_route_checks_superadmin_before_side_effect(self):
        body={'official_enabled':True,'fmp_enabled':False,'expected_revision':0}
        with patch.object(store,'status') as read,patch.object(store,'update') as write,patch.object(routes.subprocess,'run') as run:
            self.assertEqual(self.client.get('/api/v1/admin/macro-data').status_code,403)
            self.assertEqual(self.client.put('/api/v1/admin/macro-data',json=body).status_code,403)
            self.assertEqual(self.client.post('/api/v1/admin/macro-data/refresh').status_code,403)
            read.assert_not_called();write.assert_not_called();run.assert_not_called()
    def test_key_is_not_in_audit(self):
        body={'official_enabled':True,'fmp_enabled':True,'expected_revision':0,'api_key':'private-fixture-123'}
        with patch.object(store,'update',return_value={'saved':True}) as write:
            response=self.client.put('/api/v1/admin/macro-data',headers=self.headers,json=body)
        self.assertEqual(response.status_code,200)
        self.assertEqual(write.call_args.kwargs['api_key'],body['api_key'])
        self.assertNotIn(body['api_key'],json.dumps(self.audits))
    def test_probe_has_hard_deadline_and_no_model_call(self):
        with patch.object(routes.subprocess,'run') as run,patch.object(store,'status',return_value={'snapshot':{}}):
            run.return_value.returncode=0
            response=self.client.post('/api/v1/admin/macro-data/refresh',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(run.call_args.kwargs['timeout'],110)
        self.assertTrue(run.call_args.args[0][-1].endswith('macro_market.py'))
    def test_unexpected_exception_cannot_leak_provider_url_or_key(self):
        with patch.object(store,'status',side_effect=RuntimeError('https://provider?apikey=private-fixture-123')):
            response=self.client.get('/api/v1/admin/macro-data',headers=self.headers)
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private-fixture-123',response.text+json.dumps(self.audits))


if __name__=='__main__':unittest.main()
