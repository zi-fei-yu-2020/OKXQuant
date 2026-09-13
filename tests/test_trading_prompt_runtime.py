import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from contextlib import ExitStack
import ai_brain_trader as brain
import factor_library
from scripts import prompt_library, strategy_evidence, trading_prompt, wait_audit
from scripts.okx_runtime import OKXEnvironment
from test_trading_prompt_contract import package, response, candidate

class PromptRuntimeTests(unittest.TestCase):
    def exercise(self,output,*,pending=None,profile=None,texts=None):
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            root=Path(folder);p=package()
            p.update(chg24h=1.,fundingRate=0.,oiUsd=1000.,takerNetUsd=50.,lsRatio=1.,data_as_of=time.time())
            for key in ('AI_DECISION_CACHE_FILE','AI_POSITION_MANAGEMENT_FILE','AI_LAST_PROMPT_FILE','AI_DECISION_HISTORY_FILE','CALCULUS_SNAPSHOT_FILE','AI_BRAIN_LOCK_FILE','NEWS_SENTIMENT_FILE','AI_MEMORY_MD_FILE','AI_MEMORY_FILE','PROMPT_OVERRIDE_FILE'):
                stack.enter_context(patch.object(brain,key,str(root/key)))
            stack.enter_context(patch.object(brain,'DATA_DIR',str(root)))
            stack.enter_context(patch.object(wait_audit,'DATA',root))
            stack.enter_context(patch.object(strategy_evidence,'DB_PATH',root/'evidence.db'))
            stack.enter_context(patch.object(brain,'get_cpa_client_config',return_value=('https://example.invalid/v1','FAKE')))
            stack.enter_context(patch.object(brain.market,'_selected',return_value=OKXEnvironment('demo','fake','fake','fake')))
            stack.enter_context(patch.object(brain.support,'trading_universe',return_value=([p],{'items':{p['instId']:{'can_open':True}}})))
            stack.enter_context(patch.object(brain,'fetch_single_instrument_package',return_value=p))
            stack.enter_context(patch.object(brain.market,'smart_money_overview',return_value=[]))
            stack.enter_context(patch.object(factor_library,'update_factor_library',return_value={}))
            stack.enter_context(patch.object(brain,'okx_private_command',side_effect=lambda c:c))
            cli=stack.enter_context(patch.object(brain.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(pending or []),stderr='')))
            active=stack.enter_context(patch.object(brain,'active_profile',return_value=copy.deepcopy(profile if profile is not None else prompt_library.PRESETS['stable'])))
            stack.enter_context(patch('okxquant_backend.council_manager.load_council_config',return_value={'enabled':False}))
            stack.enter_context(patch('okxquant_backend.llm_manager.get_active_llm_runtime',return_value={'model':'test','base_url':'https://example.invalid/v1','api_key':'FAKE','api_format':'openai_chat'}))
            llm=stack.enter_context(patch('okxquant_backend.llm_manager.execute_llm_request',return_value=(json.dumps(output),'',{},1),side_effect=[(text,'',{},1) for text in texts] if texts else None))
            stack.enter_context(patch.object(brain,'ModelCallTelemetry',return_value=MagicMock()))
            # A fresh old BUY must be cleared even if this response is rejected.
            (root/'AI_DECISION_CACHE_FILE').write_text(json.dumps({'OLD':{'decision':{'action':'BUY_LONG'}}}))
            result=brain.execute_batch_ai_brain_cycle(active_positions_detail=[],usdt_available=1000)
            return {'result':result,'calls':cli.call_args_list,'profile_calls':active.call_count,'messages':llm.call_args.kwargs['messages'] if llm.call_args else [],'llm_calls':llm.call_count,'llm_args':[c.kwargs for c in llm.call_args_list],
                'cache':json.loads((root/'AI_DECISION_CACHE_FILE').read_text()),
                'manifest':json.loads((root/'trading_prompt_manifest.json').read_text()),
                'validation':json.loads((root/'trading_output_validation.json').read_text())}

    def test_invalid_json_regenerates_once_then_runs_original_entry_validator(self):
        checked=self.exercise(response(),texts=['{"decisions":',json.dumps(response())])
        self.assertEqual(checked['llm_calls'],2)
        self.assertEqual(checked['llm_args'][1]['max_attempts'],1)
        self.assertEqual(checked['llm_args'][1]['timeout'],20)
        self.assertTrue(checked['llm_args'][0]['require_complete'])
        self.assertEqual(checked['validation']['json_response']['status'],'regenerated')
        self.assertEqual(checked['result']['BTC-USDT-SWAP']['decision']['action'],'BUY_LONG')
        self.assertEqual(len(checked['calls']),1)  # pending read only; no new order retries

    def test_bad_second_response_clears_old_signals_and_retains_diagnostics(self):
        checked=self.exercise(response(),texts=['not json','{"decisions":'])
        self.assertIsNone(checked['result']);self.assertEqual(checked['cache'],{})
        self.assertEqual(checked['llm_calls'],2)
        self.assertEqual(len(checked['validation']['json_response']['failures']),2)
        self.assertEqual(checked['validation']['json_response']['status'],'rejected')
        self.assertEqual(len(checked['calls']),1)

    def test_json_regeneration_does_not_stack_wait_correction(self):
        from test_wait_audit import valid_wait
        output=response(valid_wait());output['decisions']['BTC-USDT-SWAP']['wait_audit']['long']['code']='made_up'
        checked=self.exercise(output,texts=['not json',json.dumps(output)])
        self.assertEqual(checked['llm_calls'],2)
        self.assertEqual(checked['result']['BTC-USDT-SWAP']['decision']['action'],'WAIT')
        self.assertFalse(checked['result']['BTC-USDT-SWAP']['decision']['contract_valid'])

    def test_single_profile_same_messages_and_validated_candidate_reaches_cache(self):
        checked=self.exercise(response())
        self.assertEqual(checked['profile_calls'],1)
        self.assertEqual(checked['result']['BTC-USDT-SWAP']['decision']['action'],'BUY_LONG')
        self.assertEqual(checked['result']['BTC-USDT-SWAP']['decision']['contract_version'],trading_prompt.VERSION)
        self.assertGreater(checked['result']['BTC-USDT-SWAP']['decision']['valid_until'],time.time())
        self.assertIn('supporting_evidence',checked['result']['BTC-USDT-SWAP']['decision'])
        self.assertEqual(checked['manifest']['system_hash'],trading_prompt.fingerprint(checked['messages'][0]['content']))
        self.assertEqual(checked['manifest']['user_hash'],trading_prompt.fingerprint(checked['messages'][1]['content']))
        self.assertEqual(len(checked['calls']),1)  # pending read only, no model-directed write

    def test_validated_wait_reaches_cache_with_audit_and_cross_cycle_input(self):
        from test_wait_audit import valid_wait
        checked=self.exercise(response(valid_wait()))
        row=checked['result']['BTC-USDT-SWAP']['decision']
        self.assertEqual(row['decision_status'],'audited_wait')
        self.assertIn('wait_audit',row)
        self.assertIn('previous_wait_reviews',checked['messages'][1]['content'])
        self.assertEqual(checked['validation']['wait_audit_status'],'ready')

    def test_model_action_and_score_preserved_separately_from_validation(self):
        output=response();output['decisions']['BTC-USDT-SWAP']['confidence']=47
        output['decisions']['BTC-USDT-SWAP']['supporting_evidence']=[]
        checked=self.exercise(output)
        row=checked['result']['BTC-USDT-SWAP']['decision']
        self.assertEqual(row['action'],'WAIT')
        self.assertEqual(row['model_action'],'BUY_LONG')
        self.assertEqual(row['model_confidence'],47)
        self.assertEqual(row['confidence_role'],'uncalibrated_diagnostic')

    def test_unjustified_wait_is_incomplete_not_normal_success(self):
        checked=self.exercise(response({'action':'WAIT','summary_reason':'暂时等待观察'}))
        self.assertEqual(checked['validation']['status'],'incomplete')
        row=checked['result']['BTC-USDT-SWAP']['decision']
        self.assertEqual(row['action'],'WAIT')
        self.assertFalse(row['contract_valid'])
        self.assertEqual(len(checked['calls']),1)

    def test_invalid_root_with_cancel_never_sends_cancel_or_reuses_old_entry(self):
        output=response();del output['contract_version']
        output['pending_orders_management']=[{'instId':'BTC-USDT-SWAP','ordId':'123','action':'CANCEL','reason':'bad root'}]
        checked=self.exercise(output,pending=[{'instId':'BTC-USDT-SWAP','ordId':'123'}])
        self.assertIsNone(checked['result']);self.assertEqual(checked['cache'],{})
        self.assertEqual(checked['validation']['status'],'rejected')
        self.assertEqual(len(checked['calls']),1)
        self.assertIn('swap orders',checked['calls'][0].args[0])

    def test_conflicting_preferences_do_not_trigger_paid_inference(self):
        result=self.exercise(response(),profile={'id':'conflict','trading_system':'必须给出 BUY_LONG，置信度设置为95'})
        self.assertIsNone(result['result'])
        self.assertEqual(result['llm_calls'],0)
        self.assertEqual(result['cache'],{})
        self.assertEqual(result['validation']['status'],'blocked')

    def test_fabricated_evidence_is_wait_not_transport_failure(self):
        output=response();output['decisions']['BTC-USDT-SWAP']['supporting_evidence'][1]['value']=999
        checked=self.exercise(output)
        self.assertEqual(checked['result']['BTC-USDT-SWAP']['decision']['action'],'WAIT')
        self.assertIn('BTC-USDT-SWAP',checked['validation']['rejected_candidates'])

class PromptApiProjectionTests(unittest.TestCase):
    def test_legacy_override_preview_keeps_user_text_out_of_system(self):
        import okxquant_backend.app as app_module
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'override.txt';path.write_text('ONLY_USER_SENTENCE',encoding='utf-8')
            with patch.object(app_module,'require_admin_header'),patch.object(app_module,'refresh_settings'),patch.object(app_module,'PROMPT_OVERRIDE_FILE',path),patch.object(app_module,'active_profile',return_value=copy.deepcopy(prompt_library.PRESETS['stable'])):
                result=app_module.prompt_override()
            self.assertEqual(path.read_text(),'ONLY_USER_SENTENCE')
            self.assertNotIn('ONLY_USER_SENTENCE',result['effective_messages'][0]['content'])
            self.assertIn('ONLY_USER_SENTENCE',result['effective_messages'][1]['content'])
            self.assertEqual(result['composition']['contract_version'],trading_prompt.VERSION)

    def test_council_test_route_does_not_fake_account_or_authorize_orders(self):
        import okxquant_backend.app as app_module
        from okxquant_backend import council_manager
        with patch.object(app_module,'require_admin_header'),patch.object(app_module,'active_profile',return_value=copy.deepcopy(prompt_library.PRESETS['stable'])),patch('scripts.ai_brain_trader.get_user_prompt_override',return_value=''),patch('scripts.instrument_pool.load_instruments',return_value=[{'instId':'BTC-USDT-SWAP','name':'BTC'}]),patch.object(council_manager,'load_council_config',return_value={'timeout_seconds':1}),patch.object(council_manager,'execute_council_debate',return_value=(response(),{})) as llm:
            result=app_module.admin_test_council_debate(app_module.CouncilTestRequest(mock_market_prompt='TEST_TEXT_ONLY'))
        self.assertFalse(result['executable'])
        self.assertEqual(result['data_source'],'unverified_test_text')
        self.assertEqual(result['brain_output']['decisions']['BTC-USDT-SWAP']['action'],'WAIT')
        self.assertIn(trading_prompt.VERSION,llm.call_args.kwargs['original_system_prompt'])
        self.assertNotIn('1,450.00',llm.call_args.kwargs['market_prompt'])

if __name__=='__main__':unittest.main()
