"""Default-rule scenario parity and manager persistence/security contracts.
Reference decisions are ordinary test code, never imported/evaluated plugin code.
"""
import ast
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from okxquant_backend import interceptor_manager as manager
from scripts import rule_interpreter as rules
from test_audit_a07_plugins_language import code

ROOT=Path(__file__).resolve().parents[1]
SOURCES=ROOT/'plugins/interceptors'
PASS="def check_risk(package, decision, context):\n    return True, ''\n"


def oracle(filename,p,d,plans):
    action=str(d.get('action','WAIT')).upper()
    if action=='WAIT':return True,''
    if filename.startswith('01_'):
        macro=str(p.get('macro_4h','') or '')
        if action=='SELL_SHORT' and '4H_MACRO_BULL' in macro:return False,'4H大级别处于多头主升通道，顺势铁律拦截逆势摸顶开空，安全降级为 WAIT。'
        if action=='BUY_LONG' and '4H_MACRO_BEAR' in macro:return False,'4H大级别处于空头承压通道，顺势铁律拦截逆势接飞刀做多，安全降级为 WAIT。'
    elif filename.startswith('02_'):
        raw=d.get('confidence')
        try:score=float(raw)
        except (ValueError,TypeError):return False,'证据评分缺失或格式无效；这不是开仓分数不足。'
        if isinstance(raw,bool) or not math.isfinite(score) or not 0<=score<=100:
            return False,'证据评分必须为 0~100 的有限数值；不能将其解释为成功概率。'
    elif filename.startswith('03_'):
        try:adx=float(p.get('adx_1h',0) or 0)
        except (ValueError,TypeError):adx=0.
        if 0<adx<18:
            if d.get('contract_valid') is True and d.get('candidate_id'):
                if any(x['id']==d['candidate_id'] and x['action']==action for x in plans):return True,''
            return False,f'1H ADX 趋势强度仅 {adx:.1f}，处于无序震荡杂波市，安全降级为 WAIT。'
    elif filename.startswith('04_'):
        try:entry,tp,sl=[float(d.get(k,0) or 0) for k in ('entry_price','take_profit_price','stop_loss_price')]
        except (ValueError,TypeError):return False,'订单价格几何参数缺失或非浮点数，安全降级为 WAIT。'
        rr=0.
        if action=='BUY_LONG' and entry>sl>0 and tp>entry:rr=(tp-entry)/(entry-sl)
        elif action=='SELL_SHORT' and sl>entry>tp>0:rr=(entry-tp)/(sl-entry)
        if rr<2:return False,f'模型报价盈亏比 {rr:.2f}R 未满足真实 2R 门禁，执行层降级为 WAIT。'
    else:
        try:
            acc=float(p.get('acceleration_a',0) or 0)
            if action=='BUY_LONG' and acc<-.6:return False,f'多头买入但二阶加速度 a={acc:.3f} 严重失速衰竭，广场示例插件拦截'
        except Exception:pass
        money=p.get('smart_money',{})
        if isinstance(money,dict):
            try:
                flow=float(money.get('net_flow_usdt',0) or 0)
                if action=='BUY_LONG' and flow<-10000000:return False,f'多头买入但聪明钱净流出 {flow/1e4:.1f}万 U，资金面严重背离，广场示例插件拦截'
            except Exception:pass
    return True,''


class BuiltinScenarioParityTests(unittest.TestCase):
    def test_five_builtin_rules_preserve_results_and_exact_reasons_across_scenarios(self):
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
        self.assertEqual(len(list(SOURCES.glob('*.py'))),5)
        for file in SOURCES.glob('*.py'):
            program=rules.validate_rule(file.read_text(encoding='utf-8'),migrate_legacy=True)
            for i,(p,d) in enumerate(cases):
                with self.subTest(rule=file.name,scenario=i):
                    self.assertEqual(program.execute(p,d,{rules.PLAN_KEY:plans}),oracle(file.name,p,d,plans))


class PluginManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.source=root/'source';self.custom=root/'data/interceptors';self.config=root/'data/config.json'
        shutil.copytree(SOURCES,self.source,ignore=shutil.ignore_patterns('__pycache__'))
        for name,value in (('PLUGINS_DIR',self.source),('CUSTOM_PLUGINS_DIR',self.custom),('CONFIG_FILE',self.config)):
            p=patch.object(manager,name,value);p.start();self.addCleanup(p.stop)
        self.package={'name':'BTC','instId':'BTC-USDT-SWAP','data_quality':'valid','macro_4h':'4H_MACRO_BULL','adx_1h':25}
        self.decision={'action':'BUY_LONG','confidence':0,'entry_price':100,'stop_loss_price':95,'take_profit_price':115}

    def pipeline(self,p=None,d=None,c=None):return manager.run_interceptor_pipeline(p or self.package,d or self.decision,c or {})

    def test_standard_scenarios_and_default_enable_order_are_preserved(self):
        listed=manager.list_plugins()
        self.assertEqual([p['filename'] for p in listed],manager.DEFAULT_ORDER)
        self.assertEqual([p['enabled'] for p in listed],[True,True,True,True,False])
        self.assertTrue(all(p['valid_syntax'] and p['supported_rule'] for p in listed))
        result=manager.run_sandbox_test()
        self.assertEqual([r['final_action'] for r in result['results']],['WAIT','WAIT','BUY_LONG','BUY_LONG'])
        self.assertEqual(result['execution_engine'],rules.ENGINE)
        self.assertIs(result['arbitrary_python'],False)
        for score in (0,1,45,60,74.9,75,80,100):
            self.assertEqual(self.pipeline(d={**self.decision,'confidence':score})[:2],('BUY_LONG',''))

    def test_custom_new_strategy_rule_can_be_added_edited_and_run_without_source_write(self):
        before={p.name:p.read_bytes() for p in self.source.glob('*.py')}
        created=manager.create_plugin('my_rule',code("return decision.get('leverage', 1) <= 3, 'custom leverage policy'"))
        self.assertEqual(created['storage'],'custom');self.assertTrue(created['enabled'])
        self.assertEqual(self.pipeline(d={**self.decision,'leverage':4})[0],'WAIT')
        manager.save_plugin_code('my_rule.py',PASS)
        self.assertEqual(self.pipeline(d={**self.decision,'leverage':4})[0],'BUY_LONG')
        self.assertEqual({p.name:p.read_bytes() for p in self.source.glob('*.py')},before)
        self.assertEqual((self.custom/'my_rule.py').stat().st_mode&0o777,0o600)

    def test_builtin_override_preserves_source_and_existing_enable_configuration(self):
        name=manager.DEFAULT_ORDER[0];old=(self.source/name).read_bytes()
        manager.toggle_plugin(name,False)
        manager.save_plugin_code(name,PASS)
        self.assertEqual((self.source/name).read_bytes(),old)
        self.assertEqual(manager.get_plugin_detail(name)['code'],PASS)
        self.assertFalse(manager.get_plugin_detail(name)['enabled'])
        self.assertEqual([p['filename'] for p in manager.list_plugins()],manager.DEFAULT_ORDER)

    def test_existing_source_edit_and_legacy_custom_file_are_not_discarded(self):
        (self.source/'01_macro_trend_filter.py').write_text(PASS,encoding='utf-8')
        (self.source/'legacy_rule.py').write_text(code("return False, 'legacy rule remains active'"),encoding='utf-8')
        self.assertEqual(self.pipeline()[1],'legacy rule remains active')
        self.assertEqual(manager.get_plugin_detail('01_macro_trend_filter.py')['code'],PASS)
        self.assertEqual(manager.get_plugin_detail('legacy_rule.py')['storage'],'source')

    def test_readonly_source_layer_is_never_a_write_target(self):
        self.source.chmod(0o555);self.addCleanup(self.source.chmod,0o755)
        with patch.object(manager,'_atomic_text',wraps=manager._atomic_text) as write:
            manager.create_plugin('fresh',PASS)
            manager.save_plugin_code(manager.DEFAULT_ORDER[0],PASS)
            manager.delete_plugin(manager.DEFAULT_ORDER[1])
        self.assertTrue(all(c.args[0].parent!=self.source for c in write.call_args_list))
        self.assertTrue((self.source/manager.DEFAULT_ORDER[1]).exists())

    def test_delete_source_is_tombstoned_and_delete_overlay_does_not_resurrect_source(self):
        name=manager.DEFAULT_ORDER[0]
        manager.save_plugin_code(name,PASS);manager.delete_plugin(name)
        self.assertTrue((self.source/name).exists());self.assertFalse((self.custom/name).exists())
        self.assertIn(name,manager.load_config()['deleted'])
        self.assertNotIn(name,[p['filename'] for p in manager.list_plugins()])
        with self.assertRaises(FileNotFoundError):manager.get_plugin_detail(name)
        manager.create_plugin(name,PASS)
        self.assertNotIn(name,manager.load_config()['deleted']);self.assertEqual(manager.get_plugin_detail(name)['code'],PASS)

    def test_save_rejects_unsafe_code_without_changing_existing_bytes_or_config(self):
        name=manager.DEFAULT_ORDER[0];before=(self.source/name).read_bytes()
        for source in ("import os\n"+PASS,code("open('/tmp/untrusted', 'w')\nreturn True,''"),code("return True, package.__class__")):
            with self.assertRaises(ValueError):manager.save_plugin_code(name,source)
        self.assertEqual((self.source/name).read_bytes(),before)
        self.assertFalse((self.custom/name).exists())
        self.assertFalse(self.config.exists())

    def test_unsafe_legacy_top_level_payload_never_runs_even_in_test_api(self):
        marker=Path(self.tmp.name)/'MUST_NOT_EXIST'
        payload=f"open({str(marker)!r}, 'w').write('executed')\n"+PASS
        (self.source/'legacy_evil.py').write_text(payload,encoding='utf-8')
        with patch('importlib.util.spec_from_file_location',side_effect=AssertionError('module loading forbidden')):
            result=manager.run_sandbox_test()
            metadata=manager.get_plugin_detail('legacy_evil.py')
        self.assertFalse(marker.exists());self.assertFalse(metadata['valid_syntax'])
        self.assertEqual([r['final_action'] for r in result['results']],['WAIT']*4)

    def test_enable_checks_language_and_missing_enabled_rule_is_not_silently_skipped(self):
        (self.source/'unsafe.py').write_text('import os\n'+PASS,encoding='utf-8')
        manager.toggle_plugin('unsafe.py',False)
        with self.assertRaises(ValueError):manager.toggle_plugin('unsafe.py',True)
        self.assertEqual(self.pipeline()[0],'BUY_LONG')
        manager.create_plugin('missing',PASS);(self.custom/'missing.py').unlink()
        # The manifest remains authoritative if only its file mirror disappears.
        self.assertEqual(self.pipeline()[0],'BUY_LONG')
        config=manager.load_config();config['sources'].pop('missing.py');manager.save_config(config)
        # Loss of both source copies must not silently skip the registered rule.
        self.assertEqual(self.pipeline()[0],'WAIT')

    def test_concurrent_creates_preserve_all_config_entries(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i:manager.create_plugin(f'parallel_{i}',PASS),range(12)))
        config=manager.load_config()
        for i in range(12):
            self.assertIn(f'parallel_{i}.py',config['pipeline_order']);self.assertTrue(config['enabled'][f'parallel_{i}.py'])
        self.assertEqual(len(manager.list_plugins()),17)

    def test_reorder_deduplicates_without_double_execution_or_losing_flags(self):
        name=manager.DEFAULT_ORDER[0]
        manager.toggle_plugin(name,False)
        listed=manager.reorder_plugins([name,name]+list(reversed(manager.DEFAULT_ORDER)))
        self.assertEqual(len(listed),5);self.assertEqual(listed[0]['filename'],name);self.assertFalse(listed[0]['enabled'])

    def test_path_traversal_symlink_and_oversized_source_are_rejected(self):
        for name in ('../bad.py','/tmp/bad.py','bad/thing.py','bad\\thing.py','bad.py:stream'):
            with self.assertRaises((ValueError,FileNotFoundError)):manager.save_plugin_code(name,PASS)
        manager.ensure_plugins_dir()
        outside=Path(self.tmp.name)/'outside.py';outside.write_text(PASS)
        (self.custom/'link.py').symlink_to(outside)
        with self.assertRaises((ValueError,FileNotFoundError)):manager.get_plugin_detail('link.py')
        with self.assertRaises(ValueError):manager.save_plugin_code('link.py',PASS)
        (self.custom/'big.py').write_bytes(b' '*(rules.MAX_SOURCE_BYTES+1))
        self.assertFalse(manager.parse_plugin_metadata(self.custom/'big.py')['valid_syntax'])

    def test_legacy_adx_plan_translation_is_persisted_without_external_imports(self):
        name='03_adx_volatility_filter.py';source=(self.source/name).read_text(encoding='utf-8')
        saved=manager.save_plugin_code(name,source)
        self.assertNotIn('from scripts.',saved['code']);self.assertIn(rules.PLAN_KEY,saved['code'])
        self.assertEqual((self.source/name).read_text(encoding='utf-8'),source)
        candidate={**self.decision,'candidate_id':'valid','contract_valid':True}
        p={**self.package,'adx_1h':16}
        with patch.object(manager,'_trusted_entry_plans',return_value=[{'id':'valid','action':'BUY_LONG'}]) as provider:
            self.assertEqual(self.pipeline(p,candidate)[0],'BUY_LONG');provider.assert_called_once()
        with patch.object(manager,'_trusted_entry_plans',return_value=[]):
            self.assertEqual(self.pipeline(p,candidate,{rules.PLAN_KEY:[{'id':'valid','action':'BUY_LONG'}]})[0],'WAIT')

    def test_real_candidate_reconstruction_preserves_low_adx_exception_in_both_directions(self):
        from test_entry_candidates import package,selection
        from scripts import entry_candidates,trading_prompt
        for side in ('long','short'):
            p=package(side);p['adx_1h']=16
            plan=entry_candidates.catalog(p)['plans'][0]
            d=trading_prompt.candidate(p,selection(plan),trading_prompt.facts_for(p))
            self.assertTrue(d['contract_valid'])
            self.assertEqual(self.pipeline(p,d)[0],d['action'])
            for change in ({'candidate_id':'fake'},{'candidate_id':None},{'contract_valid':False}):
                self.assertEqual(self.pipeline(p,{**d,**change})[0],'WAIT')

    def test_invalid_pipeline_objects_and_tampered_config_fail_without_host_callbacks(self):
        class BadDict(dict):
            def get(self,*args):raise AssertionError('unsafe mapping callback')
        self.assertEqual(manager.run_interceptor_pipeline(BadDict(),self.decision,{})[0],'WAIT')
        manager.ensure_plugins_dir();self.config.write_text('{broken',encoding='utf-8')
        self.assertEqual(self.pipeline()[0],'WAIT')
        with self.assertRaises(ValueError):manager.create_plugin('new',PASS)
        self.assertEqual(self.config.read_text(),'{broken')

    def test_safe_legacy_filenames_and_unrelated_config_fields_remain_compatible(self):
        manager.save_config({'pipeline_order':manager.DEFAULT_ORDER[:], 'enabled':manager.DEFAULT_ENABLED.copy(), 'legacy_note':'preserve'})
        for name in ('custom.v2.py','_legacy.py','custom rule.py','自定义规则.py'):
            self.assertEqual(manager.create_plugin(name,PASS)['filename'],name)
        self.assertEqual(manager.load_config()['legacy_note'],'preserve')
        self.assertEqual(self.pipeline()[0],'BUY_LONG')
        for name in ('CON.py','nul.py','LPT1.py'):
            with self.assertRaises(ValueError):manager.create_plugin(name,PASS)

    def test_rule_cannot_mutate_shared_candidate_to_bypass_following_builtin(self):
        manager.create_plugin('pretend',code("decision = {'action': 'WAIT'}\npackage = {'macro_4h': '4H_MACRO_BULL'}\nreturn True, ''"))
        manager.reorder_plugins(['pretend.py']+manager.DEFAULT_ORDER)
        self.assertEqual(self.pipeline(p={**self.package,'macro_4h':'4H_MACRO_BEAR'})[0],'WAIT')

    def test_infinite_rule_try_handler_cannot_turn_budget_failure_into_approval(self):
        manager.create_plugin('loop',code("try:\n    while True:\n        pass\nexcept Exception:\n    return True, ''\nreturn True, ''"))
        result=self.pipeline()
        self.assertEqual(result[0],'WAIT')
        self.assertIn('budget',result[1])

    def test_disabled_template_can_be_enabled_and_enforces_its_existing_rules(self):
        manager.toggle_plugin('99_custom_template_sample.py',True)
        self.assertEqual(self.pipeline(p={**self.package,'acceleration_a':-.7})[0],'WAIT')
        self.assertEqual(self.pipeline(p={**self.package,'acceleration_a':0,'smart_money':{'net_flow_usdt':-10000001}})[0],'WAIT')
        self.assertEqual(self.pipeline(p={**self.package,'acceleration_a':-.6,'smart_money':{'net_flow_usdt':-10000000}})[0],'BUY_LONG')

    def test_fixed_plan_adapter_is_not_called_for_unrelated_rules_or_invalid_contract(self):
        with patch('scripts.entry_candidates.catalog',side_effect=AssertionError('unexpected plan call')) as provider:
            self.assertEqual(self.pipeline()[0],'BUY_LONG')
            self.assertEqual(self.pipeline(d={**self.decision,'contract_valid':True,'candidate_id':'would-rebuild'})[0],'BUY_LONG')
            self.assertEqual(self.pipeline(p={**self.package,'adx_1h':16})[0],'WAIT')
        provider.assert_not_called()

    def test_custom_only_delete_does_not_exhaust_tombstone_capacity(self):
        for i in range(8):
            name=f'ephemeral_{i}.py'
            manager.create_plugin(name,PASS);manager.delete_plugin(name)
        self.assertEqual(manager.load_config().get('deleted',[]),[])
        self.assertEqual(len(manager.list_plugins()),5)

    def test_deeply_corrupt_configuration_does_not_reset_or_bypass_rules(self):
        manager.ensure_plugins_dir()
        raw='['*1500+'0'+']'*1500
        self.config.write_text(raw,encoding='utf-8')
        self.assertEqual(self.pipeline()[0],'WAIT')
        self.assertEqual(self.config.read_text(),raw)
