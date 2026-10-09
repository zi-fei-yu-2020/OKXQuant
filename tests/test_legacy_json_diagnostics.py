import unittest
from copy import deepcopy
from unittest.mock import patch,Mock
from scripts.model_json import decode_with_regeneration
from okxquant_backend.llm_transport import LLMRequestError,normalize_legacy_failure_record,safe_transport_diagnostics

class LegacyJsonDiagnosticsTests(unittest.TestCase):
    def test_legacy_label_projection_preserves_original_evidence(self):
        row={'failure_reason':'模型请求失败：?????JSON???????（HTTP 200）','model_failure':
             {'category':'truncated_model_output','http_status':200,'attempts':1,'message':'?????JSON???????'}}
        before=deepcopy(row);fixed=normalize_legacy_failure_record(row)
        self.assertIn('模型JSON输出被截断',fixed['failure_reason']);self.assertEqual(row,before)
        self.assertTrue(fixed['model_failure']['legacy_label_corrected'])
    def test_unknown_or_invalid_failure_not_reconstructed(self):
        row={'failure_reason':'?????JSON???????','model_failure':{'category':'other','http_status':200,'attempts':1}}
        self.assertIs(normalize_legacy_failure_record(row),row)
        row['model_failure']['category']='truncated_model_output';row['model_failure']['attempts']=True
        self.assertIs(normalize_legacy_failure_record(row),row)
    def test_truncated_json_reports_characters_without_leaking_text(self):
        text='{"private-token-example":"unfinished';report={}
        with self.assertRaises(LLMRequestError) as cm:decode_with_regeneration(lambda:text,report=report)
        d=cm.exception.transport_diagnostics
        self.assertEqual(d['output_chars'],len(text));self.assertEqual(d['output_validation'],'truncated_json')
        self.assertEqual(d['failure_phase'],'json');self.assertNotIn('private-token-example',str(d))
    def test_new_diagnostic_fields_are_allowlisted_and_bounded(self):
        d=safe_transport_diagnostics({'max_output_tokens':16384,'output_chars':42,'finish_reason':'stop',
            'output_validation':'truncated_json','failure_phase':'json','body':'secret','completion_seen':True})
        self.assertEqual(d['finish_reason'],'stop');self.assertTrue(d['completion_seen']);self.assertNotIn('body',d)
        bad=safe_transport_diagnostics({'finish_reason':'secret-key','max_output_tokens':True,'output_chars':float('nan')})
        self.assertEqual(bad,{})
    def test_failed_parser_retains_observed_usage_and_transport(self):
        from okxquant_gateway.telemetry import ModelCallTelemetry
        store=Mock();error=LLMRequestError(200,1,'truncated_model_output')
        error.transport_diagnostics={'failure_phase':'json','output_validation':'truncated_json','output_chars':30}
        response={'usage':{'completion_tokens':123,'prompt_tokens':456,'total_tokens':579,
            '_transport':{'attempts':1,'http_status':200,'completion_seen':True,'finish_reason':'stop','max_output_tokens':16384}}}
        with patch('okxquant_gateway.telemetry.GatewayStore',return_value=store):
            ModelCallTelemetry('offline_test','unchanged','high','','').finish('failed',response,error=error)
        row=store.record_model_call.call_args.args[0]
        self.assertEqual(row['output_tokens'],123);self.assertEqual(row['transport']['finish_reason'],'stop')
        self.assertTrue(row['transport']['completion_seen']);self.assertEqual(row['transport']['output_validation'],'truncated_json')
