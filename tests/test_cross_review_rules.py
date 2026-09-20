"""Independent rule-engine cross review; run only through scripts/run_tests.py.
Production source is never changed. Persistence POCs use disposable storage.
The differential reference executes ONLY five fixed, validated repository sources
inside the isolated test snapshot, never uploaded/custom rule text.
"""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import rule_interpreter as rules
from okxquant_backend import interceptor_manager as manager

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'plugins'/'interceptors'
PASS="def check_risk(package, decision, context):\n    return True, ''\n"

def rule(body):
    return "def check_risk(package, decision, context):\n"+"\n".join("    "+line for line in body.splitlines())+"\n"

class RuleStorageCrossReview(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.source=self.root/'source';shutil.copytree(SOURCE,self.source,ignore=shutil.ignore_patterns('__pycache__'))
        self.custom=self.root/'data'/'interceptors';self.config=self.root/'data'/'config.json'
        for name,value in [('PLUGINS_DIR',self.source),('CUSTOM_PLUGINS_DIR',self.custom),('CONFIG_FILE',self.config)]:
            self.stack.enter_context(patch.object(manager,name,value))
        self.package={'instId':'BTC-USDT-SWAP','data_quality':'valid','macro_4h':'4H_MACRO_BULL','adx_1h':25}
        self.decision={'action':'BUY_LONG','confidence':0,'entry_price':100,'stop_loss_price':95,'take_profit_price':115}
    def pipeline(self,p=None,d=None,c=None):
        return manager.run_interceptor_pipeline(p or self.package,d or self.decision,c or {})

    def test_p2_failed_override_must_not_change_effective_builtin(self):
        name='01_macro_trend_filter.py'
        p={**self.package,'macro_4h':'4H_MACRO_BEAR'}
        self.assertEqual(self.pipeline(p)[0],'WAIT')
        with patch.object(manager,'save_config',side_effect=OSError('injected config fsync failure')):
            with self.assertRaises(OSError):manager.save_plugin_code(name,PASS)
        self.assertEqual(self.pipeline(p)[0],'WAIT','P2: failed save silently changed active builtin into PASS')

    def test_p2_failed_create_must_not_activate_orphan_overlay(self):
        with patch.object(manager,'save_config',side_effect=OSError('injected config replace failure')):
            with self.assertRaises(OSError):manager.create_plugin('orphan',rule("return False, 'not committed'"))
        self.assertNotIn('orphan.py',[p['filename'] for p in manager.list_plugins()],
                         'P2: failed create left an unregistered overlay enabled by discovery')

    def test_committed_manifest_survives_mirror_write_failure(self):
        with patch.object(manager,'_mirror_committed',return_value=None):
            manager.create_plugin('manifest_only',PASS)
        self.assertFalse((self.custom/'manifest_only.py').exists())
        self.assertEqual(manager.get_plugin_detail('manifest_only.py')['code'],PASS)
        self.assertTrue(manager.get_plugin_detail('manifest_only.py')['supported_rule'])
        self.assertEqual(self.pipeline()[0],'BUY_LONG')

    def test_failed_config_commit_preserves_existing_custom_rule(self):
        manager.create_plugin('existing_rule',rule("return False, 'original'"))
        before=self.config.read_bytes()
        with patch.object(manager,'save_config',side_effect=OSError('injected failure')):
            with self.assertRaises(OSError):manager.save_plugin_code('existing_rule.py',PASS)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertEqual(self.pipeline()[0],'WAIT')

    def test_overlay_persistence_source_tombstone_and_abi(self):
        name='01_macro_trend_filter.py';before=(self.source/name).read_bytes()
        meta=manager.save_plugin_code(name,PASS)
        self.assertEqual((self.source/name).read_bytes(),before)
        self.assertEqual(meta['storage'],'custom')
        self.assertEqual(meta['execution_engine'],rules.ENGINE)
        self.assertTrue(meta['supported_rule']);self.assertTrue(meta['valid_syntax'])
        self.assertEqual(manager.get_plugin_detail(name)['code'],PASS)
        manager.delete_plugin(name)
        self.assertTrue((self.source/name).exists());self.assertIn(name,manager.load_config()['deleted'])
        self.assertNotIn(name,[p['filename'] for p in manager.list_plugins()])
        self.assertFalse(manager.run_sandbox_test()['arbitrary_python'])

    def test_concurrent_different_writers_preserve_flags_order_and_overlays(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i:manager.create_plugin('cross_'+str(i),PASS),range(8)))
            list(pool.map(lambda i:manager.toggle_plugin('cross_'+str(i)+'.py',False),range(8)))
        cfg=manager.load_config()
        for i in range(8):
            name='cross_'+str(i)+'.py'
            self.assertIn(name,cfg['pipeline_order']);self.assertIs(cfg['enabled'][name],False)
            self.assertEqual(manager.get_plugin_detail(name)['code'],PASS)

    def test_plan_adapter_cannot_take_caller_supplied_plans_or_callables(self):
        p={**self.package,'adx_1h':16}
        d={**self.decision,'candidate_id':'forged','contract_valid':True}
        with patch.object(manager,'_trusted_entry_plans',return_value=[]) as provider:
            self.assertEqual(self.pipeline(p,d,{rules.PLAN_KEY:[{'id':'forged','action':'BUY_LONG'}]})[0],'WAIT')
        provider.assert_called_once()
        fn=Mock(side_effect=AssertionError('must not call'))
        self.assertEqual(self.pipeline(c={'loader':fn})[0],'WAIT');fn.assert_not_called()

    def test_actual_valid_low_adx_candidates_unchanged_in_both_directions(self):
        from test_entry_candidates import package,selection
        from scripts import entry_candidates,trading_prompt
        for side in ('long','short'):
            p=package(side);p['adx_1h']=16
            plan=entry_candidates.catalog(p)['plans'][0]
            d=trading_prompt.candidate(p,selection(plan),trading_prompt.facts_for(p))
            self.assertTrue(d['contract_valid'])
            self.assertEqual(self.pipeline(p,d)[0],d['action'])

    def test_new_abi_provenance_fields_remain_ordinary_immutable_data(self):
        d={**self.decision,'strategy_profile_hash':'abc','memory_publication':{'scope':'demo','active_version':3,'prompt_hash':'memory'},
           'invalidation':{'price':95,'timeframe':'1H'},'wait_repair':None,'supporting_evidence':[{'ref':'/price','value':100}]}
        c={'active_inst_ids':set(),'active_position_sides':{},'account_scope':'demo','binding_version':7,'position_basis':[]}
        before=copy.deepcopy((self.package,d,c))
        self.assertEqual(self.pipeline(d=d,c=c)[0],'BUY_LONG')
        self.assertEqual((self.package,d,c),before)

class CapabilitiesCrossReview(unittest.TestCase):
    def test_no_indirect_call_attribute_import_or_mutation_channels(self):
        payloads=[
            "return context['function'](), ''", "f = context.get('function')\nreturn f(), ''",
            "return bool((()).__class__.__base__.__subclasses__()), ''",
            "return bool(getattr(context,'get')), ''", "return True, '{x.__class__}'.format(x=1)",
            "return True, str.__call__('x')", "return True, open('fixture').read()",
            "import socket\nreturn True, ''", "import os\nreturn True, os.environ.get('X')",
            "from pathlib import Path\nreturn True, ''", "context['x'] = 1\nreturn True, ''",
            "context.update({'x': 1})\nreturn True, ''", "return True, eval('1')",
        ]
        for payload in payloads:
            with self.subTest(payload=payload),self.assertRaises(rules.RuleError):rules.validate_rule(rule(payload))

    def test_custom_object_hooks_are_not_touched(self):
        calls=[]
        class Trap(dict):
            def get(self,*a):calls.append('get');raise AssertionError()
            def __iter__(self):calls.append('iter');raise AssertionError()
            def __str__(self):calls.append('str');raise AssertionError()
        program=rules.validate_rule(PASS)
        for value in (Trap(),{'nested':Trap()}, {'fn':lambda:None}):
            with self.assertRaises(rules.RuleError):program.execute(value,{}, {})
        self.assertEqual(calls,[])

    def test_budget_faults_cannot_be_swallowed_by_any_supported_except(self):
        for catcher in ('Exception','ValueError','(TypeError, ValueError)',''):
            except_line='except '+catcher+':' if catcher else 'except:'
            source=rule('try:\n    while True:\n        pass\n'+except_line+"\n    return True, 'swallowed'\nreturn True, ''")
            with self.subTest(catcher=catcher),self.assertRaises(rules.RuleBudgetExceeded):
                rules.validate_rule(source).execute({}, {}, {},rules.Budget(loops=8))

    def test_allocations_formatting_and_generator_budget_are_bounded(self):
        bodies=["x = 'x' * 1000000000\nreturn True, ''", "x=2**1000000000\nreturn True, ''",
                "x=[0]*1000000000\nreturn True, ''", "return True, f'{1:999}'",
                "return all(x >= 0 for x in range(2000)), ''"]
        for body in bodies:
            with self.subTest(body=body),self.assertRaises(rules.RuleBudgetExceeded):
                rules.validate_rule(rule(body)).execute({}, {}, {},rules.Budget(loops=8))

    def test_exception_can_handle_bad_data_without_authorizing_budget_faults(self):
        source=rule("try:\n    x=float('bad')\nexcept ValueError:\n    return False, 'invalid data'\nreturn True, ''")
        self.assertEqual(rules.validate_rule(source).execute({}, {}, {}),(False,'invalid data'))

class ActualSourceDifferentialReview(unittest.TestCase):
    def test_735_actual_builtin_source_comparisons(self):
        cases=[]
        base={'macro_4h':'4H_MACRO_BULL','adx_1h':25.,'acceleration_a':0.,'smart_money':{}}
        decision={'action':'BUY_LONG','confidence':0,'entry_price':100,'stop_loss_price':95,'take_profit_price':115}
        plans=[{'id':'valid','action':'BUY_LONG'}]
        for action in ('WAIT','BUY_LONG','SELL_SHORT'):
            for macro in ('4H_MACRO_BULL','4H_MACRO_BEAR','4H_MACRO_RANGE',None):
                for adx in (None,'bad',0,14.2,18,25,float('nan')):
                    cases.append(({**base,'macro_4h':macro,'adx_1h':adx},{**decision,'action':action}))
        for score in (None,True,False,'bad',float('nan'),float('inf'),-1,0,45,74.9,75,80,100,101):
            cases.append((base,{**decision,'confidence':score}))
        for prices in ((100,115,95),(100,110,95),(100,109.99,95),(100,85,105),(100,90,105),(100,91,105),(100,100,100),('bad',115,95),(0,None,-1)):
            for action in ('BUY_LONG','SELL_SHORT'):
                cases.append((base,{**decision,'action':action,**dict(zip(('entry_price','take_profit_price','stop_loss_price'),prices))}))
        for acc in (-.601,-.6,-.59,'bad',None):
            for flow in (-10000001,-10000000,0,'bad',None):
                cases.append(({**base,'acceleration_a':acc,'smart_money':{'net_flow_usdt':flow}},decision))
        for valid in (True,False):
            for candidate in ('valid','fake',None):
                cases.append(({**base,'adx_1h':16},{**decision,'contract_valid':valid,'candidate_id':candidate}))
        self.assertEqual(len(cases),147)
        from scripts.risk_policy import Policy
        compared=0
        with patch('scripts.entry_candidates.catalog',return_value={'plans':plans}),patch('scripts.risk_policy.load_policy',return_value=Policy()):
            for filename in manager.DEFAULT_ORDER:
                source=(SOURCE/filename).read_text(encoding='utf8')
                program=rules.validate_rule(source,migrate_legacy=True)
                # Fixed reviewed repository builtins only. No upload/overlay source
                # reaches this test-only Python reference evaluator.
                namespace={}
                exec(compile(source,str(SOURCE/filename),'exec'),namespace)
                reference=namespace['check_risk']
                for index,(p,d) in enumerate(cases):
                    with self.subTest(filename=filename,index=index):
                        self.assertEqual(program.execute(copy.deepcopy(p),copy.deepcopy(d),{rules.PLAN_KEY:plans}),
                                         reference(copy.deepcopy(p),copy.deepcopy(d),{}))
                    compared+=1
        self.assertEqual(compared,735)


class RuleApiCrossReview(unittest.TestCase):
    def setUp(self):
        RuleStorageCrossReview.setUp(self)
        from fastapi.testclient import TestClient
        from okxquant_backend import app as api
        from okxquant_backend.admin_auth import AdminAuthStore
        self.api=api
        auth=AdminAuthStore(self.root/'auth.db')
        auth.initialize_from_legacy('RuleCrossAdminFixture123!')
        auth.create_user('reader','RuleCrossReaderFixture123!','admin')
        self.stack.enter_context(patch.object(api,'admin_auth',auth))
        self.stack.enter_context(patch.object(api,'DATA_DIR',self.root))
        self.stack.enter_context(patch.object(api,'audit_record'))
        self.client=TestClient(api.app,raise_server_exceptions=False);self.addCleanup(self.client.close)
        self.superuser={'X-OKXQuant-Session':auth.login('admin','RuleCrossAdminFixture123!')['session_token']}
        self.reader={'X-OKXQuant-Session':auth.login('reader','RuleCrossReaderFixture123!')['session_token']}

    def test_every_rule_write_and_test_route_rejects_anonymous_and_reader_before_effects(self):
        cases=[('POST','',{'filename':'fixture.py','code':PASS},'create_plugin'),
               ('PUT','/01_macro_trend_filter.py/code',{'code':PASS},'save_plugin_code'),
               ('PUT','/01_macro_trend_filter.py/toggle',{'enabled':False},'toggle_plugin'),
               ('DELETE','/01_macro_trend_filter.py',None,'delete_plugin'),
               ('POST','/reorder',{'pipeline_order':[]},'reorder_plugins'),
               ('POST','/test',{'scenario':None},'run_sandbox_test')]
        for method,path,payload,operation in cases:
            for headers,expected in (({},401),(self.reader,403)):
                with self.subTest(method=method,path=path,expected=expected),patch.object(manager,operation) as effect:
                    response=self.client.request(method,'/api/v1/admin/interceptors'+path,json=payload,headers=headers)
                    self.assertEqual(response.status_code,expected,response.text)
                    effect.assert_not_called()

    def test_superadmin_unsafe_save_rejected_without_changing_source_or_overlay(self):
        name='01_macro_trend_filter.py';before=(self.source/name).read_bytes()
        response=self.client.put('/api/v1/admin/interceptors/'+name+'/code',headers=self.superuser,
                                 json={'code':rule("import os\nreturn True, ''")})
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual((self.source/name).read_bytes(),before)
        self.assertFalse((self.custom/name).exists())

    def test_toggle_and_reorder_domain_errors_use_400_404(self):
        response=self.client.put('/api/v1/admin/interceptors/missing.py/toggle',headers=self.superuser,json={'enabled':True})
        self.assertEqual(response.status_code,404,response.text)
        response=self.client.put('/api/v1/admin/interceptors/bad..py/toggle',headers=self.superuser,json={'enabled':True})
        self.assertEqual(response.status_code,400,response.text)
        response=self.client.post('/api/v1/admin/interceptors/reorder',headers=self.superuser,json={'pipeline_order':['bad..py']})
        self.assertEqual(response.status_code,400,response.text)

    def test_reader_can_inspect_new_engine_metadata_but_not_mutate(self):
        response=self.client.get('/api/v1/admin/interceptors',headers=self.reader)
        self.assertEqual(response.status_code,200,response.text)
        for row in response.json()['plugins']:
            self.assertEqual(row['execution_engine'],rules.ENGINE)
            self.assertIn('supported_rule',row);self.assertIn('storage',row)

if __name__=='__main__':unittest.main()
