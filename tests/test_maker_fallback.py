import unittest
from unittest import mock
from unittest.mock import Mock
from types import SimpleNamespace
from scripts import maker_fallback as m

class MakerFallbackTests(unittest.TestCase):
    def setUp(self): self.env=SimpleNamespace(identity='okx:demo:test')
    def test_only_complete_business_rejection_allows_fallback(self):
        self.assertTrue(m.explicit_exchange_rejection({'returncode':0,'data':[{'sCode':'51000'}]},{'exchange_code':'51000'}))
        self.assertFalse(m.explicit_exchange_rejection({'returncode':-1,'data':None},{'exchange_code':'51000'}))
        self.assertFalse(m.explicit_exchange_rejection({'returncode':0,'data':None},{'exchange_code':'51000'}))
        self.assertFalse(m.explicit_exchange_rejection({'returncode':0,'data':[{'sCode':'0'}]},{'exchange_code':'0'}))
    def test_status_requires_exact_order_and_instrument_identity(self):
        req=Mock(return_value=[{'instId':'BTC-USDT-SWAP','ordId':'123','state':'live'}])
        self.assertEqual(m.status_row(req,self.env,'BTC-USDT-SWAP','123')['state'],'live')
        with self.assertRaises(RuntimeError):m.status_row(Mock(return_value=[{'instId':'ETH-USDT-SWAP','ordId':'123','state':'live'}]),self.env,'BTC-USDT-SWAP','123')
        with self.assertRaises(RuntimeError):m.status_row(Mock(return_value=[]),self.env,'BTC-USDT-SWAP','123')
    def test_cancel_requires_a_list_response(self):
        req=Mock(return_value=[{}]);self.assertEqual(m.cancel(req,self.env,'BTC-USDT-SWAP','123'),[{}])
        with self.assertRaises(RuntimeError):m.cancel(Mock(return_value={}),self.env,'BTC-USDT-SWAP','123')
    def test_wait_is_bounded_single_sleep(self):
        with mock.patch.object(m.time,'sleep') as sleep:m.wait_once()
        sleep.assert_called_once_with(m.WAIT_SECONDS)
    def test_source_contract_contains_post_only_bounded_fallback(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/'scripts'/'ai_factor_trader.py').read_text(encoding='utf8')
        for token in ('post_only','fallback_allowed','maker_fallback','status_row','cancel('):self.assertIn(token,source)
