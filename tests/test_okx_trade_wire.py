"""Signed REST body shape and fail-closed batch errors; no live network."""
import base64
import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch
from scripts.okx_runtime import OKXEnvironment
from okxquant_backend.okx_trade_service import _request


class OKXTradeWireTests(unittest.TestCase):
    def setUp(self):
        self.env = OKXEnvironment('demo', 'test-key', 'test-secret', 'test-pass')

    def response(self, payload):
        response = MagicMock()
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__.return_value = response
        return response

    def test_batch_algo_cancel_signs_exact_array_and_demo_header(self):
        stamp = '2026-09-05T00:00:00.000Z'
        payload = [{'instId': 'BTC-USDT-SWAP', 'algoId': 'test-algo'}]
        with patch('okxquant_backend.okx_trade_service._timestamp', return_value=stamp), patch('urllib.request.urlopen', return_value=self.response({'code': '0', 'data': [{'sCode': '0', 'algoId': 'test-algo'}]})) as send:
            _request('POST', '/api/v5/trade/cancel-algos', payload, self.env)
        request = send.call_args.args[0]
        self.assertEqual(json.loads(request.data), payload)
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers['x-simulated-trading'], '1')
        expected = base64.b64encode(hmac.new(b'test-secret', (stamp+'POST/api/v5/trade/cancel-algos').encode()+request.data, hashlib.sha256).digest()).decode()
        self.assertEqual(headers['ok-access-sign'], expected)

    def test_batch_item_error_is_not_reported_success(self):
        with patch('urllib.request.urlopen', return_value=self.response({'code': '0', 'data': [{'sCode': '51400', 'sMsg': 'not found'}]})):
            with self.assertRaisesRegex(RuntimeError, '51400'):
                _request('POST', '/api/v5/trade/cancel-algos', [{'algoId': 'missing'}], self.env)

    def test_invalid_batch_or_get_array_never_sends(self):
        with patch('urllib.request.urlopen') as send:
            for method, payload in [('GET', [{}]), ('POST', []), ('POST', ['invalid'])]:
                with self.assertRaises(ValueError):
                    _request(method, '/api/v5/trade/cancel-algos', payload, self.env)
        send.assert_not_called()

    def test_dictionary_false_and_zero_values_are_preserved(self):
        with patch('urllib.request.urlopen', return_value=self.response({'code': '0', 'data': []})) as send:
            _request('POST', '/api/v5/trade/order', {'reduceOnly': False, 'zero': 0, 'empty': ''}, self.env)
        self.assertEqual(json.loads(send.call_args.args[0].data), {'reduceOnly': False, 'zero': 0})


if __name__ == '__main__':
    unittest.main()
