"""A05 offline regressions: mocked inference/private IO, disposable files only."""
import copy
import json
import math
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import trading_prompt, model_json, news_connection, memory_registry
from scripts.okx_runtime import OKXEnvironment
from okxquant_backend import council_manager, llm_manager
from okxquant_backend.llm_transport import LLMRequestError
import test_trading_prompt_runtime as runtime_fixture
import test_evolution_review_status as evolution_fixture
from test_trading_prompt_contract import package, response


class PromptContextTests(unittest.TestCase):
    def test_variables_reference_frozen_user_data_and_do_not_expand_into_system(self):
        profile = {'id':'audit', 'name':'Audit Profile', 'trading_user':
                   '{{profile_name}} {{active_instruments}} {{timezone}} {{strategy_version}} {{news_intelligence}} {{custom_unknown}}'}
        context = {'news_intelligence':'UNTRUSTED_NEWS_MARKER', 'pending_orders_status':'verified'}
        bundle = trading_prompt.compose(profile, context, [package()])
        data = json.JSONDecoder().raw_decode(bundle.user)[0]
        self.assertTrue(bundle.allow_open)
        self.assertEqual(data['runtime_data']['profile_name'], 'Audit Profile')
        self.assertEqual(data['runtime_data']['active_instruments'], 'BTC')
        self.assertEqual(data['runtime_data']['timezone'], 'Asia/Shanghai')
        self.assertEqual(data['runtime_data']['custom_unknown'], 'UNKNOWN')
        self.assertIn('[runtime_data.news_intelligence]', data['user_preferences'][0]['content'])
        self.assertNotIn('UNTRUSTED_NEWS_MARKER', bundle.system)
        self.assertNotIn('profile_name', context)  # caller snapshot remains immutable
        self.assertIn({'code':'runtime_variable_unknown','variable':'custom_unknown'}, bundle.manifest['warnings'])

    def test_runtime_execution_profile_uses_same_snapshot_as_preferences(self):
        profile = {'id':'audit-small', 'name':'Small fixture', 'execution_profile':'small300'}
        with patch('okxquant_backend.account_connections.assert_current'):
            checked = runtime_fixture.PromptRuntimeTests().exercise(response(), profile=profile)
        self.assertIsNotNone(checked['result'])
        data = json.JSONDecoder().raw_decode(checked['messages'][1]['content'])[0]
        self.assertEqual(data['runtime_data']['execution_profile']['profile_id'], 'audit-small')
        self.assertEqual(data['runtime_data']['execution_profile']['execution']['id'], 'small300')
        self.assertEqual(checked['profile_calls'], 1)
        row=checked['result']['BTC-USDT-SWAP']
        self.assertIn('2.2',row['thought_process']['risk_reward_evaluation'])
        self.assertEqual(row['strategy_profile_hash'],row['decision']['strategy_profile_hash'])

    def test_static_signature_ignores_rendered_flat_copies_but_tracks_modules(self):
        profile = {'id':'custom', 'name':'Custom', 'pipelines':{'trading_user':[
            {'id':'m','content':'Use {{decision_timestamp}} and {{news_intelligence}}','enabled':True}]},
            'trading_user':'Rendered yesterday with stale default context'}
        first = trading_prompt.profile_signature(profile)
        profile['trading_user'] = 'Rendered today with different context'
        self.assertEqual(trading_prompt.profile_signature(profile), first)
        profile['pipelines']['trading_user'][0]['content'] += ' Prefer closed structure.'
        self.assertNotEqual(trading_prompt.profile_signature(profile), first)

    def test_original_lightweight_brain_adapter_remains_usable(self):
        import test_brain_regressions as original
        fixture = original.BrainRegressions()
        fixture.setUp()
        try:
            fixture.test_council_success_returns_this_cycle_and_finishes_success_without_fallback()
        finally:
            fixture.doCleanups()


class CompleteModelTests(unittest.TestCase):
    def test_malformed_envelopes_raise_contract_error_not_attribute_errors(self):
        cases = [('openai_chat', None), ('openai_chat', {'choices':[1]}),
                 ('openai_chat', {'choices':[{'message':[]}]}),
                 ('claude_messages', {'content':[None]}),
                 ('openai_responses', {'output':[None]}),
                 ('openai_responses', {'output_text':42})]
        for protocol, body in cases:
            with self.subTest(protocol=protocol, body=body), self.assertRaises(trading_prompt.ContractError):
                model_json.verify_completion(body, protocol)

    def test_refusals_and_tool_calls_cannot_hide_beside_valid_json(self):
        cases = [
            ('openai_responses', {'status':'completed','output_text':'{}','output':[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]}),
            ('openai_responses', {'status':'completed','output':[{'type':'message','status':'incomplete','content':[{'type':'output_text','text':'{}'}]}]}),
            ('claude_messages', {'stop_reason':'end_turn','content':[{'type':'text','text':'{}'},{'type':'tool_use'}]}),
            ('openai_chat', {'choices':[{'finish_reason':'stop','message':{'content':'{}','tool_calls':[{}]}}]}),
        ]
        for protocol, body in cases:
            with self.subTest(protocol=protocol), self.assertRaises(trading_prompt.ContractError):
                model_json.verify_completion(body, protocol)

    def test_good_multi_protocol_outputs_still_allowed(self):
        for protocol, body in [
            ('openai_chat', {'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}),
            ('claude_messages', {'stop_reason':'end_turn','content':[{'type':'thinking','thinking':'reason'},{'type':'text','text':'{}'}]}),
            ('openai_responses', {'status':'completed','output':[{'type':'reasoning'},{'type':'message','status':'completed','content':[{'type':'output_text','text':'{}'}]}]}),
        ]:
            model_json.verify_completion(body, protocol)

    def test_regeneration_is_one_call_and_does_not_retry_transport_failure(self):
        retry = Mock(return_value='{}'); report = {}
        self.assertEqual(model_json.decode_with_regeneration(lambda:'{"bad":', retry, report=report), {})
        retry.assert_called_once_with(timeout=20.0, max_attempts=1)
        retry.reset_mock()
        with self.assertRaises(LLMRequestError):
            model_json.decode_with_regeneration(Mock(side_effect=LLMRequestError(503, 2, 'http_error')), retry)
        retry.assert_not_called()

    def test_manager_rejects_responses_refusal_before_extracting_output_text(self):
        body = {'status':'completed', 'output_text':'{}', 'output':[{'type':'message','content':[{'type':'refusal'}]}]}
        with patch.object(llm_manager, 'get_active_llm_runtime', return_value={}), patch.object(llm_manager, 'request_json', return_value=(body, 200, 1, 1)):
            with self.assertRaises(trading_prompt.ContractError):
                llm_manager.execute_llm_request(messages=[], model='fixture', base_url='https://example.invalid', api_key='FAKE', api_format='openai_responses', require_complete=True)


class CouncilBudgetTests(unittest.TestCase):
    def debate(self, clock):
        config = {'roles':{'cio':{'is_arbitrator':True,'enabled':True,'prompt':''}}}
        with patch.object(council_manager, 'load_council_config', return_value=config), patch.object(council_manager.time, 'monotonic', side_effect=clock), patch.object(llm_manager, 'load_llm_config', return_value={}), patch.object(llm_manager, 'get_active_llm_runtime', return_value={'model':'fixture'}), patch.object(llm_manager, 'execute_llm_request', return_value=('{}','',{},1)) as call:
            result = council_manager.execute_council_debate('{}', 'system', timeout=2)
            return result, call.call_args.kwargs

    def test_small_budget_is_not_increased_to_twenty_five_seconds(self):
        result, kwargs = self.debate([10,10.5,11,11])
        self.assertEqual(kwargs['timeout'], 1.5)
        self.assertIs(kwargs['require_complete'], True)

    def test_late_arbitration_is_never_accepted(self):
        with self.assertRaises(LLMRequestError) as error:
            self.debate([10,10.5,13])
        self.assertEqual(error.exception.category, 'deadline_exceeded')

    def test_expired_budget_cannot_start_arbitration(self):
        with self.assertRaises(LLMRequestError):
            self.debate([10,12.1])


class DecisionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.brain = runtime_fixture.brain
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'cache.json'
        self.env = OKXEnvironment('demo','FAKE','FAKE','FAKE',account_scope='scope-A')
        self.profile = {'id':'same-id','trading_user':'Original strategy'}
        self.row = {'timestamp':time.time(), **self.brain.account_basis(self.env),
                    'strategy_profile_hash':trading_prompt.profile_signature(self.profile),
                    'decision':{'action':'BUY_LONG','contract_version':trading_prompt.VERSION,'contract_valid':True,'valid_until':time.time()+60}}
        for target, name, value in [(self.brain,'AI_DECISION_CACHE_FILE',str(self.path))]:
            p = patch.object(target,name,value);p.start();self.addCleanup(p.stop)
        for target, name, value in [(self.brain.market,'_selected',self.env),(self.brain,'active_profile',self.profile)]:
            p = patch.object(target,name,return_value=value);p.start();self.addCleanup(p.stop)
        p = patch('okxquant_backend.account_connections.assert_current');p.start();self.addCleanup(p.stop)

    def read(self):
        self.path.write_text(json.dumps({'BTC-USDT-SWAP':self.row}),encoding='utf8')
        return self.brain.get_latest_ai_decision('BTC-USDT-SWAP')

    def test_current_cache_is_readable(self):
        self.assertEqual(self.read(), self.row)

    def test_foreign_or_unscoped_cache_is_not_reused(self):
        self.row['account_scope'] = 'scope-B'; self.assertIsNone(self.read())
        del self.row['account_scope']; self.assertIsNone(self.read())

    def test_binding_rotation_is_not_same_decision_even_for_same_scope(self):
        self.row['binding_version'] = 2; self.assertIsNone(self.read())

    def test_same_profile_id_edit_invalidates_cached_decision(self):
        self.profile['trading_user'] = 'New strategy'; self.assertIsNone(self.read())

    def test_nonfinite_expiration_is_not_immortal(self):
        for value in (float('nan'),float('inf'),time.time()-1):
            self.row['decision']['valid_until'] = value
            self.assertIsNone(self.read())

    def test_stale_or_future_frame_cannot_publish_management(self):
        for stamp in (time.time()-301,time.time()+60,float('nan')):
            with self.assertRaises(trading_prompt.ContractError):
                self.brain.assert_cycle_current(self.env, stamp)

    def test_inflight_account_change_rejected(self):
        other = OKXEnvironment('demo','FAKE','FAKE','FAKE',account_scope='scope-B')
        with patch.object(self.brain.market,'_selected',return_value=other), self.assertRaises(trading_prompt.ContractError):
            self.brain.assert_cycle_current(self.env,time.time())

    def test_stale_inference_leaves_cache_empty_and_sends_no_cancel(self):
        guard = self.brain.assert_cycle_current
        with patch.object(self.brain,'assert_cycle_current',side_effect=lambda env, at: guard(env, at-301)):
            checked = runtime_fixture.PromptRuntimeTests().exercise(response())
        self.assertIsNone(checked['result']);self.assertEqual(checked['cache'],{})
        self.assertEqual(len(checked['calls']),1)  # read pending orders only
        self.assertEqual(checked['validation']['status'],'rejected')

    def test_late_history_write_failure_invalidates_already_published_cache(self):
        write = self.brain.atomic_write_json
        management = []
        def fail_history(path, payload):
            if Path(path).name == 'AI_DECISION_HISTORY_FILE':
                raise OSError('fixture history failure')
            if Path(path).name == 'AI_POSITION_MANAGEMENT_FILE':
                management.append(copy.deepcopy(payload))
            return write(path,payload)
        with patch.object(self.brain,'atomic_write_json',side_effect=fail_history):
            checked = runtime_fixture.PromptRuntimeTests().exercise(response())
        self.assertIsNone(checked['result'])
        self.assertEqual(checked['cache'],{})
        self.assertEqual(management[-1],{'timestamp':0,'instructions':[]})
        self.assertEqual(checked['validation']['status'],'rejected')

    def test_real_runtime_always_checks_registry_binding(self):
        from okxquant_backend.account_connections import AccountChangeError
        for source in ('environment','account-center','account-center-unbound'):
            env = OKXEnvironment('demo','FAKE','FAKE','FAKE',source=source,account_scope='scope-A')
            with patch.object(self.brain.market,'_selected',return_value=env), patch('okxquant_backend.account_connections.assert_current',side_effect=AccountChangeError('rotated')) as check:
                with self.assertRaises(AccountChangeError):
                    self.brain.assert_cycle_current(env,time.time())
                check.assert_called_once_with(env)

    def test_management_output_has_scope_strategy_position_and_frame_provenance(self):
        outputs = []
        write = self.brain.atomic_write_json
        def capture(path, payload):
            if Path(path).name == 'AI_POSITION_MANAGEMENT_FILE':
                outputs.append(copy.deepcopy(payload))
            return write(path,payload)
        with patch.object(self.brain,'atomic_write_json',side_effect=capture):
            checked = runtime_fixture.PromptRuntimeTests().exercise(response())
        self.assertIsNotNone(checked['result'])
        payload=outputs[-1]
        self.assertIn('account_scope',payload)
        self.assertIn('binding_version',payload)
        self.assertIn('strategy_profile_hash',payload)
        self.assertEqual(payload['position_basis'],[])
        self.assertLessEqual(payload['timestamp'],payload['generated_at'])


class NewsBoundaryTests(unittest.TestCase):
    def payload(self):
        return {'schema':2,'connection_status':'partial','sections':{
            'latest':{'status':'fresh','last_success_at':1000,'data':[{'id':'1','cTime':990000,'title':'BTC event','summary':'reported context'}]}}}

    def test_malformed_section_or_row_does_not_destroy_valid_news(self):
        payload = self.payload();payload['sections']['important'] = ['invalid']
        payload['sections']['latest']['data'].append(None)
        snapshot = news_connection.strategy_snapshot(payload,['BTC'],now=1000)
        self.assertEqual([r['id'] for r in snapshot['items']],['1'])
        self.assertFalse(snapshot['section_freshness']['important']['usable'])

    def test_bad_sentiment_is_unknown_not_neutral_or_trade_signal(self):
        for bad in ([1],{'BTC':None}):
            payload = self.payload();payload['sections']['sentiment'] = {'status':'fresh','last_success_at':1000,'data':bad}
            snapshot = news_connection.strategy_snapshot(payload,['BTC'],now=1000)
            self.assertFalse(snapshot['sentiment_fresh']);self.assertEqual(snapshot['coins_sentiment'],{})
            self.assertTrue(snapshot['items'])

    def test_stale_news_and_future_source_are_excluded(self):
        payload=self.payload();payload['sections']['latest']['data'][0]['cTime']=1001000
        self.assertEqual(news_connection.strategy_snapshot(payload,['BTC'],now=1000)['items'],[])
        self.assertEqual(news_connection.strategy_snapshot(self.payload(),['BTC'],now=2300)['items'],[])

    def test_corrupt_root_degrades_to_unknown_without_binding_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'news.json';path.write_text('[]')
            with patch.object(news_connection.account_connections,'news_connection') as conn:
                snapshot=news_connection.load_strategy_snapshot(path,['BTC'],now=1000)
            conn.assert_not_called();self.assertEqual(snapshot['items'],[])

    def test_connection_generation_mismatch_drops_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            payload=self.payload();payload.update(connection_id='a',connection_generation=1)
            path=Path(folder)/'news.json';path.write_text(json.dumps(payload))
            with patch.object(news_connection.account_connections,'news_connection',return_value={'id':'a','generation':2}):
                snapshot=news_connection.load_strategy_snapshot(path,['BTC'],now=1000)
            self.assertEqual(snapshot['items'],[])


class ReplaySeparationTests(unittest.TestCase):
    def test_default_calculus_replay_never_overwrites_live_snapshot(self):
        import os
        import sys
        from scripts import calculus_replay
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data').mkdir()
            live=root/'data'/'calculus_snapshot.json';live.write_text('LIVE SNAPSHOT')
            source=root/'input.json';source.write_text('[]')
            old=os.getcwd()
            try:
                os.chdir(root)
                with patch.object(sys,'argv',['calculus_replay','--input',str(source)]):
                    calculus_replay.main()
            finally:
                os.chdir(old)
            self.assertEqual(live.read_text(),'LIVE SNAPSHOT')
            report=json.loads((root/'data'/'calculus_replay_report.json').read_text())
            self.assertEqual(report['mode'],'offline_replay')
            self.assertIs(report['order_authorized'],False)


class ReviewSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.fixture=evolution_fixture.EvolutionReviewTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine=evolution_fixture.engine

    def test_report_identifies_inference_memory_not_later_publication(self):
        old={'active_version':1,'prompt_hash':'old-hash','content':'old memory','rules':[]}
        new={**old,'active_version':2,'prompt_hash':'new-hash'}
        with patch.object(self.engine,'load_closed_trades',return_value=[]), patch.object(self.engine,'call_llm_evolution_review',return_value={'change_status':'NO_CHANGE'}), patch.object(memory_registry,'view',side_effect=[old,new]):
            report=self.engine.run_self_evolution(force=True)
        self.assertEqual(report['memory_version_at_review'],1)
        self.assertEqual(report['memory_prompt_hash_at_review'],'old-hash')
        self.assertTrue(report['memory_preserved']);self.assertEqual(report['change_status'],'NO_CHANGE')

    def test_legacy_memory_hash_change_retriggers_review_even_with_none_version(self):
        with patch.object(self.engine,'load_closed_trades',return_value=[]), patch.object(self.engine,'call_llm_evolution_review',return_value={'change_status':'NO_CHANGE'}) as model:
            first=self.engine.run_self_evolution()
            unchanged=self.engine.run_self_evolution()
            self.assertEqual(model.call_count,1)
            (self.fixture.root/'AI_TRADING_MEMORY.md').write_text('CHANGED BY EXPLICIT OPERATOR')
            second=self.engine.run_self_evolution()
            self.assertEqual(model.call_count,2)
        self.assertNotEqual(first['memory_prompt_hash_at_review'],second['memory_prompt_hash_at_review'])
        self.assertFalse((self.fixture.root/'memory_registry.db').exists())


if __name__=='__main__':
    unittest.main()
