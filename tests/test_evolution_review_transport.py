"""Review requests and evidence rendering must not depend on trading availability."""
from contextlib import ExitStack
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import scripts.self_improvement_engine as engine
from scripts.evolution_evidence import prompt_payload
from okxquant_backend import llm_manager

class ReviewPromptTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key,value in {'DATA_DIR':str(self.root),'LOG_FILE':str(self.root/'log'),'EVOLUTION_LAST_PROMPT_FILE':str(self.root/'prompt.txt')}.items():
            self.stack.enter_context(patch.object(engine,key,value))
        self.stack.enter_context(patch.object(engine,'get_cpa_client_config',return_value=('https://example.invalid/v1','FAKE')))
        self.stack.enter_context(patch.object(engine,'active_profile',return_value={'id':'fixture','name':'fixture'}))
        self.stack.enter_context(patch.object(engine,'ModelCallTelemetry',return_value=Mock()))
        self.stack.enter_context(patch.object(llm_manager,'get_active_llm_runtime',return_value={'model':'fixture','reasoning_effort':'high','api_format':'openai_chat','api_key':'FAKE','base_url':'https://example.invalid/v1'}))
    def row(self,i):
        return {'trade_id':'trade-'+str(i),'inst':'BTC','time':'2026-09-18 10:00:00','net_pnl':i-25,'gross_pnl':i-24,'fee':-1,'strategy':'已验证成交策略说明'*25,
                'decision_evidence':{'status':'linked','entries':[{'features':{'price':100+i}}]}}
    def test_financial_rows_are_not_truncated_by_editor_module_limit(self):
        rows=[self.row(i) for i in range(80)];reply={'change_status':'NO_CHANGE','memory_proposals':[]}
        with patch.object(llm_manager,'execute_llm_request',return_value=(json.dumps(reply),'',{},1)) as request:
            result=engine.call_llm_evolution_review(rows,timestamp_str='2026-09-18 10:00:00')
        self.assertEqual(result,reply)
        kwargs=request.call_args.kwargs
        self.assertEqual((kwargs['timeout'],kwargs['attempt_timeout'],kwargs['max_attempts']),(180.,80.,2))
        self.assertTrue(kwargs['require_complete'])
        user=kwargs['messages'][1]['content'];marker='【完整运行证据 JSON】\n'
        payload=json.JSONDecoder().raw_decode(user.split(marker,1)[1])[0]
        self.assertEqual(len(payload['financial_rows']),80)
        self.assertEqual({r['trade_id'] for r in payload['financial_rows']},{r['trade_id'] for r in rows})
        self.assertGreater(len(json.dumps(payload['financial_rows'],ensure_ascii=False)),12000)
        self.assertEqual(payload['feedback']['settled_samples'],80)
        self.assertLessEqual(payload['coverage']['entry_detail_rows'],8)
        self.assertLessEqual(payload['coverage']['entry_detail_chars'],24000)
    def test_detail_budget_is_explicit_without_cutting_financial_rows_or_json(self):
        rows=[self.row(i) for i in range(3)]
        rows[1]['decision_evidence']['entries'][0]['features']['large']='x'*5000
        result=prompt_payload(rows,detail_budget=400,max_details=2)
        json.dumps(result,allow_nan=False)
        self.assertEqual(len(result['financial_rows']),3)
        self.assertNotIn('trade-1',result['coverage']['entry_detail_trade_ids'])
        self.assertTrue(result['coverage']['unexpanded_details_are_not_missing_archived_evidence'])
    def test_transport_exception_is_not_swallowed_into_empty_review(self):
        from okxquant_backend.llm_transport import LLMRequestError
        with patch.object(llm_manager,'execute_llm_request',side_effect=LLMRequestError(503,2,'http_error')):
            with self.assertRaises(LLMRequestError):engine.call_llm_evolution_review([self.row(0)])
    def test_malformed_model_json_is_failure_not_no_change(self):
        with patch.object(llm_manager,'execute_llm_request',return_value=('not json','',{},1)):
            with self.assertRaises(json.JSONDecodeError):engine.call_llm_evolution_review([self.row(0)])
