import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import wait_audit as audit, trading_prompt as contract
from scripts.decision_reporting import summarize, format_summary
from test_trading_prompt_contract import package, response, ref, candidate


def valid_wait():
    def side(direction):
        return {'code':'confirmation_pending','reason':'当前结构尚未满足该方向的确认条件',
                'evidence':[ref('/price',100)],
                'reconsider':{'reason':'价格突破边界后重新审查，不直接交易',
                    'conditions':[{'ref':'/price','op':'gt' if direction=='long' else 'lt','value':101 if direction=='long' else 99}]}}
    return {'action':'WAIT','summary_reason':'多空分别缺少确认，按可观测条件重新审查',
            'wait_audit':{'version':audit.VERSION,'long':side('long'),'short':side('short')}}


class WaitContractTests(unittest.TestCase):
    def checked(self, decision, **kwargs):
        return contract.validate_response(response(decision),[package()],**kwargs)

    def test_wait_requires_audit_not_just_reason(self):
        result=self.checked({'action':'WAIT','summary_reason':'暂时没有合适机会'})
        self.assertEqual(result['validation']['status'],'incomplete')
        row=result['decisions']['BTC-USDT-SWAP']
        self.assertFalse(row['contract_valid']);self.assertEqual(row['action'],'WAIT')
        self.assertEqual(row['decision_status'],'incomplete')

    def test_audited_wait_does_not_need_invented_prices(self):
        result=self.checked(valid_wait())
        self.assertEqual(result['validation']['status'],'validated')
        row=result['decisions']['BTC-USDT-SWAP']
        self.assertEqual(row['decision_status'],'audited_wait')
        self.assertNotIn('entry_price',row)

    def test_schema_requires_both_directions_and_audit_version(self):
        schema=contract.output_schema()['properties']['decisions']['additionalProperties']['oneOf'][0]
        self.assertIn('wait_audit',schema['required'])
        for field in ('long','short','version'):
            raw=valid_wait();del raw['wait_audit'][field]
            self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')

    def test_fabricated_ref_or_value_rejected(self):
        for field,value in (('ref','/unknown'),('value',999)):
            raw=valid_wait();raw['wait_audit']['long']['evidence'][0][field]=value
            self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')

    def test_met_trigger_cannot_be_new_wait_condition(self):
        raw=valid_wait();raw['wait_audit']['long']['reconsider']['conditions'][0]['value']=90
        self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')

    def test_trigger_wrong_type_and_unknown_ref_rejected(self):
        for change in ({'value':True},{'value':'oops'},{'ref':'/missing'}, {'op':'exec'}):
            raw=valid_wait();raw['wait_audit']['long']['reconsider']['conditions'][0].update(change)
            self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')

    def test_missing_data_must_really_be_missing(self):
        raw=valid_wait();side=raw['wait_audit']['long']
        side.update(code='data_missing',evidence=[],missing_refs=['/rsi_1h'])
        side['reconsider']['conditions']=[{'ref':'/rsi_1h','op':'available','value':True}]
        self.assertEqual(self.checked(raw)['validation']['status'],'validated')
        side['missing_refs']=['/price'];side['reconsider']['conditions'][0]['ref']='/price'
        self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')

    def test_position_constraint_not_invented(self):
        raw=valid_wait();raw['wait_audit']['long']['code']='position_constraint'
        self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')
        raw['wait_audit']['long']['evidence']=[ref('/position/side','short')]
        self.assertEqual(self.checked(raw,positions=[{'instId':'BTC-USDT-SWAP','posSide':'short','pos':1}])['validation']['status'],'validated')

    def test_rr_assertion_requires_calculation(self):
        raw=valid_wait();raw['wait_audit']['long']['reason']='当前方案净盈亏比不足2，不能通过成本门槛'
        self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')

    def test_net_rr_below_threshold_is_recomputed(self):
        raw=valid_wait();side=raw['wait_audit']['long'];side.update(code='net_rr_below_minimum',
            geometry={'entry_price':100,'stop_loss_price':99,'take_profit_price':101,'evidence':[ref('/price',100)]})
        row=self.checked(raw)['decisions']['BTC-USDT-SWAP']
        self.assertTrue(row['contract_valid']);self.assertLess(row['wait_audit']['long']['net_rr_check']['net_rr'],2)
        side['geometry']['take_profit_price']=110
        self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')

    def test_previous_trigger_requires_matching_review_and_changed_evidence(self):
        prior={'BTC-USDT-SWAP':{'required':True,'review_id':'previous','changed_refs':['/price']}}
        raw=valid_wait()
        self.assertEqual(self.checked(raw,previous_wait_reviews=prior)['validation']['status'],'incomplete')
        raw['wait_audit']['previous_review']={'review_id':'previous','reason':'新价格变化后另一方向仍缺少确认','evidence':[ref('/price',100)]}
        self.assertEqual(self.checked(raw,previous_wait_reviews=prior)['validation']['status'],'validated')
        prior['BTC-USDT-SWAP']['changed_refs']=[]
        self.assertEqual(self.checked(raw,previous_wait_reviews=prior)['validation']['status'],'incomplete')

    def test_state_error_is_visible_but_does_not_force_entry(self):
        result=self.checked(valid_wait(),previous_wait_reviews={'BTC-USDT-SWAP':{'context_error':True}})
        self.assertEqual(result['validation']['status'],'incomplete')
        self.assertEqual(result['decisions']['BTC-USDT-SWAP']['action'],'WAIT')

    def test_entry_contract_unchanged(self):
        row=self.checked(candidate())['decisions']['BTC-USDT-SWAP']
        self.assertEqual(row['action'],'BUY_LONG');self.assertTrue(row['contract_valid'])

    def test_predicate_all_and_unknown(self):
        catalog={'/price':{'value':100}}
        self.assertEqual(audit.evaluate([{'ref':'/price','op':'gte','value':100}],catalog),'met')
        self.assertEqual(audit.evaluate([{'ref':'/price','op':'gt','value':100}],catalog),'not_met')
        self.assertEqual(audit.evaluate([{'ref':'/missing','op':'gt','value':1}],catalog),'unknown')

    def test_range_references_come_from_real_closed_rows(self):
        p=package();p['recent_1h']=[[100,110,90,105,1],[95,103,92,100,2]]
        facts=contract.facts_for(p)
        self.assertEqual(facts['/range/1H/high']['value'],110)
        self.assertEqual(facts['/range/1H/low']['value'],90)

    def test_threshold_shift_requires_explanation_even_before_trigger(self):
        raw=valid_wait();old=copy.deepcopy(raw['wait_audit'])
        prior={'BTC-USDT-SWAP':{'required':False,'review_id':'old','changed_refs':['/price'],
            'previous_conditions':{s:old[s]['reconsider'] for s in ('long','short')}}}
        raw['wait_audit']['long']['reconsider']['conditions'][0]['value']=102
        self.assertEqual(self.checked(raw,previous_wait_reviews=prior)['validation']['status'],'incomplete')
        raw['wait_audit']['previous_review']={'review_id':'old','reason':'价格变化后重新评估边界位置','evidence':[ref('/price',100)]}
        self.assertEqual(self.checked(raw,previous_wait_reviews=prior)['validation']['status'],'validated')

    def test_forged_computed_rr_is_not_displayed(self):
        raw=valid_wait();raw['wait_audit']['long']['net_rr_check']={'net_rr':0.1,'minimum':2}
        checked=self.checked(raw)['decisions']['BTC-USDT-SWAP']
        self.assertNotIn('net_rr_check',checked['wait_audit']['long'])

    def test_unreachable_calculus_threshold_rejected(self):
        raw=valid_wait();raw['wait_audit']['long']['reconsider']['conditions']=[{'ref':'/calculus/timeframes/1H/velocity','op':'gt','value':99}]
        self.assertEqual(self.checked(raw)['validation']['status'],'incomplete')


class WaitStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        patcher=patch.object(audit,'DATA',Path(self.tmp.name));patcher.start();self.addCleanup(patcher.stop)
        self.scope='demo:test';self.p=package()
        self.decision=contract.validate_response(response(valid_wait()),[self.p])['decisions']['BTC-USDT-SWAP']
        self.cache={'BTC-USDT-SWAP':{'decision':self.decision,'decision_id':'original'}}

    def test_scope_isolation_and_no_duplicate_frame_count(self):
        audit.commit(self.scope,self.cache,[self.p],frame_id='one',now=100)
        audit.commit(self.scope,self.cache,[self.p],frame_id='one',now=105)
        self.assertEqual(audit.public_status(self.scope)['no_entry_candidate_streak'],1)
        self.assertEqual(audit.public_status('live:other')['items'],[])

    def test_trigger_evaluation_and_incomplete_preserves_original_review(self):
        audit.commit(self.scope,self.cache,[self.p],frame_id='one',now=100)
        p={**self.p,'price':102}
        context=audit.prepare(self.scope,[p],now=200)['BTC-USDT-SWAP']
        self.assertTrue(context['required']);self.assertEqual(context['trigger_checks']['long'],'met')
        self.assertIn('/price',context['changed_refs'])
        bad={'BTC-USDT-SWAP':{'decision':{'action':'WAIT','contract_valid':False,'decision_status':'incomplete','summary_reason':'invalid'}}}
        audit.commit(self.scope,bad,[p],frame_id='two',now=200)
        self.assertEqual(audit.prepare(self.scope,[p],now=210)['BTC-USDT-SWAP']['review_id'],'original')

    def test_expired_condition_requires_review_even_if_not_met(self):
        audit.commit(self.scope,self.cache,[self.p],frame_id='one',now=100)
        self.assertTrue(audit.prepare(self.scope,[self.p],now=4000)['BTC-USDT-SWAP']['required'])

    def test_long_wait_diagnostic_never_changes_decision(self):
        for i in range(audit.ALERT_ROUNDS):audit.commit(self.scope,self.cache,[self.p],frame_id=str(i),now=100+i*900)
        status=audit.public_status(self.scope)
        self.assertTrue(status['alert']);self.assertEqual(self.cache['BTC-USDT-SWAP']['decision']['action'],'WAIT')

    def test_corrupt_state_is_not_silently_reset(self):
        audit._path(self.scope).write_text('broken')
        self.assertEqual(audit.public_status(self.scope)['status'],'error')
        self.assertTrue(audit.prepare(self.scope,[self.p])['BTC-USDT-SWAP']['context_error'])
        with self.assertRaises(ValueError):audit.commit(self.scope,self.cache,[self.p],frame_id='x')

    def test_unchanged_wait_does_not_reset_max_review_age(self):
        for i in range(4):audit.commit(self.scope,self.cache,[self.p],frame_id=str(i),now=100+i*900)
        self.assertTrue(audit.prepare(self.scope,[self.p],now=3800)['BTC-USDT-SWAP']['required'])

    def test_nested_corruption_is_reported(self):
        audit._path(self.scope).write_text(json.dumps({'scope':self.scope,'version':audit.VERSION,'items':{'BTC':[]},'streak':0}))
        self.assertEqual(audit.public_status(self.scope)['status'],'error')

    def test_failed_atomic_write_keeps_previous_state(self):
        audit.commit(self.scope,self.cache,[self.p],frame_id='one',now=100)
        before=audit._path(self.scope).read_bytes()
        with patch.object(audit.os,'replace',side_effect=OSError('offline')):
            with self.assertRaises(OSError):audit.commit(self.scope,self.cache,[self.p],frame_id='two',now=200)
        self.assertEqual(audit._path(self.scope).read_bytes(),before)


class DecisionReportingTests(unittest.TestCase):
    def test_wld_notice_does_not_replace_other_symbol_reviews(self):
        decision=contract.validate_response(response(valid_wait()),[package()])['decisions']['BTC-USDT-SWAP']
        summary=summarize({'BTC-USDT-SWAP':{'name':'BTC','decision':decision}},['WLD：模拟盘不支持，仅观察'])
        line=format_summary(summary)
        self.assertEqual(summary['evaluated_count'],1)
        self.assertEqual(summary['items'][0]['name'],'BTC');self.assertIn('WAIT1',line);self.assertIn('观察:WLD',line)
        self.assertEqual(summary['counts']['audited_wait'],1)

    def test_no_cache_is_not_audited_wait(self):
        summary=summarize({},['WLD：仅观察'],unavailable_reason='模型503')
        self.assertEqual(summary['status'],'unavailable');self.assertIn('503',format_summary(summary))

    def test_legacy_wait_and_rejected_entry_are_incomplete(self):
        summary=summarize({'BTC-USDT-SWAP':{'decision':{'action':'WAIT','summary_reason':'old'}}})
        self.assertEqual(summary['counts']['incomplete'],1)
        self.assertIn('不完整1(BTC)',format_summary(summary))

    def test_incomplete_macro_is_not_green_ready(self):
        from okxquant_backend.macro_status import project
        result=project({'BTC':{'macro_assessment':'text','timestamp':100}},[],validation={'status':'incomplete'},validation_at=101,now=110)
        self.assertEqual(result['status'],'incomplete')

    def test_execution_rejection_is_not_model_wait_or_missing_audit(self):
        summary=summarize({'BTC-USDT-SWAP':{'decision':{'action':'WAIT','contract_valid':False,'decision_status':'execution_rejected','validation_reason':'ADX gate'}}})
        self.assertEqual(summary['counts']['execution_rejected'],1)
        self.assertEqual(summary['counts']['incomplete'],0)
        self.assertEqual(summary['items'][0]['reason'],'ADX gate');self.assertIn('风控拒绝1(BTC)',format_summary(summary))
