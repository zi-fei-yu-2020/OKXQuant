"""Offline invariants for CPU savings, cache invalidation and incremental accounting."""
import copy
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import json_projection_cache as cache, ledger_monitor as monitor
from scripts import ledger_incremental as inc, risk_policy as risk, db_manager as mirror
from scripts.fill_accounting import FillArchive


class GenerationCacheTests(unittest.TestCase):
    def test_one_parse_for_unchanged_generation_and_atomic_replace_invalidates(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'source.json';path.write_text('[1]')
            view=cache.VersionedJsonProjection(tuple)
            with patch.object(cache.json,'load',wraps=json.load) as decode:
                for _ in range(100):self.assertEqual(view.read(path),(1,))
                self.assertEqual(decode.call_count,1)
                old=path.stat();replacement=Path(d)/'replace.json';replacement.write_text('[2]')
                os.utime(replacement,ns=(old.st_atime_ns,old.st_mtime_ns));os.replace(replacement,path)
                self.assertEqual(view.read(path),(2,))
                self.assertEqual(decode.call_count,2)

    def test_corruption_and_missing_file_never_serve_cached_good_data(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'source.json';path.write_text('[1]')
            view=cache.VersionedJsonProjection(tuple);self.assertEqual(view.read(path),(1,))
            replacement=Path(d)/'bad.json';replacement.write_text('bad');os.replace(replacement,path)
            with self.assertRaises(ValueError):view.read(path)
            path.unlink()
            with self.assertRaises(OSError):view.read(path)

    def test_scheduler_preserves_fast_paths_but_does_not_parse_ledger_each_tick(self):
        with tempfile.TemporaryDirectory() as d,patch.object(monitor,'DATA',Path(d)):
            monitor.atomic('trading_ledger.json',[{'status':'holding'}])
            with patch.object(cache.json,'load',wraps=json.load) as decode:
                for now in range(1,60):self.assertFalse(monitor.should_run(0,now))
                self.assertEqual(decode.call_count,0)
                for now in range(60,100):self.assertTrue(monitor.should_run(0,now))
                self.assertEqual(decode.call_count,1)
                monitor.atomic('trading_ledger.json',[{'status':'closed'}])
                self.assertFalse(monitor.should_run(0,100))
                self.assertEqual(decode.call_count,2)
                monitor.atomic('ledger_refresh_request.json',{'id':'new','at':100})
                self.assertFalse(monitor.should_run(100,104))
                self.assertTrue(monitor.should_run(100,105))

    def test_daily_risk_projection_is_account_scoped_and_invalidates_on_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'data').mkdir();(root/'scripts').mkdir()
            path=root/'data'/'trading_ledger.json'
            rows=[{'environment_id':'a','status':'closed','close_time':'2026-10-08 10:00:00','net_pnl':-10,'opening_features':{'large':'x'*10000}},
                  {'environment_id':'b','status':'closed','close_time':'2026-10-08 10:00:00','net_pnl':-1000}]
            path.write_text(json.dumps(rows))
            now=datetime(2026,10,8,12,tzinfo=timezone(timedelta(hours=8)))
            with patch.object(risk,'__file__',str(root/'scripts'/'risk_policy.py')),patch.object(cache.json,'load',wraps=json.load) as decode:
                def evaluate():return risk.ledger_daily_drawdown(now=now,initial_capital=1000,reset_time='1970-01-01 00:00:00',scope='a')
                self.assertEqual(evaluate()['net_pnl'],-10)
                self.assertEqual(evaluate()['net_pnl'],-10)
                self.assertEqual(decode.call_count,1)
                rows[0]['net_pnl']=-40;replacement=root/'data'/'replace.json';replacement.write_text(json.dumps(rows));os.replace(replacement,path)
                self.assertTrue(evaluate()['blocked'])
                self.assertEqual(decode.call_count,2)


class MirrorDeltaTests(unittest.TestCase):
    def test_in_memory_sync_skips_decode_and_only_writes_changed_rows(self):
        with tempfile.TemporaryDirectory() as d,patch.object(mirror,'DB_PATH',str(Path(d)/'db.sqlite')),patch.object(mirror,'DATA_DIR',d):
            rows=[{'id':'a','status':'closed','pnl':None,'fee':0,'gross_pnl':1}]
            self.assertEqual(mirror.sync_json_to_sqlite(Path(d)/'does-not-exist',trades=rows),1)
            with sqlite3.connect(mirror.DB_PATH) as db:first_id=db.execute('select id from trades').fetchone()[0]
            self.assertEqual(mirror.sync_json_to_sqlite(trades=rows),0)
            rows[0]['pnl']=0
            self.assertEqual(mirror.sync_json_to_sqlite(trades=rows),1)
            with sqlite3.connect(mirror.DB_PATH) as db:
                self.assertEqual(db.execute('select id,pnl from trades').fetchone(),(first_id,0))


class RevisionTests(unittest.TestCase):
    def fixture(self):
        inst='BTC-USDT-SWAP'
        receipt={'instId':inst,'posId':'p','cTime':'1000','uTime':'2000','direction':'long','fee':'-.03'}
        archive=FillArchive('available',{inst:[{'tradeId':'t','ordId':'o','fee':'-.03'}]})
        origins={'o':{'instId':inst,'opening_features':{'price':100}}}
        observations={(inst,'p','1000'):[{'at':1.5,'position':{'upl':'1'}}]}
        inputs={'orders':[{'instId':inst,'ordId':'close'}],'algos':[],'executions':[]}
        def revision(r=receipt,a=archive,o=origins,obs=observations,i=inputs,p=()):
            return inc.ClosedReceiptRevisions('a','1970',[{'instId':inst,'ctVal':1}],a,o,obs,i,[r],p).for_receipt(r)
        return receipt,archive,origins,observations,inputs,revision

    def row(self,revision):
        row={'status':'closed','evidence_status':'complete','source_status':'linked','position_created_at':'1000',
             'fee_reconciliation':{'status':'verified'},'attribution_status':'verified',
             'net_pnl':1.,'gross_pnl':1.03,'fee':-.03,'open_fee':-.01,'close_fee':-.02}
        inc.stamp(row,revision);return row

    def test_receipt_fills_origin_observation_and_exit_changes_all_invalidate(self):
        receipt,archive,origins,obs,inputs,revision=self.fixture()
        initial=revision();row=self.row(initial)
        self.assertTrue(inc.reusable(row,revision()))
        r={**receipt,'fee':'-.04'};self.assertFalse(inc.reusable(row,revision(r=r)))
        a=FillArchive('available',{receipt['instId']:[{'ordId':'o','fee':'-.04'}]});self.assertFalse(inc.reusable(row,revision(a=a)))
        o=copy.deepcopy(origins);o['o']['opening_features']['price']=101;self.assertFalse(inc.reusable(row,revision(o=o)))
        samples=copy.deepcopy(obs);samples[next(iter(samples))][0]['position']['upl']='2';self.assertFalse(inc.reusable(row,revision(obs=samples)))
        exits=copy.deepcopy(inputs);exits['orders'][0]['ordId']='new';self.assertFalse(inc.reusable(row,revision(i=exits)))
        row['net_pnl']=999;self.assertFalse(inc.reusable(row,initial))

    def test_partial_inputs_and_partial_prior_evidence_are_never_reused(self):
        r,a,o,obs,inputs,revision=self.fixture();row=self.row(revision())
        self.assertIsNone(revision(a=FillArchive('unavailable',{})))
        self.assertIsNone(revision(o={}))
        self.assertIsNone(revision(obs={}))
        row['fee_reconciliation']['status']='unverified'
        self.assertFalse(inc.reusable(row,revision()))

    def test_active_market_repricing_is_irrelevant_but_identity_and_size_changes_invalidate(self):
        r,a,o,obs,inputs,revision=self.fixture()
        p={'instId':r['instId'],'posSide':'long','pos':'1','posId':'new','cTime':'3000','markPx':'100'}
        before=revision(p=[p]);p['markPx']='101';self.assertEqual(revision(p=[p]),before)
        p['pos']='2';self.assertNotEqual(revision(p=[p]),before)


class DashboardIncrementalTests(unittest.TestCase):
    def test_ai_summaries_parse_only_when_source_changes(self):
        import dashboard.app as dash
        with tempfile.TemporaryDirectory() as d,patch.object(dash,'AI_HISTORY_FILE',str(Path(d)/'history.json')):
            Path(dash.AI_HISTORY_FILE).write_text(json.dumps([{'id':'a','macro_assessment':'text','ai_last_prompt':'full'}]))
            with patch.object(cache.json,'load',wraps=json.load) as decode:
                for _ in range(20):self.assertEqual(dash._ai_history_summaries()[0]['history_id'],'a')
                self.assertEqual(decode.call_count,1)
                Path(dash.AI_HISTORY_FILE).write_text(json.dumps([{'id':'b'}]))
                self.assertEqual(dash._ai_history_summaries()[0]['history_id'],'b')
                self.assertEqual(decode.call_count,2)

    def test_disk_checkpoint_is_compact_rate_limited_and_account_switch_immediate(self):
        import dashboard.app as dash
        with tempfile.TemporaryDirectory() as d,patch.object(dash,'DASHBOARD_CACHE_FILE',str(Path(d)/'cache.json')),patch.object(dash.time,'monotonic',return_value=100):
            data={'account_source_id':'a','account':{'total_eq':1},'trades':[{'id':'t','pos_id':'p','net_pnl':1,'opening_features':{'large':'x'*100000}}]}
            dash.persist_dashboard_cache(data)
            saved=json.loads(Path(dash.DASHBOARD_CACHE_FILE).read_text())
            self.assertNotIn('opening_features',saved['trades'][0]);self.assertEqual(saved['trades'][0]['pos_id'],'p')
            self.assertLess(Path(dash.DASHBOARD_CACHE_FILE).stat().st_size,1000)
            data['account']['total_eq']=2;self.assertFalse(dash.persist_dashboard_cache(data))
            self.assertEqual(json.loads(Path(dash.DASHBOARD_CACHE_FILE).read_text())['account']['total_eq'],1)
            data['account_source_id']='b';dash.persist_dashboard_cache(data)
            self.assertEqual(json.loads(Path(dash.DASHBOARD_CACHE_FILE).read_text())['account_source_id'],'b')

    def test_background_worker_does_not_repeat_a_recent_on_demand_refresh(self):
        import dashboard.app as dash
        def stop(_):dash._BG_WORKER_RUNNING=False
        with patch.object(dash,'_BG_WORKER_RUNNING',True),patch.object(dash,'CACHE_DATA',{'account':{'total_eq':1}}),patch.object(dash,'LAST_CACHE_TIME',100),patch.object(dash.time,'time',return_value=102),patch.object(dash.time,'sleep',side_effect=[None,None]),patch.object(dash,'update_cache_cycle') as update:
            count=0
            def sleep(_):
                nonlocal count
                count+=1
                if count>=2:dash._BG_WORKER_RUNNING=False
            with patch.object(dash.time,'sleep',side_effect=sleep):dash._dashboard_background_worker_loop()
            update.assert_not_called()


class IncrementalLedgerIntegrationTests(unittest.TestCase):
    def setUp(self):
        from scripts import sync_full_ledger as ledger, strategy_evidence as evidence
        self.ledger=ledger;self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.scope='okx:demo:cpu-fixture';self.inst='BTC-USDT-SWAP'
        for module,name,value in ((monitor,'DATA',self.root),(ledger,'DATA_DIR',str(self.root)),
                (ledger,'LEDGER_JSON_FILE',str(self.root/'ledger.json')),
                (ledger,'POSITION_TRACKER_FILE',str(self.root/'trackers.json')),
                (ledger,'INITIAL_STATE_FILE',str(self.root/'baseline.json')),
                (evidence,'DB_PATH',self.root/'events.db')):
            self.stack.enter_context(patch.object(module,name,value))
        self.stack.enter_context(patch.object(ledger,'selected_environment',return_value=SimpleNamespace(identity=self.scope,mode='demo')))
        self.stack.enter_context(patch('okxquant_backend.account_baseline.load_account_baseline',return_value={'reset_time':'1970-01-01 00:00:00'}))
        self.stack.enter_context(patch.object(ledger,'TARGET_INSTRUMENTS',[{'instId':self.inst,'name':'BTC','ctVal':1}]))
        self.receipt={'instId':self.inst,'direction':'long','posId':'p','cTime':'1000000','uTime':'2000000',
                      'openAvgPx':'100','closeAvgPx':'110','closeTotalPos':'1','lever':'3',
                      'pnl':'10','fee':'-.03','fundingFee':'0','realizedPnl':'9.97'}
        self.fills=[{'instId':self.inst,'billId':'f1','ordId':'open','tradeId':'t1','ts':'1001000','posSide':'long','side':'buy','fillSz':'1','fee':'-.01','feeCcy':'USDT'},
                    {'instId':self.inst,'billId':'f2','ordId':'close','tradeId':'t2','ts':'1999000','posSide':'long','side':'sell','fillSz':'1','fee':'-.02','feeCcy':'USDT'}]
        self.origins={'open':{'instId':self.inst,'side':'long','strategy':'fixture','strategy_evidence':'opening_fill_order_decision_link',
              'decision_id':'d','candidate_id':'c','horizon':'scalp','strategy_type':'test','setup':'test','strategy_version':'v1',
              'opening_features':{'source':'closed-candles'},'news_snapshot':{'status':'fresh'}}}
        self.observations={(self.inst,'p','1000000'):[{'at':1999,'position':{'upl':'10','markPx':'110'}}]}
        self.attribution={'exit_reason':'verified fixture','exit_source':'exchange_oco','attribution_status':'verified',
                          'close_order_ids':['close'],'close_order_sources':[],'exit_evidence':'fixture','attribution_note':''}
        self.stack.enter_context(patch.object(ledger,'close_inputs',return_value={'orders':[],'algos':[],'executions':[]}))
        self.stack.enter_context(patch.object(ledger,'read_fill_archive',side_effect=lambda _:FillArchive('available',{self.inst:self.fills})))
        self.stack.enter_context(patch('scripts.strategy_origin.index',side_effect=lambda _,**kwargs:self.origins))
        self.stack.enter_context(patch('scripts.trade_quality.observation_index',side_effect=lambda _:self.observations))
        self.attribution_calls=self.stack.enter_context(patch.object(ledger,'close_reason',side_effect=lambda *a,**kw:dict(self.attribution)))
        self.reconcile=self.stack.enter_context(patch.object(ledger,'reconcile_fill_fees',wraps=ledger.reconcile_fill_fees))
        self.stack.enter_context(patch('scripts.horizon_stats.write'))

    def build(self):
        with patch.object(self.ledger,'read_snapshot',side_effect=[[self.receipt],[],[]]):
            return self.ledger.build_lifecycle_ledger(notify=False)

    def test_identical_proven_receipt_reuses_row_and_skips_rebuild_and_disk_write(self):
        first=self.build();self.assertEqual(first[0]['evidence_status'],'complete')
        self.assertEqual(first[0]['fee_reconciliation']['status'],'verified')
        self.assertIn('reconciliation_revision',first[0])
        before=Path(self.ledger.LEDGER_JSON_FILE).stat().st_mtime_ns
        self.reconcile.reset_mock();self.attribution_calls.reset_mock()
        second=self.build()
        self.assertEqual(second,first)
        self.reconcile.assert_not_called();self.attribution_calls.assert_not_called()
        self.assertEqual(Path(self.ledger.LEDGER_JSON_FILE).stat().st_mtime_ns,before)

    def test_late_financial_correction_and_new_origin_evidence_recompute(self):
        self.build();self.reconcile.reset_mock()
        self.receipt={**self.receipt,'fee':'-.04','realizedPnl':'9.96'}
        self.fills[1]['fee']='-.03'
        corrected=self.build()[0]
        self.reconcile.assert_called_once()
        self.assertEqual(corrected['net_pnl'],9.96);self.assertAlmostEqual(corrected['open_fee']+corrected['close_fee'],-.04)
        self.reconcile.reset_mock();self.origins['open']['opening_features']['additional']='late evidence'
        self.build();self.reconcile.assert_called_once()
