import json
import unittest

from okxquant_backend.llm_stream import SSEDecoder, StreamAssembler, StreamProtocolError
from scripts.model_json import verify_completion


def frame(event, obj):
    return event, json.dumps(obj, ensure_ascii=False)


class SSEDecoderTests(unittest.TestCase):
    def test_arbitrary_splits_utf8_multiline_and_crlf(self):
        raw = 'event: message\r\ndata: ??\r\ndata: ??\r\n\r\n'.encode()
        decoder = SSEDecoder()
        out = []
        for b in raw:
            out.extend(decoder.feed(bytes([b])))
        out.extend(decoder.finish())
        self.assertEqual(out, [('message', '??\n??')])

    def test_cr_lf_split_is_single_terminator(self):
        d = SSEDecoder()
        self.assertEqual(d.feed(b'data: x\r'), [])
        self.assertEqual(d.feed(b'\n\r'), [])
        self.assertEqual(d.feed(b'\n'), [('message', 'x')])

    def test_heartbeat_comment_and_ping(self):
        d = SSEDecoder()
        self.assertEqual(d.feed(b': keepalive\n\n'), [])
        self.assertEqual(d.heartbeat_count, 1)
        self.assertEqual(d.feed(b'event: ping\ndata: {}\n\n'), [('ping', '{}')])
        self.assertEqual(d.heartbeat_count, 2)

    def test_eof_does_not_dispatch_incomplete_event(self):
        d = SSEDecoder()
        self.assertEqual(d.feed(b'data: {"ok":true}'), [])
        self.assertEqual(d.finish(), [])

    def test_utf8_split_and_invalid_utf8(self):
        d = SSEDecoder()
        self.assertEqual(d.feed(b'data: \xe4'), [])
        self.assertEqual(d.feed(b'\xbd\xa0\n\n'), [('message', '\u4f60')])
        d = SSEDecoder()
        with self.assertRaisesRegex(StreamProtocolError, 'invalid_utf8'):
            d.feed(b'data: \xff\n\n')

    def test_limits(self):
        with self.assertRaisesRegex(StreamProtocolError, 'event_too_large'):
            SSEDecoder(max_event_bytes=5).feed(b'data: abc\n\n')
        d = SSEDecoder(max_response_bytes=4)
        with self.assertRaisesRegex(StreamProtocolError, 'response_too_large'):
            d.feed(b'data:')
        with self.assertRaisesRegex(StreamProtocolError, 'event_too_large'):
            SSEDecoder(max_event_bytes=4).feed(b':1234\n')


class AssemblerTests(unittest.TestCase):
    def test_chat_content_reasoning_identity_usage_and_done(self):
        a = StreamAssembler('openai_chat')
        a.feed(*frame('message', {'id':'x','model':'m','choices':[{'index':0,'delta':{'role':'assistant','reasoning_content':'think '}}]}))
        a.feed(*frame('message', {'choices':[{'index':0,'delta':{'content':'hello'}}]}))
        a.feed(*frame('message', {'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}))
        a.feed('message', '[DONE]')
        r = a.result()
        self.assertEqual(r['choices'][0]['message']['content'], 'hello')
        self.assertEqual(r['choices'][0]['message']['reasoning_content'], 'think ')
        self.assertEqual(r['id'], 'x')
        verify_completion(r, 'openai_chat')
        self.assertTrue(a.complete)

    def test_chat_usage_optional_and_usage_only_final_chunk(self):
        a = StreamAssembler('openai_chat')
        a.feed(*frame('message', {'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}))
        a.feed(*frame('message', {'choices':[], 'usage':{'total_tokens':7}}))
        a.feed('message', '[DONE]')
        self.assertEqual(a.result()['usage']['total_tokens'], 7)

    def test_chat_rejects_missing_done_bad_finish_tool_refusal_and_late_data(self):
        for events in [
            [("message", '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}')],
            [("message", '{"choices":[{"index":0,"delta":{},"finish_reason":"length"}]}')],
            [("message", '{"choices":[{"index":0,"delta":{"tool_calls":[{"id":"call"}]}}]}')],
            [("message", '{"choices":[{"index":0,"delta":{"refusal":"no"}}]}')],
            [("message", '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}'), ('message','[DONE]'), ('message','{}')],
        ]:
            a=StreamAssembler('openai_chat')
            with self.assertRaises(StreamProtocolError):
                for e in events: a.feed(*e)
                a.result()

    def test_chat_error_event_cannot_fake_done(self):
        a=StreamAssembler('openai_chat')
        a.feed(*frame('message', {'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}))
        with self.assertRaises(StreamProtocolError): a.feed('error', '[DONE]')

    def test_chat_done_alone_and_json_eof_are_incomplete(self):
        a=StreamAssembler('openai_chat')
        with self.assertRaises(StreamProtocolError): a.feed('message','[DONE]')
        b=StreamAssembler('openai_chat'); b.feed(*frame('message', {'choices':[{'index':0,'delta':{'content':'{'}}]}))
        with self.assertRaises(StreamProtocolError): b.result()

    def test_responses_completion_and_delta_reconciliation(self):
        a=StreamAssembler('openai_responses')
        a.feed(*frame('response.output_text.delta', {'type':'response.output_text.delta','delta':'hi'}))
        final={'id':'r','model':'m','status':'completed','output':[{'type':'message','status':'completed','content':[{'type':'output_text','text':'hi'}]}]}
        a.feed(*frame('response.completed', {'type':'response.completed','response':final}))
        r=a.result(); self.assertEqual(r['output_text'],'hi'); verify_completion(r,'openai_responses')

    def test_responses_done_event_not_completion_and_failures(self):
        a=StreamAssembler('openai_responses'); a.feed(*frame('output_text.done', {'type':'response.output_text.done','text':'hi'}))
        with self.assertRaises(StreamProtocolError): a.result()
        for typ, response in [('response.completed', {'status':'incomplete'}), ('response.completed', {'status':'completed','output':[{'type':'function_call'}]}), ('response.failed', {})]:
            b=StreamAssembler('openai_responses')
            with self.assertRaises(StreamProtocolError): b.feed(*frame(typ, {'type':typ,'response':response}))

    def test_responses_mismatched_final_text_rejected(self):
        a=StreamAssembler('openai_responses')
        a.feed(*frame('response.output_text.delta', {'type':'response.output_text.delta','delta':'a'}))
        with self.assertRaises(StreamProtocolError): a.feed(*frame('response.completed', {'type':'response.completed','response':{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'b'}]}]}}))

    def test_claude_indexed_text_thinking_usage_and_terminal(self):
        a=StreamAssembler('claude_messages')
        a.feed('ping', '{}')
        a.feed(*frame('message_start', {'type':'message_start','message':{'id':'c','model':'m','usage':{'input_tokens':2}}}))
        a.feed(*frame('ping', {'type':'ping'}))
        a.feed(*frame('content_block_start', {'type':'content_block_start','index':0,'content_block':{'type':'text','text':'A'}}))
        a.feed(*frame('content_block_delta', {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'B'}}))
        a.feed(*frame('content_block_stop', {'type':'content_block_stop','index':0}))
        a.feed(*frame('content_block_start', {'type':'content_block_start','index':1,'content_block':{'type':'thinking','thinking':'why'}}))
        a.feed(*frame('content_block_stop', {'type':'content_block_stop','index':1}))
        a.feed(*frame('message_delta', {'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':3}}))
        a.feed(*frame('message_stop', {'type':'message_stop'}))
        r=a.result(); verify_completion(r,'claude_messages')
        self.assertEqual([x['type'] for x in r['content']], ['text','thinking'])
        self.assertEqual(r['usage'], {'input_tokens':2,'output_tokens':3})

    def test_claude_rejects_tool_refusal_and_truncation(self):
        a=StreamAssembler('claude_messages')
        a.feed(*frame('message_start', {'message':{}}))
        with self.assertRaises(StreamProtocolError): a.feed(*frame('content_block_start', {'type':'content_block_start','index':0,'content_block':{'type':'tool_use'}}))
        b=StreamAssembler('claude_messages')
        b.feed(*frame('message_start', {'message':{}}))
        with self.assertRaises(StreamProtocolError):
            b.feed(*frame('message_delta', {'type':'message_delta','delta':{'stop_reason':'refusal'}}))
        c=StreamAssembler('claude_messages')
        c.feed(*frame('message_start', {'message':{}}))
        c.feed(*frame('message_delta', {'type':'message_delta','delta':{'stop_reason':'end_turn'}}))
        with self.assertRaises(StreamProtocolError): c.result()

    def test_protocol_errors_are_content_free(self):
        a=StreamAssembler('openai_chat')
        with self.assertRaises(StreamProtocolError) as caught: a.feed('error','secret prompt')
        self.assertNotIn('secret', str(caught.exception))



class IntegrationHardeningTests(unittest.TestCase):
    @staticmethod
    def chat_finished():
        a = StreamAssembler('openai_chat')
        a.feed(*frame('message', {
            'id': 'chat-1', 'model': 'model-1',
            'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
        }))
        return a

    @staticmethod
    def claude_started():
        a = StreamAssembler('claude_messages')
        a.feed(*frame('message_start', {'type': 'message_start', 'message': {}}))
        return a

    def test_chat_late_usage_errors_and_refusals_always_rejected(self):
        for extra in ({'error': {'message': 'private'}}, {'error': {}},
                      {'type': 'error'}, {'refusal': 'private'},
                      {'tool_calls': [{'id': 'call'}]}):
            with self.subTest(extra=tuple(extra)):
                a = self.chat_finished()
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame('message', {'choices': [], 'usage': {}, **extra}))
                self.assertFalse(a.complete)
                with self.assertRaises(StreamProtocolError):
                    a.feed('message', '[DONE]')
                with self.assertRaises(StreamProtocolError):
                    a.result()

    def test_chat_matching_identity_on_usage_is_preserved(self):
        a = self.chat_finished()
        a.feed(*frame('message', {'id': 'chat-1', 'model': 'model-1',
                                 'choices': [], 'usage': {'total_tokens': 3}}))
        a.feed('message', '[DONE]')
        self.assertEqual(a.result()['id'], 'chat-1')
        self.assertEqual(a.result()['model'], 'model-1')
        self.assertEqual(a.result()['usage'], {'total_tokens': 3})

    def test_chat_identity_contradictions_in_delta_and_usage_rejected(self):
        for key in ('id', 'model'):
            for usage_only in (False, True):
                with self.subTest(key=key, usage_only=usage_only):
                    a = StreamAssembler('openai_chat')
                    a.feed(*frame('message', {'id': 'chat-1', 'model': 'model-1',
                                             'choices': [{'index': 0, 'delta': {}}]}))
                    if usage_only:
                        a.feed(*frame('message', {'choices': [{'index': 0, 'delta': {},
                                                              'finish_reason': 'stop'}]}))
                    obj = {'choices': [], 'usage': {}} if usage_only else {
                        'choices': [{'index': 0, 'delta': {'content': 'x'}}]}
                    obj[key] = 'different'
                    with self.assertRaisesRegex(StreamProtocolError, 'identity_mismatch'):
                        a.feed(*frame('message', obj))

    def test_chat_supplied_identity_must_be_nonempty_string(self):
        for key in ('id', 'model'):
            for value in ('', ' ', None, 3):
                with self.subTest(key=key, value=value):
                    a = StreamAssembler('openai_chat')
                    with self.assertRaises(StreamProtocolError):
                        a.feed(*frame('message', {key: value, 'choices': []}))

    def test_chat_null_and_empty_tool_calls_are_not_attempts(self):
        for calls in (None, []):
            with self.subTest(calls=calls):
                a = StreamAssembler('openai_chat')
                a.feed(*frame('message', {'choices': [{'index': 0,
                    'delta': {'content': 'ok', 'tool_calls': calls}, 'finish_reason': 'stop'}]}))
                a.feed('message', '[DONE]')
                self.assertEqual(a.result()['choices'][0]['message']['content'], 'ok')
                verify_completion(a.result(), 'openai_chat')

    def test_chat_actual_and_malformed_tool_calls_rejected(self):
        for calls in ([{}], [{'id': 'call'}], {}, '', False, 0, 'call'):
            with self.subTest(calls=calls):
                a = StreamAssembler('openai_chat')
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame('message', {'choices': [{'index': 0,
                                                          'delta': {'tool_calls': calls}}]}))

    def test_responses_unsafe_payload_types_under_generic_event(self):
        types = ('error', 'response.error', 'response.failed', 'response.incomplete',
                 'response.refusal.delta', 'response.refusal.done',
                 'response.function_call_arguments.delta', 'response.tool_call.delta',
                 'response.web_search_call.completed', 'response.mcp_call.in_progress')
        for typ in types:
            with self.subTest(typ=typ):
                a = StreamAssembler('openai_responses')
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame('message', {'type': typ}))
                with self.assertRaises(StreamProtocolError):
                    a.result()

    def test_responses_unsafe_sse_type_cannot_be_hidden_by_payload_type(self):
        for typ in ('response.failed', 'response.refusal.delta',
                    'response.function_call_arguments.delta'):
            with self.subTest(typ=typ):
                a = StreamAssembler('openai_responses')
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame(typ, {'type': 'response.output_text.delta', 'delta': 'x'}))

    def test_responses_refusal_and_tool_output_items_rejected(self):
        for typ in ('response.output_item.added', 'response.output_item.done'):
            for item in ({'type': 'function_call'}, {'type': 'message', 'content': [
                    {'type': 'refusal', 'refusal': 'no'}]}):
                with self.subTest(typ=typ, kind=item['type']):
                    a = StreamAssembler('openai_responses')
                    with self.assertRaises(StreamProtocolError):
                        a.feed(*frame('message', {'type': typ, 'item': item}))

    def test_fragmented_leading_bom_utf8_and_crlf(self):
        d = SSEDecoder()
        wire = b'\xef\xbb\xbf' + 'data: \u4f60\u597d\r\n\r\n'.encode()
        events = []
        for byte in wire:
            events.extend(d.feed(bytes([byte])))
        events.extend(d.finish())
        self.assertEqual(events, [('message', '\u4f60\u597d')])

    def test_only_one_leading_bom_is_ignored(self):
        d = SSEDecoder()
        wire = '\ufeffdata: \ufeffvalue\n\ndata: \ufefflater\n\n'.encode()
        self.assertEqual(d.feed(wire), [('message', '\ufeffvalue'), ('message', '\ufefflater')])
        d = SSEDecoder()
        self.assertEqual(d.feed('\ufeff\ufeffdata: not-a-data-field\n\n'.encode()), [])

    def test_eof_does_not_supply_separator_after_bom(self):
        for tail in (b'', b'\n', b'\r\n', b'\r'):
            with self.subTest(tail=tail):
                d = SSEDecoder()
                wire = b'\xef\xbb\xbfdata: done' + tail
                events = []
                for byte in wire:
                    events.extend(d.feed(bytes([byte])))
                events.extend(d.finish())
                self.assertEqual(events, [])
                self.assertEqual(d.finish(), [])
        d = SSEDecoder()
        self.assertEqual(d.feed(b'data: done\r\r'), [])
        self.assertEqual(d.finish(), [('message', 'done')])

    def test_eof_rejects_incomplete_bom_and_utf8(self):
        for wire in (b'\xef', b'\xef\xbb', b'\xef\xbb\xbfdata: \xe4\xbd'):
            with self.subTest(wire=wire):
                d = SSEDecoder()
                for byte in wire:
                    d.feed(bytes([byte]))
                with self.assertRaisesRegex(StreamProtocolError, 'invalid_utf8'):
                    d.finish()

    def test_claude_requires_start_before_content_and_terminal(self):
        events = [
            ('content_block_start', {'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
            ('content_block_delta', {'index': 0, 'delta': {'type': 'text_delta', 'text': 'x'}}),
            ('content_block_stop', {'index': 0}),
            ('message_delta', {'delta': {'stop_reason': 'end_turn'}}),
            ('message_stop', {}),
        ]
        for event, obj in events:
            with self.subTest(event=event):
                a = StreamAssembler('claude_messages')
                a.feed('ping', '{}')
                with self.assertRaisesRegex(StreamProtocolError, 'missing_message_start'):
                    a.feed(*frame(event, {'type': event, **obj}))

    def test_claude_duplicate_start_rejected(self):
        a = self.claude_started()
        with self.assertRaisesRegex(StreamProtocolError, 'duplicate_message_start'):
            a.feed(*frame('message_start', {'message': {}}))

    def test_claude_noncontiguous_and_malformed_indices_rejected(self):
        for index in (-1, 1, 2, True, [], '0'):
            with self.subTest(index=index):
                a = self.claude_started()
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame('content_block_start', {'index': index,
                                                        'content_block': {'type': 'text'}}))

    def test_claude_proper_stop_order_enforced(self):
        invalid = [
            ('content_block_start', {'index': 1, 'content_block': {'type': 'text'}}),
            ('content_block_stop', {'index': 1}),
            ('message_delta', {'delta': {'stop_reason': 'end_turn'}}),
            ('message_stop', {}),
        ]
        for event, obj in invalid:
            with self.subTest(event=event):
                a = self.claude_started()
                a.feed(*frame('content_block_start', {'index': 0, 'content_block': {'type': 'text'}}))
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame(event, obj))

    def test_claude_closed_block_and_post_finish_data_rejected(self):
        for event, obj in (
            ('content_block_delta', {'index': 0, 'delta': {'type': 'text_delta', 'text': 'x'}}),
            ('content_block_stop', {'index': 0}),
        ):
            with self.subTest(event=event):
                a = self.claude_started()
                a.feed(*frame('content_block_start', {'index': 0, 'content_block': {'type': 'text'}}))
                a.feed(*frame('content_block_stop', {'index': 0}))
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame(event, obj))
        a = self.claude_started()
        a.feed(*frame('message_delta', {'delta': {'stop_reason': 'end_turn'}}))
        with self.assertRaises(StreamProtocolError):
            a.feed(*frame('content_block_start', {'index': 0, 'content_block': {'type': 'text'}}))



class FinalTextAndThinkingSignatureTests(unittest.TestCase):
    @staticmethod
    def response(text='WAIT'):
        return {'status': 'completed', 'output': [
            {'type': 'message', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': text}]}]}

    @staticmethod
    def thinking_started(kind='thinking'):
        a = StreamAssembler('claude_messages')
        a.feed(*frame('message_start', {'message': {'id': 'signed-message', 'model': 'm'}}))
        a.feed(*frame('content_block_start', {'index': 0,
            'content_block': {'type': kind, kind: ''}}))
        return a

    def test_responses_final_only_conflicting_text_rejected(self):
        a = StreamAssembler('openai_responses')
        response = self.response('WAIT')
        response['output_text'] = 'BUY'
        with self.assertRaisesRegex(StreamProtocolError, 'delta_mismatch'):
            a.feed(*frame('response.completed', {'response': response}))
        self.assertFalse(a.complete)
        with self.assertRaises(StreamProtocolError):
            a.result()

    def test_responses_final_only_matching_or_derived_text(self):
        for supplied in (False, True):
            with self.subTest(supplied=supplied):
                a = StreamAssembler('openai_responses')
                response = self.response()
                if supplied:
                    response['output_text'] = 'WAIT'
                a.feed(*frame('response.completed', {'response': response}))
                self.assertEqual(a.result()['output_text'], 'WAIT')
                verify_completion(a.result(), 'openai_responses')

    def test_responses_missing_canonical_text_rejected(self):
        for output in (None, [], [{'type': 'reasoning', 'summary': []}],
                       [{'type': 'message'}], [{'type': 'message', 'content': None}],
                       [{'type': 'message', 'content': []}]):
            for convenience in (None, '', 'BUY'):
                with self.subTest(output=output, convenience=convenience):
                    a = StreamAssembler('openai_responses')
                    response = {'status': 'completed', 'output_text': convenience}
                    if output is not None:
                        response['output'] = output
                    with self.assertRaises(StreamProtocolError):
                        a.feed(*frame('response.completed', {'response': response}))
                    self.assertFalse(a.complete)

    def test_responses_invalid_or_empty_supplied_text_rejected(self):
        for supplied in (None, '', 0, [], {}):
            with self.subTest(supplied=supplied):
                a = StreamAssembler('openai_responses')
                response = self.response()
                response['output_text'] = supplied
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame('response.completed', {'response': response}))

    def test_responses_empty_accumulated_delta_still_must_match_final(self):
        a = StreamAssembler('openai_responses')
        a.feed(*frame('response.output_text.delta', {'delta': ''}))
        with self.assertRaises(StreamProtocolError):
            a.feed(*frame('response.completed', {'response': self.response()}))

    def test_claude_full_thinking_signature_text_and_terminal(self):
        a = self.thinking_started()
        a.feed(*frame('content_block_delta', {'index': 0,
            'delta': {'type': 'thinking_delta', 'thinking': 'consider risk'}}))
        for signature in ('opaque-', 'signature=='):
            a.feed(*frame('content_block_delta', {'index': 0,
                'delta': {'type': 'signature_delta', 'signature': signature}}))
        a.feed(*frame('content_block_stop', {'index': 0}))
        a.feed(*frame('content_block_start', {'index': 1,
            'content_block': {'type': 'text', 'text': ''}}))
        a.feed(*frame('content_block_delta', {'index': 1,
            'delta': {'type': 'text_delta', 'text': 'WAIT'}}))
        a.feed(*frame('content_block_stop', {'index': 1}))
        a.feed(*frame('message_delta', {'delta': {'stop_reason': 'end_turn'},
                                       'usage': {'output_tokens': 12}}))
        a.feed(*frame('message_stop', {}))
        result = a.result()
        self.assertTrue(a.complete)
        self.assertEqual(result['content'], [
            {'type': 'thinking', 'thinking': 'consider risk', 'signature': 'opaque-signature=='},
            {'type': 'text', 'text': 'WAIT'},
        ])
        self.assertEqual(result['usage'], {'output_tokens': 12})
        verify_completion(result, 'claude_messages')

    def test_claude_signature_wrong_block_and_invalid_value_rejected(self):
        for signature in (None, 0, False, [], {}):
            with self.subTest(signature=signature):
                a = self.thinking_started()
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame('content_block_delta', {'index': 0,
                        'delta': {'type': 'signature_delta', 'signature': signature}}))
        a = self.thinking_started()
        with self.assertRaises(StreamProtocolError):
            a.feed(*frame('content_block_delta', {'index': 0, 'delta': {'type': 'signature_delta'}}))
        a = self.thinking_started('text')
        with self.assertRaises(StreamProtocolError):
            a.feed(*frame('content_block_delta', {'index': 0,
                'delta': {'type': 'signature_delta', 'signature': 'opaque'}}))

    def test_claude_late_signature_rejected_at_each_boundary(self):
        for boundary in ('block_stop', 'message_delta', 'message_stop'):
            with self.subTest(boundary=boundary):
                a = self.thinking_started()
                a.feed(*frame('content_block_stop', {'index': 0}))
                if boundary in ('message_delta', 'message_stop'):
                    a.feed(*frame('message_delta', {'delta': {'stop_reason': 'end_turn'}}))
                if boundary == 'message_stop':
                    a.feed(*frame('message_stop', {}))
                with self.assertRaises(StreamProtocolError):
                    a.feed(*frame('content_block_delta', {'index': 0,
                        'delta': {'type': 'signature_delta', 'signature': 'opaque'}}))
                self.assertFalse(a.complete)
                with self.assertRaises(StreamProtocolError):
                    a.result()


if __name__ == '__main__':
    unittest.main()


class ContentTimingTests(unittest.TestCase):
    def test_role_and_usage_are_not_first_model_content(self):
        from okxquant_backend.llm_stream import StreamAssembler
        stream=StreamAssembler('openai_chat')
        stream.feed('message',json.dumps({'choices':[{'delta':{'role':'assistant','content':''},'finish_reason':None}]}))
        self.assertFalse(stream.content_started)
        stream.feed('message',json.dumps({'choices':[{'delta':{'reasoning_content':'Thinking'},'finish_reason':None}]}))
        self.assertTrue(stream.content_started)


class TerminalTailTests(unittest.TestCase):
    def test_partial_data_or_invalid_utf8_after_terminal_cannot_be_discarded(self):
        from okxquant_backend.llm_stream import SSEDecoder,StreamProtocolError
        for tail in (b'data: {"error":',b'event: error',bytes([255])):
            decoder=SSEDecoder();decoder.feed(b'data: [DONE]'+bytes([10,10])+tail)
            with self.assertRaises(StreamProtocolError):decoder.validate_terminal_tail()
    def test_trailing_whitespace_and_comments_are_not_model_data(self):
        from okxquant_backend.llm_stream import SSEDecoder
        for tail in (b'',b'  ',b': heartbeat'):
            decoder=SSEDecoder();decoder.feed(b'data: [DONE]'+bytes([10,10])+tail);decoder.validate_terminal_tail()


class CommentHeartbeatTests(unittest.TestCase):
    def test_comment_lines_count_without_an_event_separator_and_never_become_text(self):
        from okxquant_backend.llm_stream import SSEDecoder
        decoder=SSEDecoder();self.assertEqual(decoder.feed(b': ping'+bytes([10])),[])
        self.assertEqual(decoder.heartbeat_count,1)
        self.assertEqual(decoder.feed(b'data: value'+bytes([10,10])),[('message','value')])
        self.assertEqual(decoder.heartbeat_count,1)
