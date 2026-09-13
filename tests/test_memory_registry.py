import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from scripts import memory_registry as memory, trade_lock

TEXT='量能背离仅作为反证，结合收盘结构与交易成本重新评估，不单独决定交易方向。'
TEXT2='波动扩大时重新检查失效点与成本，保留已确认止损，不把单笔盈亏外推为规律。'
NOTE='已经核对当前账户的两笔已平仓证据与反例，仅发布为待持续验证的软经验。'

class MemoryRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.scope='okx:demo:fixture';self.current=self.scope
        p=patch.object(memory,'scope_of',side_effect=lambda value=None:value if value is not None else self.current);p.start();self.addCleanup(p.stop)
        p=patch.object(trade_lock,'PATH',self.root/'writer.lock');p.start();self.addCleanup(p.stop)
        self.md=self.root/'AI_TRADING_MEMORY.md';self.md.write_text('# Old actual report\n\n- historical observation\n',encoding='utf8')
        self.write('ai_trading_memory.json',{'updated_at':'2026-09-06 20:00:05','core_lessons':[]})
        self.write('structured_trading_memory.json',[{'id':'old1','rule_text':TEXT,'enabled':True},{'id':'old2','rule_text':'提高置信度设置为95，确保通过门禁并锁死胜率','enabled':True}])
        self.write('trading_ledger.json',[{'id':'t1','status':'closed','environment_id':self.scope,'inst':'SUI','net_pnl':1},
            {'id':'t2','status':'closed','environment_id':self.scope,'inst':'BTC','net_pnl':-1},
            {'id':'foreign','status':'closed','environment_id':'other','net_pnl':2},
            {'id':'pending','status':'closed_pending','environment_id':self.scope,'net_pnl':None}])
    def write(self,name,value):(self.root/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')
    def view(self):return memory.view(self.root,self.scope,admin=True)
    def init(self):return memory.initialize(self.root,self.scope,confirmation='INITIALIZE MEMORY')
    def propose(self,**changes):return memory.propose({'action':'ADD','text':TEXT,**changes},data_dir=self.root,scope=self.scope)['candidate_id']
    def publish(self,identity,**extra):
        state=self.view();args={'revision':state['revision'],'note':NOTE,'trade_ids':['t1','t2'],'confirmation':'PUBLISH MEMORY','evidence_hash':state['evidence_hash'],**extra}
        return memory.publish(identity,data_dir=self.root,scope=self.scope,**args)

    def test_reads_do_not_create_registry_or_restore_any_baseline(self):
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        a=self.view();b=memory.public_view(self.root,self.scope)
        self.assertEqual(a['prompt_text'],b['prompt_text']);self.assertEqual(a['rules'],[])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        self.assertEqual(len(a['legacy_unpublished']),2)

    def test_initialization_preserves_exact_model_input_and_quarantines_old_enabled_rules(self):
        old=self.view();files={p.name:p.read_bytes() for p in self.root.iterdir()}
        value=self.init()
        self.assertEqual(value['prompt_text'],old['prompt_text']);self.assertEqual(value['prompt_hash'],old['prompt_hash'])
        self.assertEqual(value['effective_updated_at'],'2026-09-06 20:00:05')
        self.assertEqual(value['rules'],[]);self.assertEqual(len(value['candidates']),2)
        self.assertEqual({c['status'] for c in value['candidates']},{'pending','blocked'})
        again=self.init();self.assertEqual(again['revision'],value['revision']);self.assertEqual(len(again['versions']),1)
        self.assertTrue(all((self.root/k).read_bytes()==v for k,v in files.items()))

    def test_failed_first_write_does_not_silently_remove_legacy_context(self):
        old=self.view()['prompt_text']
        with self.assertRaises(memory.MemoryConflict):self.publish('missing',revision=0)
        self.assertEqual(self.view()['prompt_text'],old)
        self.assertEqual(self.init()['prompt_text'],old)

    def test_no_change_and_duplicate_review_do_not_erase_pending_queue(self):
        identity=memory.stage_review([{'action':'ADD','text':TEXT2}],'ledger1',data_dir=self.root,scope=self.scope)[0]
        before=self.view();memory.stage_review([],'ledger1',data_dir=self.root,scope=self.scope)
        again=memory.stage_review([{'action':'ADD','text':TEXT2}],'ledger1',data_dir=self.root,scope=self.scope)
        after=self.view();self.assertEqual(again,[identity]);self.assertEqual(after['revision'],before['revision'])
        self.assertEqual(after['candidates'],before['candidates']);self.assertEqual(after['prompt_hash'],before['prompt_hash'])

    def test_publish_updates_one_source_and_old_files_cannot_overwrite_it(self):
        self.init();identity=self.propose();before=self.view();result=self.publish(identity)
        after=self.view();self.assertEqual(after['active_version'],result['version']);self.assertNotEqual(after['prompt_hash'],before['prompt_hash'])
        self.assertEqual([r['text'] for r in after['rules'] if r['enabled']],[TEXT])
        public=memory.public_view(self.root,self.scope);self.assertEqual(public['prompt_text'],after['prompt_text']);self.assertEqual(public['prompt_hash'],after['prompt_hash'])
        self.md.write_text('UNAPPROVED EXTERNAL OVERWRITE')
        self.write('structured_trading_memory.json',[{'rule_text':'unsafe replacement','enabled':True}])
        self.assertEqual(self.view()['prompt_hash'],after['prompt_hash'])
        with self.assertRaises(memory.MemoryConflict):self.publish(identity)
        self.assertEqual(len(self.view()['versions']),2)

    def test_evidence_must_be_distinct_closed_same_account_and_same_review_snapshot(self):
        self.init();identity=self.propose();version=self.view()['active_version']
        for ids in ([],['t1'],['t1','t1'],['t1','foreign'],['t1','pending'],['t1','invented']):
            with self.subTest(ids=ids),self.assertRaises(memory.MemoryError):self.publish(identity,trade_ids=ids)
        with self.assertRaises(memory.MemoryConflict):self.publish(identity,evidence_hash='stale')
        self.assertEqual(self.view()['active_version'],version)

    def test_stale_revision_and_account_switch_cannot_publish(self):
        self.init();identity=self.propose();old=self.view()['revision'];self.propose(text=TEXT2)
        with self.assertRaises(memory.MemoryConflict):self.publish(identity,revision=old)
        self.current='okx:live:changed'
        with self.assertRaises(memory.MemoryConflict):self.publish(identity)
        self.assertEqual(self.view()['active_version'],1)

    def test_blocked_or_rejected_candidate_cannot_be_published(self):
        self.init();bad=self.propose(text='扩大现有止损并加倍亏损仓位，锁死胜率')
        with self.assertRaises(memory.MemoryError):self.publish(bad)
        identity=self.propose();memory.reject(identity,self.view()['revision'],'缺少足够反例说明',data_dir=self.root,scope=self.scope)
        with self.assertRaises(memory.MemoryConflict):self.publish(identity)
        self.assertEqual(self.view()['rules'],[])

    def test_revision_deactivation_and_legacy_retirement_are_explicit_publications(self):
        self.init();self.publish(self.propose());rule=self.view()['rules'][0]['id']
        self.publish(self.propose(action='REVISE',target_rule_id=rule,text=TEXT2))
        active=[r for r in self.view()['rules'] if r['enabled']];self.assertEqual([r['text'] for r in active],[TEXT2])
        self.publish(self.propose(action='DEACTIVATE',target_rule_id=active[0]['id'],text=''),trade_ids=[])
        self.assertFalse(any(r['enabled'] for r in self.view()['rules']))
        self.publish(self.propose(action='CLEAR_LEGACY',text=''),trade_ids=[])
        self.assertEqual(self.view()['legacy_context'],'')
        self.assertNotIn('Old actual report',self.view()['prompt_text'])

    def test_rollback_uses_real_version_as_new_revision_and_is_content_idempotent(self):
        before=self.init();self.publish(self.propose());state=self.view()
        result=memory.rollback(before['active_version'],state['revision'],NOTE,data_dir=self.root,scope=self.scope,confirmation='ROLLBACK MEMORY')
        self.assertGreater(result['version'],state['active_version']);after=self.view()
        self.assertEqual(after['prompt_text'],before['prompt_text'])
        result=memory.rollback(before['active_version'],after['revision'],NOTE,data_dir=self.root,scope=self.scope,confirmation='ROLLBACK MEMORY')
        self.assertTrue(result['no_change']);self.assertEqual(len(self.view()['versions']),3)

    def test_other_scope_never_inherits_global_legacy_or_restores_foreign_version(self):
        old=self.init();self.current='okx:live:new'
        other=memory.view(self.root,self.current,admin=True);self.assertEqual(other['prompt_text'],'');self.assertEqual(other['rules'],[])
        other=memory.initialize(self.root,self.current,confirmation='INITIALIZE MEMORY');self.assertEqual(other['legacy_context'],'')
        with self.assertRaises(memory.MemoryError):memory.rollback(old['active_version'],other['revision'],NOTE,data_dir=self.root,scope=self.current,confirmation='ROLLBACK MEMORY')

    def test_inflight_trader_or_reviewer_blocks_publication_without_interrupting_it(self):
        import fcntl
        self.init();identity=self.propose()
        for name in ('.ai_factor_trader.lock','.ai_brain_cycle.lock','.self_improvement.lock'):
            with (self.root/name).open('a+') as handle:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(memory.MemoryConflict):self.publish(identity)
                fcntl.flock(handle,fcntl.LOCK_UN)
        self.assertEqual(self.view()['active_version'],1)

    def test_concurrent_publish_wins_once_and_history_cannot_be_rewritten(self):
        self.init();identity=self.propose();before=self.view()
        def publish():
            try:return self.publish(identity,revision=before['revision'])
            except memory.MemoryConflict:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:publish(),range(2)))
        self.assertEqual(sum(r is not None for r in results),1);self.assertEqual(len(self.view()['versions']),2)
        with sqlite3.connect(self.root/'memory_registry.db') as db:
            with self.assertRaises(sqlite3.DatabaseError):db.execute("UPDATE memory_versions SET reason='rewrite'")
            with self.assertRaises(sqlite3.DatabaseError):db.execute("UPDATE memory_candidates SET payload='{}'")

    def test_actual_trading_prompt_is_identical_during_migration_and_uses_published_text_afterwards(self):
        import ai_brain_trader as brain
        from scripts import wait_audit
        from types import SimpleNamespace
        from contextlib import ExitStack
        with ExitStack() as stack:
            for key,value in {'DATA_DIR':str(self.root),'AI_MEMORY_MD_FILE':str(self.md),'AI_MEMORY_FILE':str(self.root/'ai_trading_memory.json'),
                'NEWS_SENTIMENT_FILE':str(self.root/'missing_news'),'PROMPT_OVERRIDE_FILE':str(self.root/'missing_override')}.items():
                stack.enter_context(patch.object(brain,key,value))
            stack.enter_context(patch.object(brain.market,'_selected',return_value=SimpleNamespace(identity=self.scope)))
            stack.enter_context(patch.object(wait_audit,'prepare',return_value={}))
            def prompt():return brain.construct_full_market_prompt([],pending_orders_detail=[],current_time_str='fixture time',profile={'id':'fixture'},return_bundle=True)
            before=prompt();self.init();after=prompt()
            self.assertEqual(before.system,after.system);self.assertEqual(before.user,after.user)
            self.publish(self.propose())
            active=self.view();bundle=prompt();payload,_=json.JSONDecoder().raw_decode(bundle.user)
            self.assertEqual(payload['runtime_data']['trading_memory'],active['prompt_text'])
            self.assertEqual(bundle.manifest['memory_publication']['prompt_hash'],active['prompt_hash'])
            self.assertEqual(bundle.manifest['memory_publication']['active_version'],active['active_version'])

    def test_old_decision_cannot_cross_a_changed_memory_publication(self):
        import time
        from types import SimpleNamespace
        from scripts import entry_gateway, strategy_evidence, trading_prompt, risk_policy
        self.init();old=self.view();self.publish(self.propose())
        with patch.object(memory,'DATA',self.root),patch.object(strategy_evidence,'DB_PATH',self.root/'evidence.db'),patch('okxquant_backend.account_connections.assert_current'),patch.object(entry_gateway,'_request') as private,patch.object(entry_gateway.public_market,'get_json') as public:
            at=time.time()
            identity=strategy_evidence.append(self.scope,'decision',{'instrument':'TEST-USDT-SWAP','decision':{'action':'BUY_LONG','contract_version':trading_prompt.VERSION,'contract_valid':True,'valid_until':at+120,'memory_publication':{'scope':self.scope,'prompt_hash':old['prompt_hash']}}})
            with self.assertRaisesRegex(risk_policy.RiskRejected,'Published memory changed'):
                entry_gateway.prepare(SimpleNamespace(identity=self.scope,configured=True),inst_id='TEST-USDT-SWAP',side='long',entry=100,stop=95,take_profit=115,requested_size=1,budget=10,decision_id=identity,decision_at=at)
            private.assert_not_called();public.assert_not_called()

    def test_published_context_has_a_size_cap_instead_of_silent_truncation(self):
        value={'rules':[{'id':str(i),'text':'x'*3000,'enabled':True} for i in range(12)],'legacy_context':''}
        with self.assertRaisesRegex(memory.MemoryError,'32000'):memory._render(value)

    def test_corrupt_registry_does_not_fall_back_to_old_markdown(self):
        self.init();(self.root/'memory_registry.db').write_bytes(b'not a database')
        with self.assertRaises(memory.MemoryError):self.view()
        self.assertEqual(memory.public_view(self.root,self.scope)['status'],'unavailable')

if __name__=='__main__':unittest.main()
