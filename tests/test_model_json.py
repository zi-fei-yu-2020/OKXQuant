import unittest
from unittest.mock import Mock
from scripts.model_json import *

class ModelJsonTests(unittest.TestCase):
    def test_safe_text_envelopes(self):
        for text in ('{"a":1}','\ufeff{"a":1}','```JSON\n{"a":1}\n```'):
            self.assertEqual(parse_response(text),{'a':1})
        self.assertEqual(text_content([{'type':'text','text':'{"a":'},{'type':'text','text':'1}'}]),'{"a":1}')
        for bad in (42,{'text':'{}'},[{'type':'image','text':'{}'}]):
            with self.assertRaises(ContractError):text_content(bad)

    def test_no_ambiguous_json_repair_or_partial_instructions(self):
        for text in ('{"a":1,}', '{"a":', '{"a":1,"a":2}', '{"a":NaN}', '[]', 'here is JSON: {}', '{} {}', '{"a":1e999}'):
            with self.subTest(text=text):
                with self.assertRaises(ContractError):parse_response(text)

    def test_precise_error_without_response_leak(self):
        with self.assertRaises(ContractError) as cm:parse_response('{"private-example":')
        self.assertIn('第1行',str(cm.exception));self.assertNotIn('private-example',str(cm.exception))

    def test_one_fresh_retry_only_and_report_hash(self):
        retry=Mock(return_value='{"decisions":{}}'); report={}
        result=decode_with_regeneration(lambda:'{"decisions":',retry,report=report)
        self.assertEqual(result,{'decisions':{}});retry.assert_called_once_with(timeout=20.0,max_attempts=1)
        self.assertEqual(report['status'],'regenerated');self.assertIn('sha256',report['failures'][0])
        retry.reset_mock()
        decode_with_regeneration(lambda:'{}',retry);retry.assert_not_called()

    def test_failure_is_closed_and_no_transport_retry(self):
        retry=Mock(return_value='{"bad":');report={}
        with self.assertRaises(ContractError):decode_with_regeneration(lambda:'not json',retry,report=report)
        self.assertEqual(retry.call_count,1);self.assertEqual(report['status'],'rejected')
        retry.reset_mock()
        with self.assertRaises(TimeoutError):decode_with_regeneration(Mock(side_effect=TimeoutError()),retry)
        retry.assert_not_called()

    def test_elapsed_budget_rejects_late_response(self):
        with self.assertRaises(ContractError):
            decode_with_regeneration(lambda:'bad',lambda **kw:'{}',clock=Mock(side_effect=[0,21,21]))

    def test_provider_truncation_even_with_valid_partial_json(self):
        for reason in ('length','content_filter','tool_calls'):
            with self.assertRaises(ContractError):verify_completion({'choices':[{'finish_reason':reason,'message':{'content':'{}'}}]},'openai_chat')
        verify_completion({'choices':[{'finish_reason':'stop'}]},'openai_chat')
        for proto,response in [('claude_messages',{'stop_reason':'max_tokens'}),('openai_responses',{'status':'incomplete'})]:
            with self.assertRaises(ContractError):verify_completion(response,proto)

    def test_regeneration_is_not_contract_approval(self):
        from scripts.trading_prompt import validate_response
        result=decode_with_regeneration(lambda:'bad',lambda **kw:'{"decisions":{"FAKE":{"action":"BUY_LONG"}}}')
        with self.assertRaises(ContractError):validate_response(result,[])

if __name__=='__main__':unittest.main()
