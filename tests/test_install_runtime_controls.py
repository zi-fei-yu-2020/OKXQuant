import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import nullcontext
from fastapi.testclient import TestClient
from fastapi import HTTPException
import okxquant_backend.app as api
from scripts.init_env import initialize
from scripts.okx_runtime import OKXEnvironment

class InstallTests(unittest.TestCase):
    def test_observation_defaults_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'.env';initialize(path)
            text=path.read_text();self.assertIn('OKXQUANT_AUTOTRADE_ENABLED=0',text)
            self.assertIn('OKXQUANT_RUNTIME_PROFILE=light',text);self.assertIn('OKXQUANT_GATEWAY_AUTOSTART=1',text)
            token=next(x.split('=',1)[1] for x in text.splitlines() if x.startswith('OKXQUANT_SETUP_TOKEN='))
            self.assertGreaterEqual(len(token),24);self.assertTrue(any(c.isdigit() for c in token))
            with self.assertRaises(FileExistsError):initialize(path)
            self.assertEqual(path.read_text(),text)

class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(api.app);self.addCleanup(self.client.close)
        for target,kwargs in [('okxquant_backend.account_connections.registry_guard',{'side_effect':nullcontext}),('scripts.config_lock.configuration_write',{'side_effect':lambda *a,**kw:nullcontext()}),('okxquant_backend.account_connections.assert_current',{})]:
            p=patch(target,**kwargs);p.start();self.addCleanup(p.stop)
    def test_only_superadmin_can_control_entries(self):
        with patch.object(api,'require_superadmin',side_effect=HTTPException(403,'forbidden')),patch.object(api,'update_env') as save:
            self.assertEqual(self.client.put('/api/v1/admin/runtime-controls',json={'automatic_trader':False,'confirmation':'PAUSE AUTO'}).status_code,403);save.assert_not_called()
    def test_confirmation_matches_environment_and_does_not_grant_other_consent(self):
        env=OKXEnvironment('demo','fake','fake','fake')
        with patch.object(api,'require_superadmin',return_value={'username':'test'}),patch('scripts.okx_runtime.selected_environment',return_value=env),patch.object(api,'update_env') as save,patch.object(api,'audit_record'):
            for body in [{'automatic_trader':True,'confirmation':'ENABLE LIVE AUTO'},{'automatic_trader':'true','confirmation':'ENABLE DEMO AUTO'},{'automatic_trader':False,'confirmation':'wrong'}]:
                self.assertEqual(self.client.put('/api/v1/admin/runtime-controls',json=body).status_code,422)
            save.assert_not_called()
            response=self.client.put('/api/v1/admin/runtime-controls',json={'automatic_trader':True,'confirmation':'ENABLE DEMO AUTO'})
            self.assertEqual(response.status_code,200,response.text);save.assert_called_once_with({'OKXQUANT_AUTOTRADE_ENABLED':True})
            self.assertTrue(response.json()['protection_unchanged']);self.assertFalse(response.json()['orders_canceled'])
    def test_pause_is_possible_without_credentials_but_enable_is_not(self):
        with patch.object(api,'require_superadmin',return_value={'username':'test'}),patch('scripts.okx_runtime.selected_environment',return_value=OKXEnvironment('demo','','','')),patch.object(api,'update_env') as save,patch.object(api,'audit_record'):
            self.assertEqual(self.client.put('/api/v1/admin/runtime-controls',json={'automatic_trader':True,'confirmation':'ENABLE DEMO AUTO'}).status_code,409)
            self.assertEqual(self.client.put('/api/v1/admin/runtime-controls',json={'automatic_trader':False,'confirmation':'PAUSE AUTO'}).status_code,200)
            save.assert_called_once_with({'OKXQUANT_AUTOTRADE_ENABLED':False})


class ManagedUnboundPauseTests(unittest.TestCase):
    def test_pause_does_not_require_a_tradeable_binding(self):
        from okxquant_backend import account_connections, settings_store
        env=OKXEnvironment('demo','','','',source='account-center-unbound')
        # This is the actual production rejection, deliberately not patched away.
        with self.assertRaises(account_connections.AccountChangeError):account_connections.assert_current(env)
        client=TestClient(api.app)
        try:
            with tempfile.TemporaryDirectory() as tmp,patch.object(account_connections,'DATA',Path(tmp)),patch.object(api,'require_superadmin',return_value={'username':'fixture'}),patch('scripts.okx_runtime.selected_environment',return_value=env),patch.object(api,'update_env') as save,patch.object(api,'audit_record'):
                response=client.put('/api/v1/admin/runtime-controls',json={'automatic_trader':False,'confirmation':'PAUSE AUTO'})
                self.assertEqual(response.status_code,200,response.text)
                save.assert_called_once_with({'OKXQUANT_AUTOTRADE_ENABLED':False})
                self.assertEqual(client.put('/api/v1/admin/runtime-controls',json={'automatic_trader':True,'confirmation':'ENABLE DEMO AUTO'}).status_code,409)
        finally:client.close()
