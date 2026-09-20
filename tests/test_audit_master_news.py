from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from scripts import news_sentiment_harvester as n

class NewsBreakerAudit(unittest.TestCase):
    def test_protocol_marketing_declaration_does_not_stop_entries(self):
        self.assertIsNone(n.black_swan_reason('Uniswap向Curve宣战：StablePair Hook要重塑稳定币交易市场？'))
    def test_explicit_negation_does_not_trigger(self):
        self.assertIsNone(n.black_swan_reason('OKX辟谣：没有停止提币，所有服务正常'))
    def test_denial_in_other_sentence_does_not_mask_real_event(self):
        self.assertIsNotNone(n.black_swan_reason('USDT否认严重脱锚。OKX宣布暂停全部提现'))
    def test_existing_real_exchange_event_still_triggers(self):
        self.assertIsNotNone(n.black_swan_reason('OKX宣布暂停全部提现'))
    def test_real_state_declaration_still_detected(self):
        self.assertIsNotNone(n.black_swan_reason('俄罗斯正式向乌克兰宣战'))
    def test_repeated_event_does_not_extend_freeze(self):
        with tempfile.TemporaryDirectory() as d,patch.object(n,'CIRCUIT_BREAKER_FILE',str(Path(d)/'cb.json')),patch('scripts.news_sentiment_harvester.time.time',return_value=1800000000),patch('qq_notifier.notify_circuit_breaker'):
            self.assertTrue(n.trigger_circuit_breaker('OKX宣布暂停全部提现','exchange'))
            before=Path(n.CIRCUIT_BREAKER_FILE).read_bytes()
            with patch('scripts.news_sentiment_harvester.time.time',return_value=1800000300):
                self.assertFalse(n.trigger_circuit_breaker('OKX宣布暂停全部提现','exchange'))
            self.assertEqual(before,Path(n.CIRCUIT_BREAKER_FILE).read_bytes())
    def test_new_event_does_not_erase_old_deduplication(self):
        with tempfile.TemporaryDirectory() as d,patch.object(n,'CIRCUIT_BREAKER_FILE',str(Path(d)/'cb.json')),patch('scripts.news_sentiment_harvester.time.time',return_value=1800000000),patch('qq_notifier.notify_circuit_breaker'):
            n.trigger_circuit_breaker('事件A','exchange');n.trigger_circuit_breaker('事件B','exchange')
            self.assertFalse(n.trigger_circuit_breaker('事件A','exchange'))
            self.assertEqual(len(json.loads(Path(n.CIRCUIT_BREAKER_FILE).read_text())['seen_events']),2)
