"""Second independent cross-review. Real ASGI/TestClient + temp stores; production is read-only."""
import copy
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from okxquant_backend import app as api, account_baseline
from okxquant_backend.admin_auth import AdminAuthStore
from scripts import prompt_library as profiles, strategy_engine_runtime as engine
from scripts import execution_profiles, trading_prompt, risk_policy
from okxquant_gateway.store import GatewayStore

BASE = '/api/v1/admin'

class CrossReviewContracts(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='cross-contract-')))
        self.env = SimpleNamespace(mode='live', identity='okx:live:cross-a', connection_id='cross-a',
                                   binding_version=1, configured=True, simulated=False)
        auth = AdminAuthStore(self.root/'auth.db')
        auth.initialize_from_legacy('CrossReviewAdmin123!')
        for module, name, value in ((api, 'admin_auth', auth), (api, 'DATA_DIR', self.root),
                (api, 'GATEWAY_DB_PATH', self.root/'gateway.db'),
                (api, 'PROMPT_OVERRIDE_FILE', self.root/'system_prompt_override.txt'),
                (profiles, 'LIBRARY_FILE', self.root/'profiles.json'),
                (engine, 'STATE_FILE', self.root/'engine.json'),
                (account_baseline, 'BASELINE_FILE', self.root/'baseline.json')):
            self.stack.enter_context(patch.object(module, name, value))
        self.stack.enter_context(patch.object(api, 'refresh_settings'))
        self.audit = self.stack.enter_context(patch.object(api, 'audit_record'))
        self.stack.enter_context(patch('scripts.okx_runtime.selected_environment', side_effect=lambda: self.env))
        self.stack.enter_context(patch('okxquant_backend.account_connections.assert_current'))
        self.stack.enter_context(patch.object(engine, '_autotrade_enabled', return_value=True))
        self.client = TestClient(api.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.superuser = self.login('admin', 'CrossReviewAdmin123!')
        created = self.client.post(BASE+'/users', headers=self.superuser,
            json={'username':'reader','password':'CrossReviewReader123!','role':'admin'})
        self.assertEqual(created.status_code, 200)
        self.reader = self.login('reader', 'CrossReviewReader123!')

    def login(self, username, password):
        response = self.client.post(BASE+'/auth/login', json={'username':username,'password':password})
        self.assertEqual(response.status_code, 200)
        return {'X-OKXQuant-Session': response.json()['session_token']}

    def request(self, method, path, payload=None, headers=None):
        return self.client.request(method, BASE+path, headers=self.superuser if headers is None else headers,
                                   **({'json':payload} if payload is not None else {}))

    def authorize(self, enabled=True, binding=None):
        if binding is None:
            status=self.request('GET','/strategy/engine')
            self.assertEqual(status.status_code,200)
            binding=status.json()
        return self.request('POST','/strategy/engine/live-authorization',
            {'enabled':enabled,'confirmation':'ENABLE LIVE SCALP' if enabled else 'DISABLE LIVE SCALP',
             'expected_binding':binding})

    def create(self, name='Cross review', source='stable'):
        result = self.request('POST','/prompt-profiles', {'name':name,'description':'contract fixture','source_id':source})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['profile']

    def test_profile_create_duplicate_import_and_independent_execution_binding(self):
        source = self.create(source='small300')
        updated = self.request('PUT','/prompt-profiles/'+source['id'],
            {'name':source['name'],'editor_mode':'advanced','trading_system':'Cross review independent preference'})
        self.assertEqual(updated.status_code, 200, updated.text)
        duplicate = self.create('Duplicate', source['id'])
        self.assertEqual(duplicate['execution_profile'], 'small300')
        self.assertEqual(duplicate['trading_system'], 'Cross review independent preference')
        export = self.request('GET','/prompt-profiles/'+duplicate['id']+'/export', headers=self.reader)
        self.assertEqual(export.status_code, 200)
        package = export.json(); package['profile']['name']='Imported'; package['profile']['execution_profile']='standard'
        imported = self.request('POST','/prompt-profiles/import', {'payload':package,'name_override':'Imported'})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['profile']['execution_profile'], 'standard')
        self.assertEqual(profiles.active_profile()['id'], 'stable')
        prior = profiles.get_profile(duplicate['id'])
        response = self.request('PUT','/prompt-profiles/'+duplicate['id'], {'name':duplicate['name'],'execution_profile':'standard'})
        self.assertEqual(response.status_code, 200)
        current = profiles.get_profile(duplicate['id'])
        for key in ('trading_system','trading_user','evolution_system','evolution_user','editor_mode'):
            self.assertEqual(current[key], prior[key], key)

    def test_activate_and_hotload_claim_next_decision_not_execution(self):
        source = self.create(source='small300')
        response = self.request('POST','/prompt-profiles/'+source['id']+'/activate', {})
        self.assertEqual(response.status_code, 200, response.text)
        result=response.json()
        self.assertEqual(result['effective_for'], 'next_fresh_decision')
        self.assertFalse(result['decision_generation_triggered'])
        self.assertFalse(result['existing_positions_resized'])
        self.assertEqual(result['decision_cache_status'], 'not_regenerated')
        self.assertEqual(execution_profiles.runtime()['execution']['id'], 'small300')
        before=trading_prompt.profile_signature(profiles.active_profile())
        response=self.request('PUT','/prompt-profiles/'+source['id'], {'name':source['name'],'trading_system':'Updated preference','editor_mode':'advanced'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotEqual(trading_prompt.profile_signature(profiles.active_profile()),before)
        self.assertFalse(response.json()['decision_generation_triggered'])

    def test_profile_role_boundaries_and_error_cache_headers(self):
        source=self.create()
        writes=[('POST','/prompt-profiles',{'name':'Denied'}),
                ('PUT','/prompt-profiles/'+source['id'],{'name':'Denied'}),
                ('POST','/prompt-profiles/'+source['id']+'/activate',{}),
                ('POST','/prompt-profiles/import',{'payload':{},'name_override':'Denied'}),
                ('DELETE','/prompt-profiles/'+source['id'],None)]
        before=profiles.LIBRARY_FILE.read_bytes()
        for method,path,payload in writes:
            for headers,expected in (({},401),(self.reader,403)):
                with self.subTest(method=method,path=path,role=expected):
                    response=self.request(method,path,payload,headers=headers)
                    self.assertEqual(response.status_code,expected)
                    self.assertIn('no-store',response.headers.get('cache-control',''))
        self.assertEqual(profiles.LIBRARY_FILE.read_bytes(),before)

    def test_failed_profile_validation_and_failed_save_are_not_success(self):
        source=self.create()
        before=profiles.LIBRARY_FILE.read_bytes()
        invalid=self.request('PUT','/prompt-profiles/'+source['id'], {'name':source['name'],'execution_profile':'not-a-preset'})
        self.assertEqual(invalid.status_code,422)
        self.assertEqual(profiles.LIBRARY_FILE.read_bytes(),before)
        with patch.object(profiles.os,'replace',side_effect=OSError('synthetic storage unavailable')):
            failed=self.request('PUT','/prompt-profiles/'+source['id'], {'name':'Not saved'})
        self.assertGreaterEqual(failed.status_code,400)
        self.assertEqual(profiles.LIBRARY_FILE.read_bytes(),before)
        self.assertNotIn('"saved":true',failed.text)

    def test_review_exact_ui_payload_persists_deduplicates_and_has_no_publication(self):
        response=self.request('POST','/gateway/jobs/self_improvement/run',{'confirmation':'RUN EVOLUTION'})
        self.assertEqual(response.status_code,202,response.text)
        result=response.json();self.assertTrue(result['accepted']);self.assertEqual(result['status'],'queued')
        store=GatewayStore(self.root/'gateway.db')
        self.assertEqual(len(store.pending_job_requests()),1)
        repeated=self.request('POST','/gateway/jobs/self_improvement/run',{'confirmation':'RUN EVOLUTION'})
        self.assertTrue(repeated.json()['deduplicated'])
        self.assertEqual(repeated.json()['request_id'],result['request_id'])
        claimed=store.claim_job_request(result['request_id'])
        self.assertIsNotNone(claimed)
        running=self.request('POST','/gateway/jobs/self_improvement/run',{'confirmation':'RUN EVOLUTION'})
        self.assertEqual(running.json()['status'],'running')
        self.assertEqual(running.json()['request_id'],result['request_id'])

    def test_review_role_confirmation_and_job_allowlist(self):
        for headers,payload,expected in (({}, {'confirmation':'RUN EVOLUTION'},401),
                (self.reader,{'confirmation':'RUN EVOLUTION'},403),
                (self.superuser,{'confirmation':'run evolution'},400),
                (self.superuser,{},422)):
            response=self.request('POST','/gateway/jobs/self_improvement/run',payload,headers=headers)
            self.assertEqual(response.status_code,expected)
            self.assertIn('no-store',response.headers.get('cache-control',''))
        self.assertEqual(self.request('POST','/gateway/jobs/trader/run',{'confirmation':'RUN EVOLUTION'}).status_code,404)

    def test_live_engine_rbac_confirmation_enable_revoke(self):
        path='/strategy/engine/live-authorization'
        for headers,payload,expected in (({}, {'enabled':True,'confirmation':'ENABLE LIVE SCALP'},401),
                (self.reader,{'enabled':True,'confirmation':'ENABLE LIVE SCALP'},403),
                (self.superuser,{'enabled':True,'confirmation':'DISABLE LIVE SCALP'},400)):
            response=self.request('POST',path,payload,headers=headers)
            self.assertEqual(response.status_code,expected)
        self.assertFalse(engine.STATE_FILE.exists())
        response=self.authorize()
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['authorized'])
        self.assertEqual(response.json()['actor'],'admin')
        response=self.authorize(False)
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['authorized'])
        self.assertFalse(response.json()['enabled'])

    def test_live_engine_profile_edit_invalidates_existing_consent(self):
        profile=self.create()
        self.request('POST','/prompt-profiles/'+profile['id']+'/activate',{})
        allowed=self.authorize()
        self.assertEqual(allowed.status_code,200,allowed.text)
        self.request('PUT','/prompt-profiles/'+profile['id'],{'name':profile['name'],'execution_profile':'small300'})
        status=self.request('GET','/strategy/engine',headers=self.reader)
        self.assertEqual(status.status_code,200)
        self.assertFalse(status.json()['authorized'])
        self.assertEqual(status.json()['status'],'binding_changed')

    def test_live_authorization_must_not_silently_target_a_different_account_than_confirmation_view(self):
        seen=self.request('GET','/strategy/engine').json()
        self.assertEqual(seen['account_scope'],'okx:live:cross-a')
        self.env=SimpleNamespace(mode='live',identity='okx:live:cross-b',connection_id='cross-b',binding_version=2,configured=True,simulated=False)
        # Regression for the original pre-fix UI payload: missing binding must stay rejected.
        response=self.request('POST','/strategy/engine/live-authorization',{'enabled':True,'confirmation':'ENABLE LIVE SCALP'})
        self.assertEqual(response.status_code,409, 'P1: missing binding authorized current account B')
        self.assertFalse(engine.status(self.env)['authorized'])

    def test_baseline_api_migrates_without_assigning_demo_legacy_capital_to_live(self):
        legacy={'initial_capital':500,'reset_time':'2025-01-01 00:00:00'}
        account_baseline.BASELINE_FILE.write_text(json.dumps(legacy))
        before=account_baseline.load_account_baseline(self.env.identity)
        self.assertFalse(before['baseline_configured'])
        response=self.request('PUT','/account-baseline',{'initial_capital':300,'confirmation':'UPDATE CAPITAL'})
        self.assertEqual(response.status_code,200,response.text)
        saved=json.loads(account_baseline.BASELINE_FILE.read_text())
        self.assertEqual(saved['legacy_baseline'],legacy)
        self.assertEqual(saved['baselines'][self.env.identity]['initial_capital'],300)
        self.assertFalse(account_baseline.load_account_baseline('okx:live:other')['baseline_configured'])

    def test_public_snapshot_does_not_return_previous_account_data(self):
        from dashboard import app as dash
        old={'account_source_id':'okx:demo:previous','account':{'total_equity':12345},'private_marker':'previous-account-only'}
        with patch.object(dash,'CACHE_DATA',old),patch.object(dash,'LAST_CACHE_TIME',time.time()),patch.object(dash,'request_cache_refresh') as refresh:
            client=TestClient(dash.app)
            self.addCleanup(client.close)
            for path in ('/api/all','/api/overview'):
                result=client.get(path)
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['account_source_id'],self.env.identity)
                self.assertTrue(result.json()['initializing'])
                self.assertNotIn('previous-account-only',result.text)
                self.assertEqual(result.json()['account'],{})
                self.assertIn('no-store',result.headers.get('cache-control',''))
            self.assertTrue(refresh.called)

    def test_legacy_prompt_library_cannot_bypass_superadmin_activation_role(self):
        response=self.request('PUT','/prompt-library',{'active_style':'small300','trading_system':'Reader supplied preference'},headers=self.reader)
        observed=profiles.active_profile()['id']
        self.assertEqual(response.status_code,403,
            f'P1: ordinary admin changed active profile through legacy endpoint; active={observed}')
        self.assertEqual(observed,'stable')

    def test_legacy_prompt_override_cannot_bypass_superadmin_edit_role(self):
        response=self.request('PUT','/prompts',{'content':'Reader supplied preference'},headers=self.reader)
        persisted=api.PROMPT_OVERRIDE_FILE.exists()
        self.assertEqual(response.status_code,403,
            f'P1: ordinary admin wrote live prompt override through legacy endpoint; persisted={persisted}')
        self.assertFalse(persisted)

    def test_public_cache_routes_do_not_bypass_current_account_snapshot_scope(self):
        for resource,filename in (('decisions','ai_brain_decisions.json'),('self-improvement','self_improvement_report.json')):
            with self.subTest(resource=resource):
                old={'account_source_id':'okx:live:previous','account_scope':'okx:live:previous',
                     'scope':'okx:live:previous','environment_id':'okx:live:previous',
                     'review_markdown':'CROSS_REVIEW_PREVIOUS_ACCOUNT_PRIVATE_MARKER'}
                (self.root/filename).write_text(json.dumps(old))
                response=self.client.get('/api/v1/cache/'+resource)
                self.assertNotIn('CROSS_REVIEW_PREVIOUS_ACCOUNT_PRIVATE_MARKER',response.text,
                    'P1: unscoped public cache route exposes the previous account payload')

    def test_admin_unhandled_storage_failure_is_not_cacheable(self):
        source=self.create()
        with patch.object(profiles.os,'replace',side_effect=OSError('synthetic storage unavailable')):
            response=self.request('PUT','/prompt-profiles/'+source['id'],{'name':'Failed update'})
        self.assertEqual(response.status_code,500)
        self.assertIn('no-store',response.headers.get('cache-control',''),
                      'P2: unhandled admin 500 bypasses the cache-control middleware')

    def test_prompt_library_actual_page_contract_has_module_views_and_historical_status(self):
        source=self.create(source='small300')
        response=self.request('GET','/prompt-library',headers=self.reader)
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        item=next(row for row in data['profiles'] if row['id']==source['id'])
        self.assertEqual(item['execution_profile'],'small300')
        self.assertEqual(item['execution_settings']['id'],'small300')
        for name in ('trading_system','trading_user','evolution_system','evolution_user'):
            self.assertIsInstance(item['pipeline_views'][name],list)
        self.assertEqual(data['snapshots_status'],'historical_not_activation_evidence')
        self.assertIn('no-store',response.headers.get('cache-control',''))

    def test_failed_activate_leaves_active_profile_and_saved_preferences_unchanged(self):
        source=self.create(source='small300')
        before=profiles.LIBRARY_FILE.read_bytes()
        with patch.object(profiles.os,'replace',side_effect=OSError('synthetic unavailable')):
            response=self.request('POST','/prompt-profiles/'+source['id']+'/activate',{})
        self.assertEqual(response.status_code,500)
        self.assertEqual(profiles.LIBRARY_FILE.read_bytes(),before)
        self.assertEqual(profiles.active_profile()['id'],'stable')

    def test_invalid_import_is_atomic_and_does_not_activate_partial_profile(self):
        self.create()
        before=profiles.LIBRARY_FILE.read_bytes()
        export=self.request('GET','/prompt-profiles/stable/export').json()
        export['profile']['execution_profile']='unsupported'
        response=self.request('POST','/prompt-profiles/import',{'payload':export,'name_override':'Invalid'})
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(profiles.LIBRARY_FILE.read_bytes(),before)
        self.assertEqual(profiles.active_profile()['id'],'stable')

    def test_partial_binding_update_does_not_reenable_disabled_profile(self):
        source=self.create()
        response=self.request('PUT','/prompt-profiles/'+source['id'],{'name':source['name'],'enabled':False})
        self.assertEqual(response.status_code,200)
        response=self.request('PUT','/prompt-profiles/'+source['id'],{'name':source['name'],'execution_profile':'small300'})
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['profile']['enabled'])
        self.assertEqual(self.request('POST','/prompt-profiles/'+source['id']+'/activate',{}).status_code,409)

    def test_finished_manual_review_is_visible_via_actual_gateway_status_endpoint(self):
        accepted=self.request('POST','/gateway/jobs/self_improvement/run',{'confirmation':'RUN EVOLUTION'}).json()
        store=GatewayStore(self.root/'gateway.db')
        claimed=store.claim_job_request(accepted['request_id'])
        store.finish_job(claimed['run_id'],0,'Offline review finished; no publication')
        response=self.request('GET','/gateway',headers=self.reader)
        self.assertEqual(response.status_code,200,response.text)
        finished=next(row for row in response.json()['manual_requests'] if row['request_id']==accepted['request_id'])
        self.assertEqual(finished['status'],'success')
        self.assertIsNotNone(finished['finished_at'])
        next_request=self.request('POST','/gateway/jobs/self_improvement/run',{'confirmation':'RUN EVOLUTION'}).json()
        self.assertNotEqual(next_request['request_id'],accepted['request_id'])
        self.assertFalse(next_request['deduplicated'])

    def test_live_consent_failure_is_sanitized_in_status_and_write_response(self):
        sentinel='CROSS_SYNTHETIC_CREDENTIAL_MUST_NOT_BE_RETURNED'
        expected=self.request('GET','/strategy/engine').json()
        with patch.object(engine,'_current_binding',side_effect=RuntimeError(sentinel)):
            status=self.request('GET','/strategy/engine')
            self.assertEqual(status.status_code,200)
            self.assertEqual(status.json()['status'],'unavailable')
            self.assertFalse(status.json()['authorized'])
            self.assertFalse(sentinel in status.text)
            response=self.authorize(binding=expected)
        self.assertEqual(response.status_code,409)
        self.assertFalse(sentinel in response.text,'P2: write route returns unsanitized underlying exception text')

    def test_engine_authorized_but_autotrade_paused_is_not_enabled(self):
        authorized=self.authorize()
        self.assertEqual(authorized.status_code,200,authorized.text)
        with patch.object(engine,'_autotrade_enabled',return_value=False):
            response=self.request('GET','/strategy/engine')
        self.assertTrue(response.json()['authorized'])
        self.assertFalse(response.json()['enabled'])
        self.assertEqual(response.json()['status'],'paused')

    def test_engine_live_authorization_rejects_demo_and_does_not_create_consent(self):
        self.env=SimpleNamespace(mode='demo',identity='okx:demo:cross-a',connection_id='cross-a',binding_version=1,configured=True,simulated=True)
        response=self.authorize()
        self.assertEqual(response.status_code,409)
        self.assertFalse(engine.STATE_FILE.exists())

    def test_baseline_permissions_confirmation_corruption_and_other_accounts(self):
        for headers,payload,status in ((self.reader,{'initial_capital':300,'confirmation':'UPDATE CAPITAL'},403),
                (self.superuser,{'initial_capital':300,'confirmation':'wrong'},400),
                (self.superuser,{'initial_capital':0,'confirmation':'UPDATE CAPITAL'},422)):
            self.assertEqual(self.request('PUT','/account-baseline',payload,headers=headers).status_code,status)
        self.assertFalse(account_baseline.BASELINE_FILE.exists())
        for scope,amount in (('okx:live:cross-a',300),('okx:live:cross-b',900)):
            self.env.identity=scope
            result=self.request('PUT','/account-baseline',{'initial_capital':amount,'confirmation':'UPDATE CAPITAL'})
            self.assertEqual(result.status_code,200)
        self.assertEqual(account_baseline.load_account_baseline('okx:live:cross-a')['initial_capital'],300)
        self.assertEqual(account_baseline.load_account_baseline('okx:live:cross-b')['initial_capital'],900)
        account_baseline.BASELINE_FILE.write_text('{broken')
        response=self.request('PUT','/account-baseline',{'initial_capital':500,'confirmation':'UPDATE CAPITAL'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(account_baseline.BASELINE_FILE.read_text(),'{broken')

    def test_public_snapshot_reports_effective_mode_limits_and_unknown_risk(self):
        from dashboard import app as dash
        from scripts.operational_status import execution_snapshot
        source=self.create(source='small300')
        self.request('POST','/prompt-profiles/'+source['id']+'/activate',{})
        before=execution_snapshot()
        self.assertEqual(before['mode_limits']['scalp']['max_leverage'],6)
        authorized=self.authorize()
        self.assertEqual(authorized.status_code,200)
        current=execution_snapshot()
        self.assertEqual(current['mode_limits']['scalp']['max_leverage'],20)
        self.assertEqual(current['mode_limits']['swing']['max_leverage'],5)
        cached={'account_source_id':self.env.identity,'execution_profile':current,'data_health':{},'positions_summary':{'items':[]}}
        with patch.object(dash,'DATA_DIR',str(self.root)),patch.object(dash,'CACHE_DATA',cached),patch.object(dash,'LAST_CACHE_TIME',time.time()):
            response=self.client.get('/api/all')
        self.assertEqual(response.status_code,200,response.text)
        actual=response.json()
        self.assertEqual(actual['execution_profile']['mode_limits'],current['mode_limits'])
        self.assertIsNone(actual['risk_status']['daily_blocked'])
        self.assertIsNone(actual['risk_status']['daily_drawdown'])
        self.assertEqual(actual['risk_status']['account_scope'],self.env.identity)

    def test_public_risk_status_scopes_observations_and_distinguishes_false_from_unknown(self):
        import sqlite3
        from datetime import datetime,timezone,timedelta
        from dashboard import app as dash
        now=time.time();day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).date().isoformat()
        with sqlite3.connect(self.root/'strategy_evidence.db') as db:
            db.executescript('CREATE TABLE intents(scope TEXT,state TEXT);CREATE TABLE equity_state(scope TEXT,payload TEXT);CREATE TABLE capital_pool_state(scope TEXT,payload TEXT);')
            for scope,drawdown in ((self.env.identity,.01),('okx:live:foreign',.9)):
                db.execute('INSERT INTO equity_state VALUES (?,?)',(scope,json.dumps({'day':day,'at':now,'daily_drawdown':drawdown})))
            db.execute('INSERT INTO intents VALUES (?,?)',(self.env.identity,'unknown'))
        cached={'account_source_id':self.env.identity,'data_health':{},'positions_summary':{'items':[]}}
        with patch.object(dash,'DATA_DIR',str(self.root)),patch.object(dash,'CACHE_DATA',cached),patch.object(dash,'LAST_CACHE_TIME',now),patch.object(risk_policy,'ledger_daily_drawdown',return_value={'reason':'baseline_unavailable'}):
            response=self.client.get('/api/all')
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()['risk_status']
        self.assertFalse(result['daily_blocked'])
        self.assertEqual(result['daily_drawdown'],.01)
        self.assertEqual(result['unresolved_entries'],1)
        self.assertEqual(result['status'],'observed')

    def test_valid_live_account_a_binding_cannot_authorize_current_account_b(self):
        seen=self.request('GET','/strategy/engine').json()
        self.env=SimpleNamespace(mode='live',identity='okx:live:cross-b',connection_id='cross-b',binding_version=2,configured=True,simulated=False)
        response=self.authorize(binding=seen)
        self.assertEqual(response.status_code,409)
        self.assertFalse(engine.STATE_FILE.exists())

    def test_same_scope_profile_edit_rejects_pre_edit_authorization_snapshot(self):
        profile=self.create()
        self.request('POST','/prompt-profiles/'+profile['id']+'/activate',{})
        seen=self.request('GET','/strategy/engine').json()
        changed=self.request('PUT','/prompt-profiles/'+profile['id'],{'name':profile['name'],'trading_system':'Changed after confirmation view'})
        self.assertEqual(changed.status_code,200,changed.text)
        response=self.authorize(binding=seen)
        self.assertEqual(response.status_code,409)
        self.assertFalse(engine.STATE_FILE.exists())

    def test_old_revoke_snapshot_cannot_revoke_new_authorization_record(self):
        self.assertEqual(self.authorize().status_code,200)
        old=self.request('GET','/strategy/engine').json()
        self.assertEqual(self.authorize().status_code,200)
        current=self.request('GET','/strategy/engine').json()
        self.assertNotEqual(old['record_id'],current['record_id'])
        response=self.authorize(False,binding=old)
        self.assertEqual(response.status_code,409)
        self.assertTrue(self.request('GET','/strategy/engine').json()['authorized'])

    def test_history_rollback_validation_and_delete_buttons_match_route_shapes(self):
        profile=self.create()
        identity=profile['id']
        first=self.request('GET','/prompt-profiles/'+identity+'/history',headers=self.reader)
        self.assertEqual(first.status_code,200)
        revision=first.json()['history'][0]['id']
        updated=self.request('PUT','/prompt-profiles/'+identity,{'name':'Edited','trading_system':'Another preference'})
        self.assertEqual(updated.status_code,200)
        validation=self.request('POST','/prompt-profiles/validate',{'name':'Dry run'},headers=self.reader)
        self.assertEqual(validation.status_code,200)
        self.assertIn('valid',validation.json())
        self.assertEqual(self.request('POST','/prompt-profiles/'+identity+'/rollback',{'revision_id':revision},headers=self.reader).status_code,403)
        rolled=self.request('POST','/prompt-profiles/'+identity+'/rollback',{'revision_id':revision})
        self.assertEqual(rolled.status_code,200,rolled.text)
        self.assertEqual(rolled.json()['profile']['name'],profile['name'])
        self.assertEqual(profiles.active_profile()['id'],'stable')
        self.assertEqual(self.request('POST','/prompt-profiles/'+identity+'/activate',{}).status_code,200)
        self.assertEqual(self.request('DELETE','/prompt-profiles/'+identity).status_code,409)
        self.assertEqual(self.request('POST','/prompt-profiles/stable/activate',{}).status_code,200)
        deleted=self.request('DELETE','/prompt-profiles/'+identity)
        self.assertEqual(deleted.status_code,200)
        self.assertTrue(deleted.json()['deleted'])


# Optional read-only Node UI harness. Extract/run separately; the Python runner forbids subprocesses.
UI_HARNESS_JS = r'''
const fs = await import('node:fs/promises');
const vm = await import('node:vm');
const path = await import('node:path');
const { createRequire } = await import('node:module');
const { strict: assert } = await import('node:assert');
const root = globalThis.crossReviewRoot || path.resolve('.');
const ts = createRequire(path.join(root, 'okxquant_frontend/package.json'))('typescript');
const results = [];
async function compile(relative) {
  const source = await fs.readFile(path.join(root, relative), 'utf8');
  let script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const ast = ts.createSourceFile('review.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  for (const n of [...ast.statements].reverse()) {
    if (ts.isImportDeclaration(n)) script = script.slice(0, n.pos) + script.slice(n.end);
  }
  return ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
}
const script = await compile('okxquant_frontend/src/views/admin/PromptStudioPage.vue');
function page(api, prompt = async () => null) {
  const sb = {
    useApiAction: () => ({ action: fn => fn, actionBusy: { value: false }, canManage: { value: true } }),
    useApi: () => ({ api }), useAuthStore: () => ({ isSuperadmin: true }),
    useFeedback: () => ({ value: null }), useDialogs: () => ({ confirm: async () => true, prompt }),
    ref: value => ({ value }), computed: fn => ({ get value() { return fn(); } }),
    onMounted: () => {}, onBeforeRouteLeave: () => {},
    executionBinding: p => p?.execution_profile || 'standard', executionSummary: () => 'offline limits',
    newProfilePackage: (s, n, e) => ({ ...s, profile: { ...s.profile, name: n, execution_profile: e } }),
  };
  vm.createContext(sb);
  vm.runInContext(script + '\nglobalThis.page={lib,loading,loadFailed,selectedProfileId,selectedExecution,dirty,bannerMsg,engineStatus,engineError,saveProfile,saveExecutionProfile,activateProfile,authorizeLiveEngine};', sb);
  const result = sb.page;
  result.lib.value = { active_profile_id: 'stable', profiles: [{ id: 'custom-review', name: 'Review', execution_profile: 'standard', execution_settings: { id: 'standard' }, pipeline_views: { trading_system: [], trading_user: [] } }] };
  result.selectedProfileId.value = 'custom-review'; result.loading.value = false;
  return result;
}
const failed = page(async () => { throw new Error('offline-save-failure'); });
failed.dirty.value = true; await failed.saveProfile();
assert.equal(failed.bannerMsg.value.type, 'err'); assert.equal(failed.dirty.value, true);
results.push('PASS: failed prompt PUT retains draft and shows failure');
const activation = page(async () => { throw new Error('offline-activation-failure'); });
await activation.activateProfile();
assert.equal(activation.bannerMsg.value.type, 'err'); assert.equal(activation.lib.value.active_profile_id, 'stable');
results.push('PASS: failed activation does not mark selected profile active');
const seenA = { environment: 'live', account_scope: 'okx:live:A', connection_id: 'A', binding_version: 1, profile_signature: 'A' };
const seenB = { ...seenA, account_scope: 'okx:live:B', connection_id: 'B', binding_version: 2 };
let submitted;
const consent = page(async (url, opts) => { if (opts?.method === 'POST') { submitted = JSON.parse(opts.body); return {}; } return seenB; }, async () => { consent.engineStatus.value = seenB; return 'ENABLE LIVE SCALP'; });
consent.engineStatus.value = seenA; await consent.authorizeLiveEngine(true);
assert.equal(submitted.expected_binding.account_scope, seenA.account_scope);
results.push('PASS: LIVE confirmation submits pre-dialog binding despite UI account change');
const errorConsent = page(async () => { throw new Error('offline-consent-rejected'); }, async () => 'ENABLE LIVE SCALP');
errorConsent.engineStatus.value = seenA; await errorConsent.authorizeLiveEngine(true);
assert.equal(errorConsent.bannerMsg.value.type, 'err');
results.push('PASS: rejected LIVE authorization is not reported saved');
const refresh = page(async (url, opts) => { if (opts?.method === 'PUT') return { saved: true }; throw new Error('offline-refresh-failure'); });
refresh.selectedExecution.value = 'small300'; await refresh.saveExecutionProfile();
assert.equal(refresh.loadFailed.value, true);
results.push('OBSERVED: successful binding PUT + failed reload leaves loadFailed=true and saved banner; not a failed PUT');
const telemetry = await compile('okxquant_frontend/src/components/StrategyTelemetryPanel.vue');
const store = { data: { execution_profile: { execution: { id: 'small300' }, mode_limits: { scalp: { max_leverage: 6, per_trade_equity_pct: .004 }, swing: { max_leverage: 5, per_trade_equity_pct: .005 } } }, risk_status: { daily_blocked: null, daily_drawdown: null, daily_threshold: .08, unresolved_entries: null } } };
const sb = { useDashboardStore: () => store, computed: fn => ({ get value() { return fn(); } }) };
vm.createContext(sb); vm.runInContext(telemetry + '\nglobalThis.values={leverage,riskPerTrade,dailyLossPct,dailyCircuitTriggered};', sb);
assert.equal(sb.values.dailyLossPct.value, null); assert.equal(sb.values.dailyCircuitTriggered.value, null);
assert(sb.values.leverage.value.includes('6x')); assert(sb.values.leverage.value.includes('5x'));
assert(sb.values.riskPerTrade.value.includes('0.4%')); assert(sb.values.riskPerTrade.value.includes('0.5%'));
results.push('PASS: telemetry consumes per-mode limits and preserves unknown risk as null');
const requireUi = createRequire(path.join(root, 'okxquant_frontend/package.json'));
const Vue = requireUi('vue'); const compiler = requireUi('@vue/compiler-dom');
const { renderToString } = requireUi('@vue/server-renderer');
const gateway = await fs.readFile(path.join(root, 'okxquant_frontend/src/views/admin/GatewayPage.vue'), 'utf8');
const template = gateway.match(/<template>([\s\S]*?)<\/template>\s*(?:<style|$)/)[1];
const renderBody = '(function(){' + compiler.compile(template, {mode:'function'}).code + '})()';
// Templates may contain TypeScript assertions; execute the emitted JS, not raw TS.
const render = vm.runInNewContext(ts.transpileModule(renderBody, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None,alwaysStrict:false}}).outputText, {Vue});
const request = {request_id:41278913,job_name:'self_improvement',actor:'reviewer',status:'pending'};
const data = {running:true,version:'review',stats:{},event_health:{},deliveries:[],manual_requests:[request],scheduler:{jobs:[{name:'self_improvement',schedule:'02:00',script:'scripts/self_improvement_engine.py'}],manual_requests:[request],recent_runs:[]}};
const app = Vue.createSSRApp({setup:()=>({gw:data,loading:false,loadFailed:false,actionBusy:false,bannerMsg:null,deliveredCount:0,deliveryTotal:0,overdueCount:0,statusColor:()=>'',load:()=>{},replayDelivery:()=>{}}),render});
app.config.warnHandler=()=>{};
for (const name of ['AppCard','LoadingState','Zap','RefreshCw','RotateCcw','Server','Clock','AlertTriangle']) app.component(name,{setup:(props,{slots})=>()=>Vue.h('div',slots.default?slots.default():[])});
const html = await renderToString(app);
assert(html.includes('41278913'), 'Gateway must render the manual request ID');
assert(html.includes('\u6392\u961f\u4e2d'), 'Gateway must render pending as queued, not completed');
for (const [status, label] of [['running','\u8fd0\u884c\u4e2d'],['success','\u5df2\u5b8c\u6210'],['failed','\u5931\u8d25']]) {
  request.status = status;
  const stateHtml = await renderToString(app);
  assert(stateHtml.includes('41278913') && stateHtml.includes(label), 'Gateway request lifecycle status must remain visible');
}
results.push('PASS: gateway renders manual request ID and pending/running/success/failed states');
console.log(JSON.stringify({ ui_checks: results, network_calls: 0, files_written: 0 }, null, 2));
'''
