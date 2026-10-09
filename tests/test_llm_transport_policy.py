import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from okxquant_backend import llm_transport_policy as policy
from okxquant_backend.llm_transport import LLMRequestError


class TransportPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'transport.json'
        self.p=patch.object(policy,'CONFIG_PATH',self.path);self.p.start();self.addCleanup(self.p.stop)
        self.context={'provider_id':'provider-a','model':'model-a','base_url':'https://provider.invalid/v1','api_key':'VERY-PRIVATE-KEY','api_format':'openai_chat','reasoning_effort':'high','reasoning_type':'standard_effort'}
        self.current=patch.object(policy,'selected_context',side_effect=lambda *args:dict(self.context));self.current.start();self.addCleanup(self.current.stop)

    def test_unknown_auto_is_compatibility_not_claimed_stream_support(self):
        state=policy.status(self.context)
        self.assertEqual(state['effective_mode'],'json');self.assertEqual(state['capabilities']['stream']['status'],'unknown')
        self.assertTrue(state['warnings']);self.assertFalse(self.path.exists())

    def test_verified_stream_selects_stream_and_saved_state_contains_no_credentials(self):
        policy.record_verification(self.context,'stream',True,{'completion_seen':True,'heartbeat_count':3,'body':'SECRET BODY','url':'PRIVATE URL'})
        state=policy.status(self.context);self.assertEqual(state['effective_mode'],'stream')
        self.assertEqual(state['capabilities']['stream']['diagnostics'],{'heartbeat_count':3,'completion_seen':True})
        raw=self.path.read_text();self.assertNotIn('VERY-PRIVATE-KEY',raw);self.assertNotIn('provider.invalid',raw)
        self.assertNotIn('SECRET BODY',raw);self.assertEqual(os.stat(self.path).st_mode&0o777,0o600)

    def test_every_connection_boundary_invalidates_receipt(self):
        policy.record_verification(self.context,'stream',True,{})
        for key in ['provider_id','model','base_url','api_key','api_format','reasoning_effort','reasoning_type']:
            other={**self.context,key:'different'}
            self.assertEqual(policy.capability_state(other)['stream']['status'],'unknown',key)

    def test_late_probe_cannot_stamp_new_credentials(self):
        old=dict(self.context);self.context['api_key']='ROTATED-PRIVATE-KEY'
        with self.assertRaises(ValueError):policy.record_verification(old,'stream',True,{})
        self.assertFalse(self.path.exists())

    def test_explicit_modes_do_not_depend_on_verification_and_do_not_change_connection(self):
        for mode in ['stream','json','auto']:
            policy.save_policy('provider-a',{'mode':mode})
            state=policy.status(self.context)
            self.assertEqual(state['effective_mode'],'json' if mode=='auto' else mode)
            self.assertEqual(self.context['reasoning_effort'],'high')

    def test_temporary_failed_probe_does_not_revoke_verified_protocol(self):
        policy.record_verification(self.context,'stream',True,{'completion_seen':True})
        policy.record_verification(self.context,'stream',False,{},'idle_timeout')
        state=policy.status(self.context)
        self.assertEqual(state['effective_mode'],'stream')
        self.assertEqual(state['capabilities']['stream']['last_probe_status'],'failed')

    def test_only_explicit_unsupported_is_classified_unsupported(self):
        policy.record_verification(self.context,'stream',False,{},'http_error')
        self.assertEqual(policy.capability_state(self.context)['stream']['status'],'failed')
        policy.record_verification(self.context,'stream',False,{},'stream_unsupported')
        self.assertEqual(policy.capability_state(self.context)['stream']['status'],'unsupported')

    def test_invalid_bounds_and_unknown_fields_cannot_be_saved(self):
        for value in [{'mode':'magic'},{'first_byte_timeout_seconds':601},{'total_timeout_seconds':20},{'idle_timeout_seconds':float('nan')},{'connect_timeout_seconds':True},{'max_response_bytes':100000.5},{'verified':True}]:
            with self.assertRaises(ValueError,msg=str(value)):policy.save_policy('provider-a',value)
        self.assertFalse(self.path.exists())

    def test_corrupt_config_fails_closed_not_silent_default(self):
        self.path.write_text('{bad')
        with self.assertRaises(LLMRequestError) as caught:policy.status(self.context)
        self.assertEqual(caught.exception.category,'configuration_error')

    def test_provider_policy_does_not_bleed_across_providers(self):
        policy.save_policy('provider-a',{'mode':'stream'})
        self.assertEqual(policy.policy_for('provider-b')['mode'],'auto')

    def test_call_override_resolves_its_own_provider_not_active_provider(self):
        from okxquant_backend import llm_manager
        active={'provider_id':'provider-a','model':'shared','base_url':'https://a.invalid','api_key':'a','api_format':'openai_chat'}
        models=[{'id':'shared','provider_id':'provider-b','base_url':'https://b.invalid','api_key':'b','api_format':'claude_messages','reasoning_type':'adaptive'}]
        with patch.object(llm_manager,'load_llm_config',return_value={'models':models}):
            actual=policy.resolve_context('shared','https://b.invalid','b','claude_messages','high',active)
        self.assertEqual(actual['provider_id'],'provider-b');self.assertEqual(actual['reasoning_type'],'adaptive')

    def test_explicit_endpoint_override_never_borrows_active_secret(self):
        from okxquant_backend import llm_manager
        with patch.object(llm_manager,'get_active_llm_runtime',return_value={'model':'m','base_url':'https://a.invalid','api_key':'PRIVATE'}),patch.object(llm_manager,'request_json') as request:
            with self.assertRaises(LLMRequestError):llm_manager.execute_llm_request([],base_url='https://b.invalid')
        request.assert_not_called()

    def test_frozen_reasoning_type_is_not_borrowed_from_active_alias(self):
        from okxquant_backend import llm_manager
        active={**self.context,'reasoning_type':'none'}
        reply={'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}
        with patch.object(llm_manager,'get_active_llm_runtime',return_value=active),patch.object(llm_manager,'request_json',return_value=(reply,200,1,1)) as send:
            llm_manager.execute_llm_request([],model='m',base_url=active['base_url'],api_key=active['api_key'],api_format='openai_chat',reasoning_effort='high',reasoning_type='standard_effort',transport_policy={**policy.DEFAULT_POLICY,'mode':'stream'})
        self.assertEqual(send.call_args.args[2]['reasoning_effort'],'high')

    def test_trading_runtime_keeps_keyless_connection_atomic(self):
        import ast
        path=Path(__file__).resolve().parents[1]/'scripts'/'ai_brain_trader.py'
        tree=ast.parse(path.read_text(encoding='utf8'))
        assignments=[node for node in ast.walk(tree) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'base_url','api_key'} for t in node.targets) and 'active_llm.get' in ast.unparse(node)]
        self.assertEqual(len(assignments),2)
        context={'active_llm':{'base_url':'https://new.invalid','api_key':''},'base_url':'https://old.invalid','api_key':'OLD-PRIVATE-KEY'}
        exec(compile(ast.fix_missing_locations(ast.Module(body=assignments,type_ignores=[])),str(path),'exec'),context)
        self.assertEqual(context['base_url'],'https://new.invalid');self.assertEqual(context['api_key'],'')

    def test_empty_explicit_endpoint_does_not_inherit_active_connection(self):
        from okxquant_backend import llm_manager
        with patch.object(llm_manager,'request_json') as send:
            with self.assertRaises(LLMRequestError):llm_manager.execute_llm_request([],base_url='',api_key='')
        send.assert_not_called()

    def test_council_model_binding_is_provider_qualified(self):
        from okxquant_backend import council_manager,llm_manager
        models=[{'id':'shared','provider_id':pid,'base_url':f'https://{pid}.invalid','api_key':pid,'api_format':'openai_chat','reasoning_effort':'high','reasoning_type':'standard_effort'} for pid in ['a','b']]
        role={'name':'fixture','prompt':'','model_id':'shared','provider_id':'b'}
        with patch.object(llm_manager,'load_llm_config',return_value={'models':models}),patch.object(llm_manager,'execute_llm_request',return_value=('proposal','',{},1)) as send:
            result=council_manager._call_single_trader('r',role,'fixture market','fixture system')
        self.assertEqual(result['status'],'ok');self.assertEqual(send.call_args.kwargs['api_key'],'b')
        self.assertEqual(send.call_args.kwargs['base_url'],'https://b.invalid')
        self.assertEqual(send.call_args.kwargs['reasoning_type'],'standard_effort')

    def test_ambiguous_or_missing_council_model_never_falls_back(self):
        from okxquant_backend import council_manager,llm_manager
        for models in [[],[{'id':'shared','provider_id':'a'},{'id':'shared','provider_id':'b'}]]:
            with patch.object(llm_manager,'load_llm_config',return_value={'models':models}),patch.object(llm_manager,'execute_llm_request') as send:
                with self.assertRaises(ValueError):council_manager._call_single_trader('r',{'prompt':'','model_id':'shared'},'fixture','system')
                send.assert_not_called()

    def test_explicit_provider_policy_wins_over_identical_active_alias(self):
        from okxquant_backend import llm_manager
        policy.save_policy('provider-a',{'mode':'json'})
        policy.save_policy('provider-b',{'mode':'stream'})
        self.context.update(provider_id='provider-b')
        active={**self.context,'provider_id':'provider-a'}
        reply={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}
        with patch.object(llm_manager,'get_active_llm_runtime',return_value=active),patch.object(llm_manager,'request_json',return_value=(reply,200,1,1)) as send:
            llm_manager.execute_llm_request([],provider_id='provider-b',model=self.context['model'])
        self.assertEqual(send.call_args.kwargs['transport_policy']['mode'],'stream')

    def test_provider_bound_call_cannot_use_a_different_endpoint_or_key(self):
        from okxquant_backend import llm_manager
        with patch.object(llm_manager,'get_active_llm_runtime',return_value=self.context),patch.object(llm_manager,'request_json') as send:
            with self.assertRaises(LLMRequestError):llm_manager.execute_llm_request([],provider_id='provider-a',model=self.context['model'],base_url='https://other.invalid',api_key='OTHER')
        send.assert_not_called()
