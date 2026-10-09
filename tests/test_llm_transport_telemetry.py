import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from okxquant_gateway import telemetry
from okxquant_gateway.store import GatewayStore
from okxquant_backend.llm_transport import LLMRequestError


class TransportTelemetryTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/'gateway.db'
        p=patch.object(telemetry,'DB_PATH',self.path);p.start();self.addCleanup(p.stop)

    def test_real_call_metadata_is_persisted_safely_and_zero_is_not_unknown(self):
        call=telemetry.ModelCallTelemetry('diagnostic','fixture','high','PRIVATE PROMPT','PRIVATE USER')
        identity=call.finish('success',{'usage':{'prompt_tokens':0,'completion_tokens':2,'total_tokens':2,'_transport':{'transport_mode':'stream','heartbeat_count':0,'first_byte_ms':0,'completion_seen':True,'body':'PRIVATE BODY','api_key':'SECRET'}}})
        row=GatewayStore(self.path).model_calls()[0]
        self.assertEqual(row['id'],identity);self.assertEqual(row['input_tokens'],0)
        self.assertEqual(row['transport']['first_byte_ms'],0);self.assertTrue(row['transport']['completion_seen'])
        for secret in ['PRIVATE PROMPT','PRIVATE BODY','SECRET']:self.assertNotIn(secret,str(row))

    def test_failure_phase_survives_without_exposing_provider_body(self):
        error=LLMRequestError(524,1,'http_error');error.transport_diagnostics={'failure_phase':'http','http_status':524,'attempts':1,'cf_ray':'0123456789abcdef-HKG','headers':{'Authorization':'PRIVATE'}}
        telemetry.ModelCallTelemetry('diagnostic','fixture','high','','').finish('failed',error=error)
        row=GatewayStore(self.path).model_calls()[0]
        self.assertEqual(row['transport']['http_status'],524);self.assertEqual(row['transport']['cf_ray'],'0123456789abcdef-HKG')
        self.assertNotIn('PRIVATE',str(row));self.assertIsNone(row['total_tokens'])

    def test_invalid_usage_remains_unknown(self):
        telemetry.ModelCallTelemetry('diagnostic','fixture','high','','').finish('success',{'usage':{'prompt_tokens':True,'completion_tokens':-1,'total_tokens':'invented'}})
        row=GatewayStore(self.path).model_calls()[0]
        self.assertIsNone(row['input_tokens']);self.assertIsNone(row['output_tokens']);self.assertIsNone(row['total_tokens'])

    def test_no_http_response_does_not_become_fictitious_status_zero(self):
        from okxquant_backend.llm_transport import safe_transport_diagnostics
        value=safe_transport_diagnostics({'http_status':0,'failure_phase':'total','attempts':1,'completion_seen':False})
        self.assertNotIn('http_status',value);self.assertNotIn('bytes_received',value)
        self.assertEqual(value['failure_phase'],'total')
