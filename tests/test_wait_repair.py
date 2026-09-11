"""Offline guardrails: corrections are WAIT-only and counters do not rewrite history."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from scripts import trading_prompt as contract, wait_audit, wait_repair, wait_counters
from test_trading_prompt_contract import package, response, ref, candidate
from test_wait_audit import valid_wait

INST='BTC-USDT-SWAP'


def macro_wait(p=None):
    p=p or package(); raw=valid_wait()
    side='short' if 'BULL' in p['macro_4h'] else 'long'
    raw['wait_audit'][side]={'code':'macro_constraint','reason':'现有四小时方向规则限制逆向候选，不代表真实持仓',
        'evidence':[ref('/macro_4h',p['macro_4h'])],
        'reconsider':{'conditions':[{'ref':'/macro_4h','op':'ne','value':p['macro_4h']}],'reason':'宏观状态变化后重新研究'}}
    return raw


class ConstraintTests(unittest.TestCase):
    def test_macro_constraint_is_valid_for_the_blocked_direction_without_positions(self):
        for macro in ('4H_MACRO_BULL','4H_MACRO_BEAR'):
            p={**package(),'macro_4h':macro}
            result=contract.validate_response(response(macro_wait(p)),[p])
            self.assertTrue(result['decisions'][INST]['contract_valid'])
            self.assertEqual(result['decisions'][INST]['action'],'WAIT')
    def test_macro_constraint_cannot_veto_range_or_the_allowed_direction(self):
        for macro,side in [('4H_MACRO_RANGE','short'),('4H_MACRO_BULL','long'),('4H_MACRO_BEAR','short'),('4H_MACRO_BULL 4H_MACRO_BEAR','short')]:
            p={**package(),'macro_4h':macro};raw=valid_wait();raw['wait_audit'][side].update(code='macro_constraint',evidence=[ref('/macro_4h',macro)])
            result=contract.validate_response(response(raw),[p])['decisions'][INST]
            self.assertFalse(result['contract_valid']);self.assertIn('宏观限制',result['validation_reason'])
    def test_macro_code_requires_its_actual_macro_reference(self):
        raw=macro_wait();raw['wait_audit']['short']['evidence']=[ref('/price',100)]
        self.assertFalse(contract.validate_response(response(raw),[package()])['decisions'][INST]['contract_valid'])
    def test_position_constraint_requires_actual_nonzero_position_and_valid_pnl(self):
        p=package()
        for position,allowed in [(None,False),({'side':'short','pos':0},False),({'side':'short','pos':1},True),({'side':'long','pos':1,'upl':2},False),({'side':'long','pos':1,'upl':-2},True),({'side':'long','pos':1,'upl':True},False)]:
            facts=contract.facts_for(p,position)
            self.assertEqual(wait_audit.position_constraint_allowed(facts,'long'),allowed)
    def test_input_has_per_side_allowed_codes_and_explicit_previous_review_requirements(self):
        p=package();prior={'required':True,'review_id':'prior-id','changed_refs':['/price']}
        bundle=contract.compose({'id':'test'},{'previous_wait_reviews':{INST:prior}},[p])
        payload=json.JSONDecoder().raw_decode(bundle.user)[0];constraints=payload['wait_constraints'][INST]
        self.assertNotIn('position_constraint',constraints['short']['allowed_codes'])
        self.assertIn('macro_constraint',constraints['short']['allowed_codes'])
        self.assertNotIn('macro_constraint',constraints['long']['allowed_codes'])
        self.assertTrue(constraints['previous_review_required']);self.assertEqual(constraints['previous_review_id'],'prior-id')
        self.assertEqual(constraints['changed_refs'],['/price'])
    def test_optional_previous_review_cannot_reset_anchor_without_verified_evidence(self):
        raw=valid_wait();raw['wait_audit']['previous_review']={'review_id':'invented','reason':'不能凭空声称已经复查','evidence':[ref('/price',100)]}
        result=contract.validate_response(response(raw),[package()])['decisions'][INST]
        self.assertFalse(result['contract_valid'])
        prior={INST:{'required':False,'review_id':'real','changed_refs':['/price']}}
        result=contract.validate_response(response(raw),[package()],previous_wait_reviews=prior)['decisions'][INST]
        self.assertFalse(result['contract_valid'])
        raw['wait_audit']['previous_review']['review_id']='real'
        self.assertTrue(contract.validate_response(response(raw),[package()],previous_wait_reviews=prior)['decisions'][INST]['contract_valid'])
    def test_position_error_identifies_direction_and_correct_category(self):
        raw=valid_wait();raw['wait_audit']['short']['code']='position_constraint'
        row=contract.validate_response(response(raw),[package()])['decisions'][INST]
        self.assertIn('做空',row['validation_reason']);self.assertIn('macro_constraint',row['validation_reason'])


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.p=package();bad=macro_wait();bad['wait_audit']['short']['code']='position_constraint'
        self.raw=response(bad);self.validated=contract.validate_response(self.raw,[self.p])
    def call(self,output=None,**kwargs):
        callback=Mock(return_value=json.dumps({'decisions':{INST:macro_wait()}} if output is None else output,ensure_ascii=False))
        result,report=wait_repair.attempt(self.raw,self.validated,[self.p],request=callback,**kwargs)
        return result,report,callback
    def test_correction_uses_frozen_facts_once_and_preserves_original_data(self):
        before=copy.deepcopy((self.raw,self.validated,self.p));result,report,request=self.call()
        self.assertTrue(result['decisions'][INST]['contract_valid']);self.assertEqual(result['decisions'][INST]['action'],'WAIT')
        self.assertEqual(report['corrected'],[INST]);self.assertEqual(report['status'],'corrected')
        request.assert_called_once();self.assertEqual(request.call_args.kwargs['max_attempts'],1);self.assertEqual(request.call_args.kwargs['timeout'],20)
        payload=json.loads(request.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(payload['frozen_facts'][INST],contract.facts_for(self.p))
        self.assertEqual(report['original_waits'][INST]['value'],self.raw['decisions'][INST])
        self.assertEqual((self.raw,self.validated,self.p),before)
    def test_new_entries_positions_and_cancels_cannot_be_introduced(self):
        invalid=[{'decisions':{INST:candidate()}}, {'decisions':{INST:macro_wait()},'position_management':[{'action':'CLOSE_MARKET'}]},
                 {'decisions':{INST:macro_wait()},'pending_orders_management':[{'action':'CANCEL'}]},
                 {'decisions':{INST:{**macro_wait(),'entry_price':100}}}, {'decisions':{'OTHER':macro_wait()}}]
        for output in invalid:
            result,report,callback=self.call(output)
            self.assertFalse(result['decisions'][INST]['contract_valid']);self.assertEqual(result['decisions'][INST]['action'],'WAIT')
            self.assertEqual(result['position_management'],self.validated['position_management'])
            self.assertEqual(result['pending_orders_management'],self.validated['pending_orders_management'])
            self.assertFalse(report['corrected']);callback.assert_called_once()
    def test_already_valid_other_decisions_are_not_changed(self):
        self.validated['decisions']['OTHER']={'action':'BUY_LONG','contract_valid':True,'decision_status':'entry_candidate','sentinel':'unchanged'}
        before=copy.deepcopy(self.validated['decisions']['OTHER']);result,report,request=self.call()
        self.assertEqual(result['decisions']['OTHER'],before)
        self.assertEqual(report['status'],'deferred_for_actions');request.assert_not_called()
    def test_protective_and_cancel_actions_are_not_delayed_for_correction(self):
        for key,value in [('position_management',[{'action':'UPDATE_SL'}]),('position_management',[{'action':'CLOSE_MARKET'}]),('pending_orders_management',[{'action':'CANCEL'}])]:
            self.validated[key]=value
            result,report,request=self.call()
            self.assertEqual(result[key],value);request.assert_not_called()
            self.assertEqual(report['status'],'deferred_for_actions')
            self.validated[key]=[]
    def test_bad_evidence_and_unfulfilled_prior_review_remain_incomplete(self):
        bad=macro_wait();bad['wait_audit']['short']['evidence'][0]['value']='4H_MACRO_RANGE'
        result,report,_=self.call({'decisions':{INST:bad}})
        self.assertFalse(result['decisions'][INST]['contract_valid']);self.assertTrue(report['remaining_errors'])
        prior={INST:{'required':True,'review_id':'prior','changed_refs':['/price']}}
        result,report,_=self.call(previous_wait_reviews=prior)
        self.assertFalse(result['decisions'][INST]['contract_valid'])
        fixed=macro_wait();fixed['wait_audit']['previous_review']={'review_id':'prior','reason':'价格变化后仍需重新确认其他约束','evidence':[ref('/price',100)]}
        result,_,_=self.call({'decisions':{INST:fixed}},previous_wait_reviews=prior)
        self.assertTrue(result['decisions'][INST]['contract_valid'])
    def test_correction_cannot_claim_insufficient_rr_when_geometry_passes_policy(self):
        fixed=macro_wait();fixed['wait_audit']['long'].update(code='net_rr_below_minimum',reason='该方案净盈亏比不足',geometry={'entry_price':100,'stop_loss_price':99,'take_profit_price':110,'evidence':[ref('/price',100)]})
        result,_,_=self.call({'decisions':{INST:fixed}})
        self.assertFalse(result['decisions'][INST]['contract_valid'])
    def test_missing_non_wait_and_corrupt_context_do_not_trigger_a_correction(self):
        for raw in (response(candidate()),response({}),response()):
            validated=contract.validate_response(raw,[self.p]);callback=Mock()
            _,report=wait_repair.attempt(raw,validated,[self.p],request=callback)
            callback.assert_not_called();self.assertFalse(report['attempted'])
        callback=Mock();_,report=wait_repair.attempt(self.raw,self.validated,[self.p],request=callback,previous_wait_reviews={INST:{'context_error':True}})
        callback.assert_not_called();self.assertEqual(report['status'],'unavailable')
    def test_timeout_or_malformed_json_never_retries_or_promotes_wait(self):
        for callback in (Mock(side_effect=TimeoutError('secret not logged')),Mock(return_value='not JSON')):
            result,report=wait_repair.attempt(self.raw,self.validated,[self.p],request=callback)
            callback.assert_called_once();self.assertEqual(report['status'],'failed')
            self.assertFalse(result['decisions'][INST]['contract_valid']);self.assertNotIn('secret',json.dumps(report))
    def test_response_after_time_budget_is_not_applied_but_is_archived(self):
        with patch.object(wait_repair.time,'monotonic',side_effect=[0,22,22]):
            result,report,request=self.call()
        self.assertFalse(result['decisions'][INST]['contract_valid'])
        self.assertEqual(report['status'],'failed');self.assertIn('response_snapshot',report)
        request.assert_called_once()
    def test_batch_cap_does_not_claim_uncalled_symbols_were_corrected(self):
        packages=[{**self.p,'instId':f'ASSET{i}-USDT-SWAP'} for i in range(13)]
        raw={**self.raw,'decisions':{p['instId']:copy.deepcopy(self.raw['decisions'][INST]) for p in packages}}
        validated=contract.validate_response(raw,packages)
        callback=Mock(return_value=json.dumps({'decisions':{packages[0]['instId']:macro_wait()}}))
        result,report=wait_repair.attempt(raw,validated,packages,request=callback)
        self.assertEqual(len(report['requested_symbols']),12)
        self.assertEqual(len(report['original_waits']),13)
        self.assertFalse(wait_repair.public_report(report,packages[-1]['instId'])['attempted'])
        self.assertFalse(result['decisions'][packages[-1]['instId']]['contract_valid'])
    def test_raw_archives_are_bounded_and_not_in_public_projection(self):
        value={'text':'字'*40000};snapshot=wait_repair.snapshot(value)
        self.assertTrue(snapshot['truncated']);self.assertEqual(len(snapshot['preview']),32000);self.assertEqual(len(snapshot['sha256']),64)
        _,report,_=self.call();public=wait_repair.public_report(report)
        self.assertNotIn('original_waits',public);self.assertNotIn('repair_output',public)


class LLMBudgetTests(unittest.TestCase):
    def test_single_attempt_is_forwarded_to_transport_without_changing_default_calls(self):
        from okxquant_backend import llm_manager
        payload={'choices':[{'message':{'content':'{}'}}],'usage':{}}
        with patch.object(llm_manager,'get_active_llm_runtime',return_value={}),patch.object(llm_manager,'request_json',return_value=(payload,200,1,1)) as request:
            args={'messages':[{'role':'user','content':'fixture'}],'model':'test','base_url':'https://example.invalid/v1','api_key':'','api_format':'openai_chat','timeout':20}
            llm_manager.execute_llm_request(**args,max_attempts=1)
            self.assertEqual(request.call_args.kwargs,{'max_attempts':1})
            self.assertEqual(request.call_args.args[3],20)
            llm_manager.execute_llm_request(**args)
            self.assertEqual(request.call_args.kwargs,{})


class CounterTests(unittest.TestCase):
    def cache(self,*,plans=0,model='WAIT',status='audited_wait',valid=True,error=None):
        return {INST:{'decision':{'action':'WAIT','model_action':model,'decision_status':status,'contract_valid':valid,
                               'entry_plans':{'plans':[{}]*plans,'error':error}}}}
    def test_errors_are_separate_from_absent_plans_and_audited_rejections(self):
        s=wait_counters.advance(None,self.cache(status='incomplete',valid=False),100)
        self.assertEqual(s['streaks'],{'no_program_plans':1,'model_all_wait':1,'audit_incomplete':1,'audited_wait_with_plans':0})
        s=wait_counters.advance(s,self.cache(plans=1),200)
        self.assertEqual(s['streaks'],{'no_program_plans':0,'model_all_wait':2,'audit_incomplete':0,'audited_wait_with_plans':1})
        s=wait_counters.advance(s,self.cache(plans=1,model='BUY_LONG',status='execution_rejected'),300)
        self.assertEqual(s['streaks']['model_all_wait'],0)
        self.assertEqual(s['last_cycle']['model_entry_proposals'],1)
    def test_unknown_catalog_or_model_is_unknown_not_zero(self):
        s=wait_counters.advance(None,self.cache(error='unavailable',model='MISSING'),100)
        self.assertIsNone(s['streaks']['no_program_plans']);self.assertIsNone(s['streaks']['model_all_wait'])
        self.assertIsNone(s['last_cycle']['program_plans'])
    def test_legacy_final_wait_streak_is_preserved_without_backfilling_new_counts(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(wait_audit,'DATA',Path(directory)):
            scope='demo-test';wait_audit._atomic(wait_audit._path(scope),{'scope':scope,'version':wait_audit.VERSION,'streak':10,'items':{}})
            cache=self.cache(status='incomplete',valid=False)
            wait_audit.commit(scope,cache,[package()],frame_id='one',now=100)
            state=wait_audit.public_status(scope)
            self.assertEqual(state['legacy_final_wait_streak'],11)
            self.assertEqual(state['diagnostics']['observed_rounds'],1)
            self.assertEqual(state['diagnostics']['streaks']['audit_incomplete'],1)
            wait_audit.commit(scope,cache,[package()],frame_id='one',now=200)
            self.assertEqual(wait_audit.public_status(scope)['diagnostics'],state['diagnostics'])
    def test_counter_corruption_is_not_silently_reset(self):
        s=wait_counters.advance(None,self.cache(),100);s['streaks']['audit_incomplete']=-1
        with self.assertRaises(ValueError):wait_counters.advance(s,self.cache(),200)
