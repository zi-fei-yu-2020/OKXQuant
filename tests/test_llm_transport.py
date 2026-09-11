import io
import json
import ssl
import unittest
import urllib.error
from unittest.mock import MagicMock, patch
from okxquant_backend.llm_transport import LLMRequestError, request_json, retry_delay
from okxquant_backend.llm_manager import test_llm_connection as connection_probe


class LLMTransportTests(unittest.TestCase):
    endpoint = 'https://model.example/v1/chat/completions'
    payload = {'model': 'configured-model', 'messages': [{'role': 'user', 'content': 'private prompt'}],
               'reasoning_effort': 'high', 'response_format': {'type': 'json_object'}}

    def error(self, code, headers=None):
        return urllib.error.HTTPError(self.endpoint, code, 'upstream', headers or {}, io.BytesIO(b'PRIVATE-ERROR-BODY'))

    def response(self, payload=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.getcode.return_value = 200
        response.read.return_value = json.dumps(payload if payload is not None else {'choices': [{'message': {'content': 'PONG'}}]}).encode()
        return response

    def test_transient_errors_retry_exact_payload_and_preserve_high(self):
        with patch('urllib.request.urlopen', side_effect=[self.error(503), self.error(502), self.response()]) as send, patch('okxquant_backend.llm_transport.time.sleep') as sleep:
            data, status, latency, attempts = request_json(self.endpoint, {'Authorization': 'Bearer secret'}, self.payload, 50)
        self.assertEqual(status, 200)
        self.assertEqual(attempts, 3)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        bodies = [c.args[0].data for c in send.call_args_list]
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1], bodies[2])
        self.assertEqual(json.loads(bodies[0]), self.payload)
        self.assertTrue(all(0 < c.kwargs['timeout'] <= 50 for c in send.call_args_list))

    def test_auth_and_parameter_errors_never_retry_or_downgrade(self):
        for code in (400, 401, 403, 404):
            with patch('urllib.request.urlopen', side_effect=self.error(code)) as send, patch('okxquant_backend.llm_transport.time.sleep') as sleep:
                with self.assertRaises(LLMRequestError) as caught:
                    request_json(self.endpoint, {}, self.payload, 50)
            self.assertEqual(caught.exception.status_code, code)
            self.assertEqual(send.call_count, 1)
            sleep.assert_not_called()
            self.assertNotIn('PRIVATE-ERROR-BODY', str(caught.exception))

    def test_exhaustion_is_bounded_and_exposes_attempt_count(self):
        with patch('urllib.request.urlopen', side_effect=[self.error(503) for _ in range(3)]), patch('okxquant_backend.llm_transport.time.sleep'):
            with self.assertRaises(LLMRequestError) as caught:
                request_json(self.endpoint, {}, self.payload, 50)
        self.assertEqual(caught.exception.attempts, 3)
        self.assertEqual(caught.exception.status_code, 503)

    def test_retry_after_larger_than_budget_does_not_retry_early(self):
        with patch('urllib.request.urlopen', side_effect=self.error(429, {'Retry-After': '60'})) as send, patch('okxquant_backend.llm_transport.time.sleep') as sleep:
            with self.assertRaises(LLMRequestError):
                request_json(self.endpoint, {}, self.payload, 5)
        self.assertEqual(send.call_count, 1)
        sleep.assert_not_called()

    def test_network_timeout_retries_but_certificate_errors_do_not(self):
        with patch('urllib.request.urlopen', side_effect=[urllib.error.URLError(TimeoutError()), self.response()]), patch('okxquant_backend.llm_transport.time.sleep'):
            self.assertEqual(request_json(self.endpoint, {}, self.payload, 50)[3], 2)
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError(ssl.SSLCertVerificationError('certificate'))) as send:
            with self.assertRaises(LLMRequestError):
                request_json(self.endpoint, {}, self.payload, 50)
        self.assertEqual(send.call_count, 1)

    def test_invalid_success_payload_fails_without_disclosing_content(self):
        with patch('urllib.request.urlopen', return_value=self.response({'error': 'PRIVATE-ERROR-BODY'})):
            with self.assertRaises(LLMRequestError) as caught:
                request_json(self.endpoint, {}, self.payload, 50)
        self.assertNotIn('PRIVATE-ERROR-BODY', str(caught.exception))

    def test_connection_probe_does_not_report_empty_content_as_success(self):
        for content in ('', None):
            with patch('urllib.request.urlopen', return_value=self.response({'choices': [{'message': {'content': content}}]})):
                result = connection_probe('https://model.example', 'test-key', 'test-model')
            self.assertFalse(result['ok'])
            self.assertEqual(result['error_category'], 'empty_model_output')


    def provider_error(self, code, headers=None, message='PRIVATE-ERROR-BODY'):
        body = json.dumps({'error': {'code': code, 'message': message}}).encode()
        return urllib.error.HTTPError(self.endpoint, 503, 'upstream', headers or {}, io.BytesIO(body))

    def test_load_shedding_waits_for_new_sample_without_changing_payload(self):
        errors = [self.provider_error('system_cpu_overloaded') for _ in range(2)]
        with patch('urllib.request.urlopen', side_effect=[*errors, self.response()]) as send, patch('okxquant_backend.llm_transport.time.sleep') as sleep:
            self.assertEqual(request_json(self.endpoint, {}, self.payload, 50)[3], 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [6.0, 12.0])
        self.assertTrue(all(json.loads(c.args[0].data) == self.payload for c in send.call_args_list))

    def test_load_shedding_respects_retry_after_and_remaining_budget(self):
        with patch('urllib.request.urlopen', side_effect=[self.provider_error('system_cpu_overloaded', {'Retry-After': '20'}), self.response()]), patch('okxquant_backend.llm_transport.time.sleep') as sleep:
            request_json(self.endpoint, {}, self.payload, 50)
            sleep.assert_called_once_with(20)
        with patch('urllib.request.urlopen', side_effect=self.provider_error('system_memory_overloaded')) as send, patch('okxquant_backend.llm_transport.time.sleep') as sleep:
            with self.assertRaises(LLMRequestError) as caught:
                request_json(self.endpoint, {}, self.payload, 5)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(caught.exception.provider_code, 'system_memory_overloaded')
        sleep.assert_not_called()

    def test_known_error_and_request_id_are_retained_without_body(self):
        request_id = '202609052300190809979208268d9d6FvHwDHBK'
        with patch('urllib.request.urlopen', side_effect=self.provider_error('system_cpu_overloaded', {'X-Oneapi-Request-Id': request_id})):
            with self.assertLogs('okxquant_backend.llm_transport', level='WARNING') as logs:
                with self.assertRaises(LLMRequestError) as caught:
                    request_json(self.endpoint, {}, self.payload, 50, max_attempts=1)
        self.assertEqual(caught.exception.request_id, request_id)
        self.assertIn('CPU', caught.exception.provider_reason)
        self.assertIn('CPU', str(caught.exception))
        self.assertIn(request_id, '\n'.join(logs.output))
        self.assertNotIn('PRIVATE-ERROR-BODY', str(caught.exception) + '\n'.join(logs.output))

    def test_untrusted_metadata_is_not_logged_or_used_for_backoff(self):
        with patch('urllib.request.urlopen', side_effect=[self.provider_error('system_PRIVATE-ERROR-BODY', {'X-Oneapi-Request-Id': 'sk-private-key'}), self.response()]), patch('okxquant_backend.llm_transport.time.sleep') as sleep:
            with self.assertLogs('okxquant_backend.llm_transport', level='WARNING') as logs:
                request_json(self.endpoint, {}, self.payload, 50)
        sleep.assert_called_once_with(0.5)
        self.assertNotIn('PRIVATE-ERROR-BODY', '\n'.join(logs.output))
        self.assertNotIn('sk-private-key', '\n'.join(logs.output))

    def test_unreadable_error_body_keeps_original_status_and_closes_response(self):
        response = self.error(503)
        response.read = MagicMock(side_effect=TimeoutError())
        response.close = MagicMock()
        with patch('urllib.request.urlopen', side_effect=response):
            with self.assertRaises(LLMRequestError) as caught:
                request_json(self.endpoint, {}, self.payload, 50, max_attempts=1)
        response.read.assert_called_once_with(8192)
        response.close.assert_called_once()
        self.assertEqual(caught.exception.status_code, 503)

    def test_probe_exposes_safe_provider_reason(self):
        with patch('urllib.request.urlopen', side_effect=self.provider_error('system_disk_overloaded')):
            result = connection_probe('https://model.example', 'test-key', 'test-model', timeout=5)
        self.assertFalse(result['ok'])
        self.assertEqual(result['provider_error_code'], 'system_disk_overloaded')
        self.assertNotIn('PRIVATE-ERROR-BODY', json.dumps(result))

    def test_retry_after_parsing_and_invalid_limits(self):
        self.assertEqual(retry_delay('2', 1), 2)
        self.assertEqual(retry_delay('invalid', 2), 1)
        with self.assertRaises(ValueError):
            request_json(self.endpoint, {}, {}, 0)
        with self.assertRaises(ValueError):
            request_json(self.endpoint, {}, {}, 30, 4)


if __name__ == '__main__':
    unittest.main()
