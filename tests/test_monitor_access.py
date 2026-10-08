"""Public display/private admin boundaries, scoped data and runtime controls."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
import okxquant_backend.app as api
import dashboard.app as dashboard
from scripts.okx_runtime import OKXEnvironment
from scripts import runtime_features
from okxquant_backend import monitor_statistics


class MonitorAccessTests(unittest.TestCase):
    def test_private_data_denied_before_loading_for_both_apps(self):
        paths=['/api/ai/last-prompt','/api/ai/unknown-private-endpoint']
        for application in (api.app,dashboard.app):
            client=TestClient(application)
            try:
                with patch.object(api,'require_admin_header',side_effect=HTTPException(401,'login required')),patch.object(dashboard,'monitoring_snapshot') as read:
                    for path in paths:
                        response=client.get(path)
                        self.assertEqual(response.status_code,401,(path,response.text))
                        self.assertIn('no-store',response.headers['cache-control'])
                    read.assert_not_called()
            finally:client.close()
    def test_status_cache_bypasses_are_protected(self):
        client=TestClient(api.app)
        try:
            with patch.object(api,'require_admin_header',side_effect=HTTPException(401,'login required')):
                for path in ['/api/v1/status','/api/v1/cache/factors','/api/v1/cache/decisions','/api/v1/cache/ledger']:
                    self.assertEqual(client.get(path).status_code,401,path)
        finally:client.close()
    def test_current_session_rechecked_on_each_request_and_not_shared_cached(self):
        client=TestClient(api.app)
        try:
            with patch.object(api,'require_admin_header',side_effect=[{'id':1,'role':'admin'},HTTPException(401,'expired')]) as auth,patch.object(dashboard,'monitoring_snapshot',return_value={'account':{'total_eq':1}}):
                first=client.get('/api/ai/last-prompt',headers={'X-OKXQuant-Session':'one'})
                second=client.get('/api/ai/last-prompt',headers={'X-OKXQuant-Session':'one'})
            self.assertEqual(first.status_code,200);self.assertEqual(second.status_code,401)
            self.assertEqual(auth.call_count,2);self.assertIn('private',first.headers['cache-control'])
            self.assertIn('X-OKXQuant-Session',first.headers['vary'])
        finally:client.close()
    def test_public_display_reads_ignore_absent_or_stale_admin_session(self):
        snapshot={'account':{'total_eq':123},'logs':['inspection healthy', 'api_key=should-not-be-public']}
        for application in (api.app,dashboard.app):
            with TestClient(application) as client, patch.object(api,'require_admin_header',side_effect=AssertionError('public display must not authenticate')), patch.object(dashboard,'monitoring_snapshot',return_value=snapshot), patch.object(dashboard,'_read_ledger_rows',return_value=[]), patch.object(dashboard,'_read_ai_history_records',return_value=[]):
                for headers in ({},{'X-OKXQuant-Session':'expired'}):
                    for path in ['/api/all','/api/overview','/api/trades','/api/ai/history']:
                        response=client.get(path,headers=headers)
                        self.assertEqual(response.status_code,200,(path,response.text))
                        self.assertIn('no-store',response.headers['cache-control'])
                        self.assertNotIn('should-not-be-public',response.text)
                    for path in ['/api/trades/missing','/api/ai/history/missing']:
                        self.assertEqual(client.get(path,headers=headers).status_code,404)
                self.assertEqual(client.get('/api/all').json()['logs'][0],'inspection healthy')
        self.assertIn('should-not-be-public',snapshot['logs'][1], 'redaction must not mutate shared snapshot')

    def test_display_allowlist_does_not_authorize_mutations_or_private_admin_data(self):
        for application in (api.app,dashboard.app):
            client=TestClient(application)
            try:
                with patch.object(api,'require_admin_header',side_effect=HTTPException(401,'login required')):
                    for method in ('post','put','patch','delete'):
                        for path in ['/api/all','/api/trades','/api/ai/history']:
                            self.assertEqual(client.request(method.upper(),path,json={}).status_code,401)
            finally:client.close()
        with TestClient(api.app) as client:
            for path in ['/api/v1/admin/config','/api/v1/admin/logs','/api/v1/admin/runtime-features']:
                self.assertEqual(client.get(path).status_code,401,path)

    def test_health_remains_public(self):
        client=TestClient(api.app)
        try:
            with patch.object(api,'require_admin_header',side_effect=AssertionError('health is public')):
                self.assertEqual(client.get('/health').status_code,200)
        finally:client.close()


class RuntimeFeatureApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(runtime_features,'CONFIG_PATH',Path(self.temp.name)/'features.json');p.start();self.addCleanup(p.stop)
        self.client=TestClient(api.app);self.addCleanup(self.client.close)
    def test_requires_superadmin_before_write(self):
        with patch.object(api,'require_superadmin',side_effect=HTTPException(403,'forbidden')),patch.object(runtime_features,'save_config') as save:
            self.assertEqual(self.client.put('/api/v1/admin/runtime-features',json={'profile':'light'}).status_code,403)
            save.assert_not_called()
    def test_validated_atomic_configuration_does_not_change_trading_controls(self):
        with patch.object(api,'require_superadmin',return_value={'id':1,'username':'fixture','role':'superadmin'}),patch.object(api,'audit_record') as audit,patch.object(api,'update_env') as env:
            response=self.client.put('/api/v1/admin/runtime-features',json={'profile':'light','overrides':{'scalp_research':True}})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['features']['scalp_research'])
            self.assertFalse(response.json()['features']['factor_snapshots'])
            for body in [{'profile':'light','overrides':{'position_guard':False}},{'profile':'bad'},{'profile':'light','overrides':{'scalp_research':'false'}},{'profile':'light','overrides':{},'features':{}}]:
                self.assertEqual(self.client.put('/api/v1/admin/runtime-features',json=body).status_code,422)
            env.assert_not_called();self.assertEqual(audit.call_count,1)
    def test_feature_state_read_requires_session(self):
        with patch.object(api,'require_admin_header',side_effect=HTTPException(401,'login')):
            self.assertEqual(self.client.get('/api/v1/admin/runtime-features').status_code,401)


class ScopedMonitorDataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.env=OKXEnvironment('demo','fake','fake','fake')
        p=patch('scripts.okx_runtime.selected_environment',return_value=self.env);p.start();self.addCleanup(p.stop)
    def test_history_does_not_adopt_unowned_or_other_account_rows(self):
        path=self.root/'history.json';path.write_text(json.dumps([{'id':'good','account_scope':self.env.identity},{'id':'foreign','account_scope':'other'},{'id':'legacy'},{'id':'conflict','account_scope':self.env.identity,'environment_id':'other'}]))
        with patch.object(dashboard,'AI_HISTORY_FILE',str(path)):
            result=json.loads(asyncio.run(dashboard.get_ai_history()).body)
            self.assertEqual([x['history_id'] for x in result['items']],['good'])
            with self.assertRaises(HTTPException):asyncio.run(dashboard.get_ai_history_detail('foreign'))
    def test_prompt_requires_matching_owner_and_exact_content_digest(self):
        path=self.root/'prompt.txt';path.write_text('fixture prompt')
        meta=self.root/'ai_brain_last_prompt_meta.json'
        with patch.object(dashboard,'AI_LAST_PROMPT_FILE',str(path)):
            self.assertEqual(json.loads(asyncio.run(dashboard.get_ai_last_prompt()).body)['prompt'],'')
            meta.write_text(json.dumps({'account_scope':self.env.identity,'sha256':hashlib.sha256(b'fixture prompt').hexdigest(),'generated_at':1}))
            self.assertEqual(json.loads(asyncio.run(dashboard.get_ai_last_prompt()).body)['prompt'],'fixture prompt')
            path.write_text('another account prompt')
            self.assertEqual(json.loads(asyncio.run(dashboard.get_ai_last_prompt()).body)['prompt'],'')
    def test_list_and_statistics_share_one_parse_and_corruption_is_not_empty(self):
        path=self.root/'ledger.json';path.write_text(json.dumps([{'id':'a','environment_id':self.env.identity,'status':'holding','open_time':'2026-10-08 01:00:00'}]))
        from scripts import json_projection_cache
        with patch.object(dashboard,'LEDGER_JSON_FILE',str(path)),patch.object(json_projection_cache.json,'load',wraps=json.load) as parse:
            dashboard._read_ledger_rows()
            monitor_statistics.read_periods(path,self.env.identity)
            dashboard._read_ledger_rows()
            self.assertEqual(parse.call_count,1)
            path.write_text('{}')
            with self.assertRaises(ValueError):dashboard._read_ledger_rows()

    def test_statistics_cache_invalidates_on_correction_scope_and_midnight(self):
        from datetime import datetime,timezone,timedelta
        at=datetime(2026,10,8,23,59,tzinfo=timezone(timedelta(hours=8))).timestamp()
        path=self.root/'ledger.json';rows=[{'id':'x','environment_id':self.env.identity,'status':'closed','horizon':'scalp','open_time':'2026-10-08 10:00:00','close_time':'2026-10-08 12:00:00','net_pnl':1,'fee':-.1,'gross_pnl':1.1}]
        path.write_text(json.dumps(rows))
        a=monitor_statistics.read_periods(path,self.env.identity,now=at)
        self.assertEqual(a['periods']['today']['scalp']['net_pnl'],1)
        self.assertEqual(monitor_statistics.read_periods(path,'other',now=at)['periods']['all']['scalp']['opened'],0)
        self.assertEqual(monitor_statistics.read_periods(path,self.env.identity,now=at+120)['periods']['today']['scalp']['closed'],0)
        rows[0]['net_pnl']=2;replacement=self.root/'replacement.json';replacement.write_text(json.dumps(rows));replacement.replace(path)
        self.assertEqual(monitor_statistics.read_periods(path,self.env.identity,now=at)['periods']['today']['scalp']['net_pnl'],2)
        path.write_text('broken')
        with self.assertRaises(ValueError):monitor_statistics.read_periods(path,self.env.identity,now=at)


class IntegratedStatisticsTests(unittest.TestCase):
    def test_trade_list_and_detail_use_same_latest_correction(self):
        env=OKXEnvironment('demo','fake','fake','fake')
        rows=[{'id':'x','environment_id':env.identity,'net_pnl':2},{'id':'x','environment_id':env.identity,'net_pnl':5}]
        with patch('scripts.okx_runtime.selected_environment',return_value=env),patch.object(dashboard,'_read_ledger_rows',return_value=rows):
            listing=json.loads(asyncio.run(dashboard.get_trades()).body)
            detail=json.loads(asyncio.run(dashboard.get_trade_detail('x')).body)
        self.assertEqual(listing['total'],1);self.assertEqual(listing['items'][0]['net_pnl'],5);self.assertEqual(detail['net_pnl'],5)
    def test_private_cache_never_copies_foreign_allocation_or_funnel(self):
        env=OKXEnvironment('demo','fake','fake','fake')
        client=TestClient(api.app)
        try:
            def read(name,default):
                return {'scope':'foreign','funnel':{'private':'foreign'},'allocation':{'private':'foreign'}} if name=='horizon_stats.json' else []
            with patch.object(api,'require_admin_header',return_value={'id':1}),patch('scripts.okx_runtime.selected_environment',return_value=env),patch.object(api,'read_json',side_effect=read):
                result=client.get('/api/v1/cache/horizon-stats')
            self.assertEqual(result.status_code,200,result.text)
            self.assertNotIn('funnel',result.json());self.assertNotIn('allocation',result.json())
        finally:client.close()
    def test_switch_during_monitor_assembly_never_mixes_accounts(self):
        a=OKXEnvironment('demo','account-a','fake','fake');b=OKXEnvironment('demo','account-b','fake','fake')
        cached={'account_source_id':a.identity,'account':{'total_eq':123},'data_health':{}}
        with patch('scripts.okx_runtime.selected_environment',side_effect=[a,b]),patch.object(dashboard,'CACHE_DATA',cached),patch.object(dashboard,'LAST_CACHE_TIME',dashboard.time.time()),patch.object(dashboard,'request_cache_refresh'),patch.object(dashboard,'_read_horizon_stats',return_value={'scope':a.identity}) as stats,patch('scripts.operational_status.risk_snapshot',return_value={}),patch('scripts.instrument_pool.load_instruments',return_value=[]),patch('scripts.instrument_support.pool_support',return_value={}):
            result=dashboard.monitoring_snapshot()
        stats.assert_called_once_with(a.identity)
        self.assertEqual(result['account_source_id'],b.identity);self.assertEqual(result['account'],{})
        self.assertEqual(result['horizon_stats'],{});self.assertTrue(result['initializing'])

    def test_unknown_statistics_stay_unknown_in_overview_payload(self):
        from scripts.horizon_stats import strategy_periods
        now=1791446400
        data=strategy_periods([{'id':'x','environment_id':'scope','status':'closed','open_time':'2026-10-08 00:00:00','close_time':'2026-10-08 01:00:00','net_pnl':None}],scope='scope',now=now)
        result=dashboard._today_from_periods(data,10)
        self.assertIsNone(result['net_realized']);self.assertIsNone(result['total_pnl']);self.assertEqual(result['unsettled_amount_rows'],1)
        self.assertEqual(result['outcome_trades'],0)
    def test_stale_snapshot_refreshes_periods_independently_of_account_data(self):
        env=OKXEnvironment('demo','fake','fake','fake')
        snapshot={'account_source_id':env.identity,'account':{'total_eq':1},'horizon_stats':{'periods':{'today':{'yesterday':True}}},'data_health':{}}
        with patch('scripts.okx_runtime.selected_environment',return_value=env),patch.object(dashboard,'CACHE_DATA',snapshot),patch.object(dashboard,'LAST_CACHE_TIME',0),patch.object(dashboard,'request_cache_refresh'),patch.object(dashboard,'_read_horizon_stats',return_value={'periods':{}}) as stats:
            result=dashboard.monitoring_snapshot()
        stats.assert_called_once();self.assertEqual(result['horizon_stats']['periods'],{});self.assertIsNone(result['today_stats']['net_realized'])


class MonitorIdleTests(unittest.TestCase):
    def test_unvisited_empty_monitor_does_not_poll_private_apis(self):
        with patch.object(dashboard,'CACHE_DATA',{}),patch.object(dashboard,'_LAST_MONITOR_REQUEST',float('-inf')),patch.object(dashboard,'_LAST_MONITOR_ATTEMPT',float('-inf')):
            self.assertFalse(dashboard._monitor_background_due(wall=1000,monotonic=1000))
    def test_active_and_idle_refresh_windows_are_distinct(self):
        with patch.object(dashboard,'CACHE_DATA',{'account':{}}),patch.object(dashboard,'LAST_CACHE_TIME',990),patch.object(dashboard,'_LAST_MONITOR_ATTEMPT',990),patch.object(dashboard,'_LAST_MONITOR_REQUEST',995):
            self.assertTrue(dashboard._monitor_background_due(wall=1000,monotonic=1000))
            with patch.object(dashboard,'_LAST_MONITOR_REQUEST',900):
                self.assertFalse(dashboard._monitor_background_due(wall=1000,monotonic=1000))
                self.assertTrue(dashboard._monitor_background_due(wall=1300,monotonic=1300))
    def test_failed_background_attempt_is_not_repeated_every_second(self):
        with patch.object(dashboard,'CACHE_DATA',{}),patch.object(dashboard,'_LAST_MONITOR_REQUEST',995),patch.object(dashboard,'_LAST_MONITOR_ATTEMPT',999):
            self.assertFalse(dashboard._monitor_background_due(wall=1000,monotonic=1000))
            self.assertTrue(dashboard._monitor_background_due(wall=1005,monotonic=1005))

if __name__=='__main__':unittest.main()
