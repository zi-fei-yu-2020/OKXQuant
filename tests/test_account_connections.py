import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from okxquant_backend import account_connections as center, connection_transport as transport
from okxquant_gateway import secrets as vault
from scripts import ledger_monitor, strategy_evidence, trade_lock, okx_runtime, news_connection


class AccountFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=time.time()
        fields=[(center,'DATA',self.root),(center,'STORE',self.root/'connections.enc'),
                (vault,'KEY_FILE',self.root/'key'),(vault,'STORE_FILE',self.root/'legacy.enc'),
                (transport,'OAUTH_ROOT',self.root/'oauth'),(transport,'AUTH_BINARY',self.root/'okx-auth'),
                (trade_lock,'PATH',self.root/'writer.lock'),(ledger_monitor,'DATA',self.root),
                (strategy_evidence,'DB_PATH',self.root/'evidence.db')]
        for module,name,value in fields:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        def legacy(values=None,mode=None):
            mode=mode or 'demo'
            return okx_runtime.OKXEnvironment(mode,'OLD_KEY' if mode=='demo' else '', 'OLD_SECRET' if mode=='demo' else '', 'OLD_PASS' if mode=='demo' else '')
        p=patch.object(okx_runtime,'legacy_environment',side_effect=legacy);p.start();self.addCleanup(p.stop)
        transport._CACHE.clear();self.addCleanup(transport._CACHE.clear)

    def new(self,auth='api_key',mode='demo',uid='account-A',news=False):
        identity=center.create('测试连接',auth,mode,'global',{'api_key':'PRIVATE_KEY_DO_NOT_LOG','secret_key':'PRIVATE_SECRET_DO_NOT_LOG','passphrase':'PRIVATE_PASS_DO_NOT_LOG'})
        state=center.load();c=state['connections'][identity];c['status']='identity_verified'
        c['capabilities'][mode]={'account_uid':uid,'canonical_scope':center._uid_scope(mode,uid),'checked_at':self.now,'read_ready':True,'news_ready':news,'write_ready':False}
        center.save(state,'fixture')
        return identity

    def flat_reader(self,c,purpose,mode,method,path,params=None,**kwargs):
        self.assertEqual(method,'GET')
        return [{'uid':'account-A'}] if path.endswith('/config') else []


class AccountLifecycleTests(AccountFixture):
    def test_read_does_not_migrate_or_create_store(self):
        self.assertFalse(center.public_status()['managed'])
        self.assertFalse(center.STORE.exists())
        self.assertIsNone(center.resolve_environment())

    def test_credentials_are_encrypted_and_never_returned(self):
        self.new()
        raw=center.STORE.read_bytes();public=json.dumps(center.public_status())
        for value in ('PRIVATE_KEY_DO_NOT_LOG','PRIVATE_SECRET_DO_NOT_LOG','PRIVATE_PASS_DO_NOT_LOG'):
            self.assertNotIn(value.encode(),raw);self.assertNotIn(value,public)

    def test_draft_does_not_switch_current_trading_account(self):
        self.new(auth='oauth',mode='live')
        self.assertFalse(center.load()['managed'])
        self.assertEqual(okx_runtime.selected_environment().api_key,'OLD_KEY')

    def test_position_blocks_binding_without_changing_state(self):
        identity=self.new();before=center.load()
        with patch.object(transport,'request',return_value=[{'pos':'1'}]):
            with self.assertRaisesRegex(center.AccountChangeError,'持仓'):center.bind('demo',identity,'BIND DEMO')
        self.assertEqual(center.load(),before)

    def test_nonfinite_position_cannot_be_treated_as_flat(self):
        identity=self.new()
        with patch.object(transport,'request',return_value=[{'pos':'NaN'}]):
            with self.assertRaisesRegex(center.AccountChangeError,'不可核验'):center.bind('demo',identity,'BIND DEMO')

    def test_advanced_pending_orders_block_unbinding(self):
        identity=self.new()
        with patch.object(transport,'request',side_effect=self.flat_reader):center.bind('demo',identity,'BIND DEMO')
        def pending(c,purpose,mode,method,path,params=None,**kwargs):
            return [{'algoId':'pending'}] if (params or {}).get('ordType')=='twap' else []
        with patch.object(transport,'request',side_effect=pending):
            with self.assertRaisesRegex(center.AccountChangeError,'挂单'):center.unbind('demo','UNBIND DEMO')
        self.assertEqual(center.load()['bindings']['demo'],identity)

    def test_inventory_failure_never_switches_to_another_account(self):
        identity=self.new()
        with patch.object(transport,'request',side_effect=transport.ConnectionError('http_429')) as get:
            with self.assertRaises(transport.ConnectionError):center.bind('demo',identity,'BIND DEMO')
        self.assertEqual(get.call_count,1);self.assertFalse(center.load()['managed'])

    def test_same_account_key_rotation_preserves_risk_scope_and_ledger(self):
        identity=self.new();(self.root/'trading_ledger.json').write_text('[1]')
        legacy_scope=okx_runtime.legacy_environment().identity
        with patch.object(transport,'request',side_effect=self.flat_reader):center.bind('demo',identity,'BIND DEMO')
        first=center.resolve_environment();self.assertEqual(first.identity,legacy_scope)
        self.assertTrue((self.root/'trading_ledger.json').exists())
        next_id=self.new()
        with patch.object(transport,'request',side_effect=self.flat_reader):center.bind('demo',next_id,'BIND DEMO')
        current=center.resolve_environment()
        self.assertEqual(current.identity,legacy_scope);self.assertNotEqual(current.binding_version,first.binding_version)
        with self.assertRaises(center.AccountChangeError):center.assert_current(first)

    def test_different_account_gets_isolated_scope_and_archived_financial_state(self):
        identity=self.new(uid='account-B');(self.root/'trading_ledger.json').write_text('[1]')
        with patch.object(transport,'request',side_effect=self.flat_reader):center.bind('demo',identity,'BIND DEMO')
        self.assertEqual(center.resolve_environment().identity,center._uid_scope('demo','account-B'))
        self.assertFalse((self.root/'trading_ledger.json').exists())
        self.assertEqual(len(list((self.root/'account-switch-archive').glob('*/trading_ledger.json'))),1)

    def test_unbound_mode_does_not_fall_back_to_old_key_or_global_oauth(self):
        identity=self.new()
        with patch.object(transport,'request',side_effect=self.flat_reader):
            center.bind('demo',identity,'BIND DEMO');center.unbind('demo','UNBIND DEMO')
        env=okx_runtime.selected_environment()
        self.assertFalse(env.configured)
        with self.assertRaises(center.AccountChangeError):center.assert_current(env)
        with self.assertRaises(RuntimeError):env.cli_prefix()
        with self.assertRaises(RuntimeError):env.cli_env({'OKX_API_KEY':'SHOULD_NOT_USE'})
        self.assertIsInstance(center.public_status(),dict)

    def test_news_binding_never_changes_trading_environment_or_revision(self):
        key=self.new()
        with patch.object(transport,'request',side_effect=self.flat_reader):center.bind('demo',key,'BIND DEMO')
        first=center.resolve_environment();news=self.new(auth='oauth',mode='live',news=True)
        center.bind('news',news,'BIND NEWS')
        self.assertEqual(center.resolve_environment(),first)
        self.assertEqual(center.news_connection()['id'],news)
        center.unbind('news','UNBIND NEWS');self.assertEqual(center.resolve_environment(),first)

    def test_read_success_does_not_authorize_oauth_trading(self):
        identity=self.new(auth='oauth')
        with self.assertRaisesRegex(center.AccountChangeError,'尚未验证'):center.bind('demo',identity,'BIND DEMO')

    def test_wrong_confirmation_does_not_touch_binding(self):
        identity=self.new()
        with self.assertRaises(center.AccountChangeError):center.bind('live',identity,'BIND DEMO')
        self.assertFalse(center.load()['managed'])

    def test_unresolved_intent_blocks_account_change(self):
        identity=self.new()
        strategy_evidence.begin_intent('old','decision','TEST-USDT-SWAP',{})
        with patch.object(transport,'request') as request:
            with self.assertRaisesRegex(center.AccountChangeError,'未确认'):center.bind('demo',identity,'BIND DEMO')
        request.assert_not_called()

    def test_stale_capability_cannot_bind(self):
        identity=self.new();state=center.load();state['connections'][identity]['capabilities']['demo']['checked_at']=0;center.save(state,'fixture')
        with self.assertRaisesRegex(center.AccountChangeError,'核验'):center.bind('demo',identity,'BIND DEMO')

    def test_encrypted_store_corruption_fails_closed(self):
        self.new();center.STORE.write_bytes(b'broken')
        with self.assertRaises(center.AccountChangeError):okx_runtime.selected_environment()

    def test_probe_checks_identity_and_does_not_claim_write_success(self):
        identity=self.new(auth='oauth',mode='live')
        with patch.object(transport,'request',side_effect=self.flat_reader) as request:
            cap=center.probe(identity,'live')
        self.assertTrue(cap['read_ready']);self.assertTrue(cap['news_ready']);self.assertFalse(cap['write_ready'])
        self.assertTrue(all(c.args[3]=='GET' for c in request.call_args_list))

    def test_changed_identity_is_not_silently_rebound(self):
        identity=self.new(auth='oauth')
        with patch.object(transport,'request',return_value=[{'uid':'other'}]):cap=center.probe(identity,'demo')
        self.assertEqual(cap['account_uid'],'');self.assertFalse(cap['read_ready'])

    def test_oauth_authorization_rejects_untrusted_url(self):
        identity=self.new(auth='oauth')
        with patch.object(transport,'auth_command',return_value={'verificationUri':'https://evil.example/','userCode':'ABCD','expiresIn':300}):
            with self.assertRaisesRegex(center.AccountChangeError,'授权地址'):center.oauth_start(identity)

    def test_oauth_start_returns_only_public_device_fields(self):
        identity=self.new(auth='oauth')
        with patch.object(transport,'auth_command',return_value={'verificationUri':'https://www.okx.com/device','userCode':'ABCD','expiresIn':300,'token':'DO_NOT_RETURN'}):
            result=center.oauth_start(identity)
        self.assertNotIn('DO_NOT_RETURN',json.dumps(result));self.assertNotIn('DO_NOT_RETURN',json.dumps(center.load()))


class TransportTests(AccountFixture):
    def test_protection_probe_uses_shared_monitor_admission(self):
        from contextlib import nullcontext
        from scripts import algo_reader
        c={'auth_type':'api_key','mode':'demo','site':'global','credentials':{'api_key':'key','secret_key':'secret','passphrase':'pass'}}
        with patch.object(algo_reader,'_turn',return_value=nullcontext()) as turn,patch.object(algo_reader,'_reserve') as reserve,patch.object(algo_reader,'_check'),patch.object(transport,'_http',return_value=[]) as http:
            transport.request(c,'probe','demo','GET','/api/v5/trade/orders-algo-pending',{'ordType':'oco'})
        self.assertEqual(turn.call_args.args[1],'monitor');self.assertEqual(reserve.call_args.args[1],'monitor');http.assert_called_once()

    def test_failed_protection_admission_is_not_retried(self):
        from scripts import algo_reader
        c={'auth_type':'api_key','mode':'demo','site':'global','credentials':{'api_key':'key','secret_key':'secret','passphrase':'pass'}}
        with patch.object(algo_reader,'_turn',side_effect=algo_reader.AlgoReadError('busy')) as turn,patch.object(transport,'_http') as http:
            with self.assertRaisesRegex(transport.ConnectionError,'admission_unavailable'):transport.request(c,'probe','demo','GET','/api/v5/trade/orders-algo-pending',{'ordType':'oco'})
        turn.assert_called_once();http.assert_not_called()

    def oauth(self):
        return {'id':'a'*32,'generation':1,'auth_type':'oauth','site':'global','capabilities':{'live':{'account_uid':'account-A'},'demo':{'account_uid':'account-A'}}}

    def test_child_environment_excludes_key_credentials_and_keyring_session(self):
        with patch.dict(os.environ,{'OKX_API_KEY':'bad','OKX_SECRET_KEY':'bad','OKX_DEMO':'1','DBUS_SESSION_BUS_ADDRESS':'bad','OKX_PROFILE':'bad'}):
            env=transport.isolated_env('a'*32)
        for key in ('OKX_API_KEY','OKX_SECRET_KEY','DBUS_SESSION_BUS_ADDRESS','OKX_PROFILE'):self.assertNotIn(key,env)
        self.assertEqual(env['OKX_DEMO'],'0');self.assertEqual(env['HOME'],str(self.root/'oauth'/('a'*32)))
        with self.assertRaises(transport.ConnectionError):transport.oauth_home('../escape')

    def test_native_token_uses_fd3_protocol_without_printing_secret(self):
        transport.AUTH_BINARY.touch()
        process=Mock();process.returncode=0;process.poll.return_value=0
        def spawn(command,**kwargs):
            os.write(int(command[-1]),b'TEST_TOKEN')
            self.assertEqual(kwargs['stdout'],transport.subprocess.DEVNULL)
            return process
        with patch.object(transport.subprocess,'Popen',side_effect=spawn):self.assertEqual(transport.native_token('a'*32),'TEST_TOKEN')

    def test_oauth_news_proves_uid_with_same_bearer_and_never_sends_demo_header(self):
        with patch.object(transport,'native_token',return_value='TOKEN'),patch.object(transport,'_http',side_effect=[[{'uid':'account-A'}],[{'details':[]}]]) as http:
            transport.request(self.oauth(),'news','live','GET','/api/v5/orbit/news-search',{})
        for call in http.call_args_list:
            headers=call.args[3];self.assertEqual(headers['Authorization'],'Bearer TOKEN');self.assertNotIn('OK-ACCESS-KEY',headers);self.assertNotIn('x-simulated-trading',headers)

    def test_wrong_account_stops_before_news_or_trade_read(self):
        with patch.object(transport,'native_token',return_value='WRONG'),patch.object(transport,'_http',return_value=[{'uid':'other'}]) as http:
            with self.assertRaisesRegex(transport.ConnectionError,'account_changed'):transport.request(self.oauth(),'news','live','GET','/api/v5/orbit/news-search',{})
        self.assertEqual(http.call_count,1)

    def test_news_cannot_call_trade_paths_or_demo_mode(self):
        with patch.object(transport,'_http') as http:
            for mode,method,path in [('demo','GET','/api/v5/orbit/news-search'),('live','POST','/api/v5/trade/order'),('live','GET','/api/v5/account/balance')]:
                with self.assertRaises(transport.ConnectionError):transport.request(self.oauth(),'news',mode,method,path,{})
        http.assert_not_called()

    def test_unverified_oauth_write_is_blocked_before_token_fetch(self):
        with patch.object(transport,'native_token') as token:
            with self.assertRaisesRegex(transport.ConnectionError,'not_validated'):transport.request(self.oauth(),'trade','demo','POST','/api/v5/trade/order',{})
        token.assert_not_called()

    def test_oauth_demo_reads_keep_simulated_header(self):
        with patch.object(transport,'native_token',return_value='TOKEN'),patch.object(transport,'_http',side_effect=[[{'uid':'account-A'}],[]]) as http:
            transport.request(self.oauth(),'probe','demo','GET','/api/v5/account/balance',{})
        self.assertTrue(all(call.args[3]['x-simulated-trading']=='1' for call in http.call_args_list))

    def test_redirect_never_forwards_auth_headers(self):
        with self.assertRaisesRegex(transport.ConnectionError,'redirect_forbidden'):
            transport.NoRedirect().redirect_request(None,None,302,'',{},'https://evil.example')

    def test_demo_key_is_not_reinterpreted_as_a_live_news_key(self):
        c={'auth_type':'api_key','mode':'demo','site':'global','credentials':{'api_key':'key','secret_key':'secret','passphrase':'pass'}}
        with patch.object(transport,'_http') as http:
            with self.assertRaisesRegex(transport.ConnectionError,'environment_mismatch'):transport.request(c,'news','live','GET','/api/v5/orbit/news-search',{})
        http.assert_not_called()


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.now=10000
        self.c={'id':'a'*32,'auth_type':'oauth'}
    def reader(self,c,purpose,mode,method,path,params,**kwargs):
        self.assertEqual((purpose,mode,method),('news','live','GET'))
        if path.endswith('news-search'):
            return [{'details':[{'id':'1','cTime':str(self.now*1000),'title':'正常新闻','summary':'摘要'}]}]
        return [{'details':[{'ccy':'BTC','mentionCnt':10,'sentiment':{'bullishRatio':.6,'bearishRatio':.2,'bullishCnt':6,'bearishCnt':2,'neutralCnt':2,'label':'bullish'}}]}]
    def test_success_and_missing_coin_are_explicit(self):
        result=news_connection.collect(['BTC','WLD'],now=self.now,reader=self.reader,connection=self.c)
        self.assertEqual(result['last_success_at'],self.now)
        self.assertEqual(result['connection_status'],'fresh')
        self.assertFalse(result['coins_sentiment']['WLD']['available'])
        self.assertEqual(result['coins_sentiment']['WLD']['bullish_ratio'],'--')
        self.assertIsNone(result['coins_sentiment']['WLD']['sentiment_factor_score'])

    def test_failure_preserves_last_success_time_not_new_attempt_time(self):
        first=news_connection.collect(['BTC'],now=self.now,reader=self.reader,connection=self.c)
        with patch('scripts.news_connection.connection_transport.request',side_effect=transport.ConnectionError('http_429')):
            result=news_connection.collect(['BTC'],first,now=self.now+60,connection=self.c)
        self.assertEqual(result['last_success_at'],self.now);self.assertEqual(result['last_attempt_at'],self.now+60)
        self.assertTrue(result['stale_sections']);self.assertIsNone(news_connection.for_strategy(result,now=self.now+60))

    def test_partial_success_does_not_reuse_stale_sentiment_as_current(self):
        first=news_connection.collect(['BTC'],now=self.now,reader=self.reader,connection=self.c)
        def partial(*args,**kwargs):
            if args[4].endswith('sentiment-query'):raise transport.ConnectionError('http_503')
            return self.reader(*args,**kwargs)
        result=news_connection.collect(['BTC'],first,now=self.now+60,reader=partial,connection=self.c)
        self.assertEqual(result['connection_status'],'partial');self.assertIn('UNKNOWN',result['macro_sentiment'])

    def test_changed_connection_cannot_inherit_old_entitled_cache(self):
        old=news_connection.collect(['BTC'],now=self.now,reader=self.reader,connection=self.c)
        with patch('scripts.news_connection.connection_transport.request',side_effect=transport.ConnectionError('http_401')):
            result=news_connection.collect(['BTC'],old,now=self.now+60,connection={'id':'b'*32,'auth_type':'oauth'})
        self.assertFalse(result['latest_news']);self.assertIsNone(result['last_success_at'])

    def test_missing_news_binding_never_borrows_trading_key(self):
        with patch.object(center,'news_connection',side_effect=center.AccountChangeError('unbound')),patch.object(transport,'request') as reader:
            result=news_connection.collect(['BTC'],now=self.now)
        reader.assert_not_called();self.assertEqual(result['connection_status'],'unconfigured')

    def test_duplicate_refresh_is_bounded_without_retry(self):
        with patch('scripts.news_connection.connection_transport.request',side_effect=self.reader) as reader:
            first=news_connection.collect(['BTC'],now=self.now,connection=self.c)
            news_connection.collect(['BTC'],first,now=self.now+1,connection=self.c)
        self.assertEqual(reader.call_count,3)

    def test_old_schema_and_old_data_cannot_enter_model_context(self):
        self.assertIsNone(news_connection.for_strategy({'updated_at':'just now','latest_news':[{}]},now=self.now))
        result=news_connection.collect(['BTC'],now=self.now,reader=self.reader,connection=self.c)
        self.assertIsNone(news_connection.for_strategy(result,now=self.now+1201))


class ApiTests(AccountFixture):
    def test_account_routes_require_superadmin_and_never_return_keys(self):
        from fastapi import FastAPI,HTTPException
        from fastapi.testclient import TestClient
        from okxquant_backend.account_routes import install
        app=FastAPI()
        def require(session):
            if session!='admin':raise HTTPException(status_code=403,detail='forbidden')
            return {'username':'admin'}
        install(app,require,lambda *args:None)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/v1/admin/accounts').status_code,403)
            result=client.post('/api/v1/admin/accounts/connections',headers={'X-OKXQuant-Session':'admin'},json={
                'label':'demo','auth_type':'api_key','mode':'demo','site':'global','api_key':'SECRET_API','secret_key':'SECRET_PRIVATE','passphrase':'SECRET_PASS',
                'capabilities':{'write_ready':True}})
            self.assertEqual(result.status_code,200)
            for secret in ('SECRET_API','SECRET_PRIVATE','SECRET_PASS'):self.assertNotIn(secret,result.text)
            identity=result.json()['connection_id'];self.assertFalse(center.load()['connections'][identity]['capabilities'])


class AccountEdgeTests(AccountFixture):
    def test_unversioned_legacy_context_is_rejected_after_migration(self):
        identity=self.new();old=okx_runtime.legacy_environment()
        with patch.object(transport,'request',side_effect=self.flat_reader):center.bind('demo',identity,'BIND DEMO')
        with self.assertRaisesRegex(center.AccountChangeError,'旧连接'):center.assert_current(old)

    def test_reauthorization_invalidates_previous_capability(self):
        identity=self.new(auth='oauth',mode='live',news=True)
        with patch.object(transport,'auth_command',return_value={'verificationUri':'https://www.okx.com/device','userCode':'CODE','expiresIn':900}):center.oauth_start(identity)
        c=center.load()['connections'][identity]
        self.assertEqual(c['generation'],2);self.assertEqual(c['capabilities'],{})
        with self.assertRaises(center.AccountChangeError):center.bind('news',identity,'BIND NEWS')

    def test_pending_authorization_blocks_deletion_for_actual_lifetime(self):
        identity=self.new(auth='oauth');state=center.load();c=state['connections'][identity]
        c.update(status='authorization_pending',authorization_started_at=self.now-700,authorization_expires_at=self.now+1000)
        center.save(state,'fixture')
        with self.assertRaisesRegex(center.AccountChangeError,'仍在进行'):center.delete(identity,'DELETE CONNECTION')

    def test_symlinked_auth_directory_is_not_recursively_deleted(self):
        identity=self.new(auth='oauth');outside=self.root/'outside';outside.mkdir();(outside/'keep').write_text('keep')
        home=transport.oauth_home(identity);home.parent.mkdir();home.symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(center.AccountChangeError,'归属'):center.delete(identity,'DELETE CONNECTION')
        self.assertTrue((outside/'keep').exists());self.assertIn(identity,center.load()['connections'])

    def test_generic_key_is_not_imported_as_both_demo_and_live(self):
        def legacy(values=None,mode=None):return okx_runtime.OKXEnvironment(mode or 'demo','KEY','SECRET','PASS',source='legacy-or-oauth')
        with patch.object(okx_runtime,'legacy_environment',side_effect=legacy):
            self.assertFalse(center.public_status()['legacy_key_configured']['live'])
            self.assertIsNone(center._legacy_connection('live'))
            with self.assertRaises(center.AccountChangeError):center.import_legacy('live')

    def test_deletion_does_not_claim_remote_revocation(self):
        identity=self.new(auth='oauth');home=transport.oauth_home(identity);home.mkdir(parents=True);(home/'token-placeholder').write_text('fake')
        result=center.delete(identity,'DELETE CONNECTION')
        self.assertFalse(result['remote_revoked']);self.assertFalse(home.exists())


class NewsEdgeTests(unittest.TestCase):
    def test_invalid_news_time_is_not_a_successful_refresh(self):
        def reader(c,p,m,method,path,params,**kwargs):return [{'details':[{'id':'1','title':'bad','cTime':'NaN'}]}]
        result=news_connection.collect(['BTC'],now=10000,reader=reader,connection={'id':'a'*32,'auth_type':'oauth'})
        self.assertIsNone(result['last_success_at']);self.assertEqual(result['connection_status'],'unavailable')

    def test_non_http_source_links_are_removed(self):
        rows=news_connection.articles([{'details':[{'id':'1','title':'news','cTime':'1000','sourceUrl':'javascript:alert(1)'}]}])
        self.assertEqual(rows[0]['sourceUrl'],'')


class AccountDisplayTests(AccountFixture):
    def test_saving_same_binding_does_not_reset_decisions_or_risk_context(self):
        identity=self.new()
        with patch.object(transport,'request',side_effect=self.flat_reader):center.bind('demo',identity,'BIND DEMO')
        before=center.load();env=center.resolve_environment()
        with patch.object(transport,'request') as request,patch.object(center,'_archive_runtime') as archive:
            result=center.bind('demo',identity,'BIND DEMO')
        self.assertTrue(result['no_change']);self.assertEqual(center.load(),before);self.assertEqual(center.resolve_environment(),env)
        request.assert_not_called();archive.assert_not_called()

    def test_runtime_credential_aliases_and_mode_match_managed_binding(self):
        identity=self.new()
        with patch.object(transport,'request',side_effect=self.flat_reader):center.bind('demo',identity,'BIND DEMO')
        result=center.runtime_credentials(True)
        self.assertTrue(result['okx']);self.assertTrue(result['okx_configured']);self.assertTrue(result['simulated_trading'])
        self.assertEqual(result['okx_environment'],'demo');self.assertEqual(result['credential_source'],'api_key')
        with patch.object(transport,'request',side_effect=self.flat_reader):center.unbind('demo','UNBIND DEMO')
        result=center.runtime_credentials(True)
        self.assertFalse(result['okx_configured']);self.assertEqual(result['connection_status'],'unbound')
        self.assertTrue(result['simulated_trading'])

    def test_masked_display_never_exposes_secret_or_passphrase_fragments(self):
        identity=self.new()
        public=center.public_status();row=next(c for c in public['connections'] if c['id']==identity)
        self.assertEqual(row['credentials_display'],{'api_key':'PRIV********_LOG','secret_key_saved':True,'passphrase_saved':True})
        self.assertEqual(public['legacy_connections']['demo']['credentials_display']['api_key'],'********')
        for secret in ('PRIVATE_KEY_DO_NOT_LOG','PRIVATE_SECRET_DO_NOT_LOG','PRIVATE_PASS_DO_NOT_LOG','OLD_SECRET','OLD_PASS'):
            self.assertNotIn(secret,json.dumps(public))

    def test_old_auto_generated_pending_label_is_presentation_only(self):
        identity=self.new();state=center.load();state['connections'][identity]['label']='原有demo Key（待核验）';center.save(state,'fixture')
        row=next(c for c in center.public_status()['connections'] if c['id']==identity)
        self.assertEqual(row['display_label'],'原有模拟盘 Key')
        self.assertEqual(center.load()['connections'][identity]['label'],'原有demo Key（待核验）')
        self.assertEqual(center.display_label('自定义账户名称'),'自定义账户名称')

    def test_news_interval_comes_from_scheduler_definition(self):
        from okxquant_gateway.scheduler import JOBS
        self.assertEqual(center.public_status()['news_interval_seconds'],next(j.interval_seconds for j in JOBS if j.name=='news'))
        self.assertEqual(center.public_status()['news_interval_seconds'],600)

    def test_manual_close_setting_changes_only_permission_not_bindings_or_trades(self):
        from okxquant_backend import settings_store
        env=self.root/'settings.env';env.write_text('OKXQUANT_OKX_ENV=demo\nUNRELATED=keep\n')
        before=center.load()
        with patch.object(settings_store,'ENV_FILE',env),patch.object(settings_store,'refresh_settings'),patch.dict(os.environ,{},clear=False),patch.object(transport,'request') as request:
            result=center.set_manual_close(True,'ENABLE MANUAL CLOSE')
            self.assertTrue(result['manual_close_enabled'])
            self.assertIn('OKXQUANT_OKX_ENV=demo',env.read_text());self.assertIn('UNRELATED=keep',env.read_text())
            self.assertIn('OKXQUANT_MANUAL_CLOSE_ENABLED=1',env.read_text())
            before_invalid=env.read_bytes()
            with self.assertRaises(center.AccountChangeError):center.set_manual_close(False,'ENABLE MANUAL CLOSE')
            self.assertEqual(env.read_bytes(),before_invalid)
            center.set_manual_close(False,'DISABLE MANUAL CLOSE')
            self.assertIn('OKXQUANT_MANUAL_CLOSE_ENABLED=0',env.read_text())
        self.assertEqual(center.load(),before);request.assert_not_called()
