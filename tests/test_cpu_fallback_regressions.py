"""October 8 production regressions: bounded fallbacks and compact evidence reads."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from scripts import evidence_projection as projection, horizon_funnel as funnel
from scripts import maker_fallback as fallback, strategy_evidence as evidence, entry_gateway as gateway
from scripts.risk_policy import RiskRejected


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'evidence.db'
        self.patch=patch.object(evidence,'DB_PATH',self.path);self.patch.start();self.addCleanup(self.patch.stop)
        self.now=time.time();self.scope='demo:test'
    def add(self, identity, **changes):
        payload={'decision':{'horizon':'scalp','decision_status':'entry_candidate'},'features':{'large':'x'*10000},**changes}
        evidence.append(self.scope,'decision',payload,identity)
    def read(self, **kw):
        return funnel.rebuild([],scope=self.scope,now=self.now+1,db_path=self.path,**kw)
    def test_unchanged_large_blobs_are_projected_once_and_new_events_incremental(self):
        self.add('a');self.add('b')
        with patch.object(funnel,'_project_event',wraps=funnel._project_event) as project:
            first=self.read();second=self.read()
            self.assertEqual(first,second);self.assertEqual(project.call_count,2)
            self.add('c');third=self.read();self.assertEqual(project.call_count,3)
        self.assertEqual(third['1d']['scalp']['decisions'],3)
        self.assertLess(self.path.with_name('evidence_projections.db').stat().st_size,25000)
    def test_digest_corrections_deletion_scope_and_window_are_not_stale(self):
        self.add('a');self.read()
        corrected={'decision':{'horizon':'swing','decision_status':'audited_wait'}}
        with evidence.connection() as db:
            db.execute('UPDATE events SET payload=?,digest=? WHERE id=?',(json.dumps(corrected),'changed','a'))
        result=self.read();self.assertEqual(result['1d']['scalp']['decisions'],0)
        self.assertEqual(result['1d']['swing']['audited_wait'],1)
        self.assertEqual(funnel.rebuild([],scope='other',now=self.now+1,db_path=self.path)['1d']['swing']['decisions'],0)
        self.assertEqual(funnel.rebuild([],scope=self.scope,now=self.now+8*86400,db_path=self.path)['7d']['swing']['decisions'],0)
        with evidence.connection() as db:db.execute('DELETE FROM events')
        self.assertEqual(self.read()['1d']['swing']['decisions'],0)
    def test_corrupt_source_does_not_serve_cached_success(self):
        self.add('a');self.read()
        with evidence.connection() as db:db.execute("UPDATE events SET payload='bad',digest='bad'")
        self.assertIn('evidence_error',self.read())
    def test_unwritable_or_corrupt_cache_falls_back_to_source(self):
        self.add('a');expected=self.read()
        self.path.with_name('evidence_projections.db').write_bytes(b'invalid sqlite')
        self.assertEqual(self.read(),expected)
    def test_rejection_links_to_decision_horizon_and_failed_writes_are_not_submitted(self):
        self.add('d')
        evidence.append(self.scope,'entry_rejection',{'decision_id':'d'})
        evidence.append(self.scope,'entry_submission',{'decision_id':'d','transport_ok':True,'failure':{'code':'x'}})
        a=self.read()['1d']['scalp'];self.assertEqual(a['risk_rejected'],1);self.assertEqual(a['submitted'],0)


class BoundedFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(evidence,'DB_PATH',Path(self.temp.name)/'evidence.db');p.start();self.addCleanup(p.stop)
        self.scope='okx:demo:test';self.inst='BTC-USDT-SWAP';self.did='original'
        self.record={'decision':{'valid_until':time.time()+60,'horizon':'scalp'},'features':{'closed':True},'as_of_ms':int(time.time()*1000)}
        evidence.append(self.scope,'decision',self.record,self.did)
        self.plan={'instId':self.inst,'side':'long','decision_id':self.did,'entry_execution_mode':'maker_first'}
        self.cid=evidence.begin_intent(self.scope,self.did,self.inst,self.plan)
        evidence.finish_intent(self.cid,'canceled')
        self.proof={'instId':self.inst,'clOrdId':self.cid,'ordId':'order','state':'canceled','accFillSz':'0'}
    def child(self,proof=None):
        return fallback.create_decision(self.scope,self.cid,self.did,self.inst,'long',self.proof if proof is None else proof)
    def child_record(self,child):
        with evidence.connection() as db:
            return json.loads(db.execute('SELECT payload FROM events WHERE id=?',(child,)).fetchone()[0])
    def test_distinct_single_child_preserves_original_decision_and_ttl(self):
        child=self.child();record=self.child_record(child)
        self.assertEqual(record['decision'],self.record['decision']);self.assertEqual(record['features'],self.record['features'])
        self.assertEqual(fallback.validated_parent(record,self.scope,self.inst,'long'),self.cid)
        self.assertEqual(self.child(),child)
        new=evidence.begin_intent(self.scope,child,self.inst,{**self.plan,'decision_id':child,'entry_execution_mode':'bounded_fallback'})
        self.assertNotEqual(new,self.cid)
        for did in (self.did,child):
            with self.assertRaises(sqlite3.IntegrityError):evidence.begin_intent(self.scope,did,self.inst,{})
    def test_partial_missing_invalid_and_mismatched_cancel_proof_never_authorizes(self):
        for delta in ({'accFillSz':'0.1'},{'accFillSz':None},{'accFillSz':'NaN'},{'accFillSz':'-1'},
                      {'clOrdId':'other'},{'instId':'ETH-USDT-SWAP'},{'state':'live'},{'ordId':''}):
            with self.subTest(delta=delta),self.assertRaises((RiskRejected,ValueError,TypeError)):
                self.child({**self.proof,**delta})
    def test_uncertain_or_filled_parent_never_authorizes(self):
        for state in ('unknown','acknowledged','pending','filled','not_found'):
            evidence.finish_intent(self.cid,state)
            with self.assertRaises(RiskRejected):self.child()
    def test_explicit_business_rejection_only(self):
        evidence.finish_intent(self.cid,'not_submitted')
        with self.assertRaises(RiskRejected):self.child({})
        child=self.child({'kind':'explicit_business_rejection','exchange_code':'51000'})
        self.assertEqual(fallback.validated_parent(self.child_record(child),self.scope,self.inst,'long'),self.cid)
    def test_only_verified_parent_is_exempt_other_correlated_reservations_still_block(self):
        child=self.child();parent=fallback.validated_parent(self.child_record(child),self.scope,self.inst,'long')
        slot=int(time.time())//900
        with self.assertRaises(RiskRejected):gateway.check_correlation(self.scope,slot,self.inst,'long')
        gateway.check_correlation(self.scope,slot,self.inst,'long',exempt_client=parent)
        evidence.begin_intent(self.scope,'other','ETH-USDT-SWAP',{'instId':'ETH-USDT-SWAP','side':'long'})
        with self.assertRaises(RiskRejected):gateway.check_correlation(self.scope,slot,self.inst,'long',exempt_client=parent)
    def test_scope_side_parent_state_and_nested_fallback_rechecked(self):
        child=self.child();record=self.child_record(child)
        for scope,side in [('other','long'),(self.scope,'short')]:
            with self.assertRaises(RiskRejected):fallback.validated_parent(record,scope,self.inst,side)
        evidence.finish_intent(self.cid,'unknown')
        with self.assertRaises(RiskRejected):fallback.validated_parent(record,self.scope,self.inst,'long')


class DispatchRegressionTests(unittest.TestCase):
    def run_route(self, *, filled='0', final_state='canceled', reject=False, explicit=False, unknown=False):
        import ai_factor_trader as trader
        from contextlib import ExitStack,nullcontext
        from scripts.okx_runtime import OKXEnvironment
        from scripts import trade_lock
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            env=OKXEnvironment('demo','fake','fake','fake');inst='BTC-USDT-SWAP';did='dispatch-fixture';plans=[];clients=[];commands=[]
            stack.enter_context(patch.object(evidence,'DB_PATH',Path(tmp)/'evidence.db'))
            stack.enter_context(patch.object(trade_lock,'PATH',Path(tmp)/'writer.lock'))
            evidence.append(env.identity,'decision',{'decision':{'valid_until':time.time()+60},'features':{}},did)
            def prepare(env,**kw):
                if kw['execution_mode']=='bounded_fallback' and reject:raise RiskRejected('fixture risk refusal')
                plan={**kw,'instId':inst,'size':1,'entry':kw['entry'],'stop':kw['stop'],'take_profit':kw['take_profit'],'entry_execution_mode':kw['execution_mode']}
                cid=evidence.begin_intent(env.identity,kw['decision_id'],inst,plan);plans.append(plan);clients.append(cid)
                return plan,cid
            def send(command,**kw):
                commands.append(command)
                if len(commands)==1 and unknown:return {'ok':False,'returncode':-1,'data':None,'error_type':'TimeoutError'}
                if len(commands)==1 and explicit:return {'ok':False,'returncode':0,'data':[{'sCode':'51000'}]}
                return {'ok':True,'returncode':0,'data':[{'sCode':'0','ordId':'order'+str(len(commands)),'clOrdId':clients[-1],'instId':inst}]}
            def request(method,path,params,env,**kw):
                if method=='POST':return [{'sCode':'0','ordId':'order1'}]
                return [{'instId':inst,'ordId':'order1','clOrdId':clients[0],'state':final_state,'accFillSz':filled}]
            for target,kwargs in [
                ('scripts.decision_authorization.entry_dispatch_guard',{'side_effect':lambda *a:nullcontext()}),
                ('scripts.maker_fallback.wait_once',{}),
                ('okxquant_backend.okx_trade_service._request',{'side_effect':request})]:stack.enter_context(patch(target,**kwargs))
            stack.enter_context(patch.object(trader.market,'_selected',return_value=env))
            stack.enter_context(patch.object(trader.support,'opening_status',return_value={'can_open':True}))
            stack.enter_context(patch.object(trader,'run_json_cmd',return_value=[{'last':'100'}]))
            stack.enter_context(patch.object(trader.market,'get_json',return_value={'data':[{'bids':[['99.99','1']],'asks':[['100.01','1']]}]}))
            stack.enter_context(patch.object(trader.entry_gateway,'prepare',side_effect=prepare))
            stack.enter_context(patch.object(trader,'save_horizon_intent'))
            stack.enter_context(patch.object(trader,'okx_private_command',side_effect=lambda x:x))
            stack.enter_context(patch.object(trader,'run_cmd_result',side_effect=send))
            outcome=trader.submit_protected_limit_order(inst,'buy','long',1,100,110,98,decision_id=did,decision_at=time.time(),horizon='scalp')
            stages=[e['payload'].get('stage') for e in evidence.export_events(env.identity,'maker_fallback')]
            return outcome,plans,commands,stages
    def test_zero_fill_cancel_allows_one_new_intent_using_original_geometry(self):
        result,plans,commands,_=self.run_route()
        self.assertTrue(result[0],result);self.assertEqual(len(commands),2)
        self.assertEqual(plans[0]['entry'],99.99);self.assertEqual(plans[1]['entry'],100)
        self.assertNotEqual(plans[0]['decision_id'],plans[1]['decision_id'])
    def test_partial_fill_on_terminal_cancel_never_resends_full_size(self):
        result,plans,commands,_=self.run_route(filled='0.2')
        self.assertTrue(result[0]);self.assertEqual(len(commands),1)
    def test_risk_preflight_failure_is_not_an_unknown_write(self):
        result,plans,commands,stages=self.run_route(reject=True)
        self.assertFalse(result[0]);self.assertIn('Fallback preflight rejected',result[1]);self.assertEqual(len(commands),1)
        self.assertIn('fallback_preflight_rejected',stages);self.assertNotIn('fallback_unknown',stages)
    def test_explicit_business_rejection_has_one_bounded_fallback(self):
        result,plans,commands,_=self.run_route(explicit=True)
        self.assertTrue(result[0],result);self.assertEqual(len(commands),2)
    def test_unknown_transport_never_falls_back(self):
        result,plans,commands,_=self.run_route(unknown=True)
        self.assertFalse(result[0]);self.assertEqual(len(commands),1)
    def test_missing_cumulative_fill_does_not_authorize_a_retry(self):
        result,plans,commands,_=self.run_route(filled=None)
        self.assertFalse(result[0]);self.assertEqual(len(commands),1)


class SettlementDiagnosticsTests(unittest.TestCase):
    def test_openings_and_settled_closes_are_distinct_without_fabricated_pnl(self):
        from datetime import datetime,timezone,timedelta
        from scripts.ledger_monitor import settlement_diagnostics
        now=datetime(2026,10,8,16,tzinfo=timezone(timedelta(hours=8))).timestamp()
        rows=[{'environment_id':'demo','open_time':'2026-10-07 23:46:28','close_time':'2026-10-08 00:01:55','status':'closed'},
              {'environment_id':'demo','open_time':'2026-10-08 13:03:34','close_time':'--','status':'closed_pending'},
              {'environment_id':'other','open_time':'2026-10-08 13:03:34','status':'closed_pending'}]
        before=copy.deepcopy(rows);s=settlement_diagnostics(rows,'demo',int((now-16*3600+115)*1000),now=now)
        self.assertEqual((s['known_opened_today'],s['settled_closes_today'],s['opened_today_pending_settlement']),(1,1,1))
        self.assertEqual(s['status'],'history_gap_observed');self.assertFalse(s['pnl_estimated']);self.assertEqual(rows,before)

if __name__=='__main__':unittest.main()
