import copy,json,tempfile,unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import nullcontext
from scripts import statistics_epoch as stats
from scripts.dashboard_stats import SHANGHAI
from okxquant_backend import account_baseline as baseline, monitor_statistics

SCOPE='okx:demo:epoch-fixture';OTHER='okx:live:other-fixture'
NOW=datetime(2026,10,10,11,0,tzinfo=SHANGHAI).timestamp()

def row(identity='old',opened='2026-10-10 08:00:00',closed='2026-10-10 09:00:00',scope=SCOPE,pnl=-30,status='closed'):
    return {'id':identity,'environment_id':scope,'inst':'BTC','instId':'BTC-USDT-SWAP','status':status,'open_time':opened,'close_time':closed,'net_pnl':pnl,'gross_pnl':pnl+2,'fee':-2,'funding_fee':0,'horizon':'swing'}

class StatisticsEpochTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.path=self.root/'account_initial_state.json'
        self.old={'account_scope':SCOPE,'initial_capital':1000,'baseline_configured':True,'reset_time':'2026-09-17 00:00:00'}
        self.other={'account_scope':OTHER,'initial_capital':5000,'baseline_configured':True,'reset_time':'2026-01-01 00:00:00'}
        self.path.write_text(json.dumps({'schema_version':2,**self.old,'baselines':{SCOPE:self.old,OTHER:self.other}}))
        self.ledger=self.root/'trading_ledger.json';self.ledger.write_text(json.dumps([row(),row('other',scope=OTHER)]))
        self.env=SimpleNamespace(mode='demo',identity=SCOPE,configured=True,connection_id='fixture',binding_version=1)
        for target,kwargs in [('okxquant_backend.account_baseline.BASELINE_FILE',{'new':self.path}),('scripts.okx_runtime.selected_environment',{'return_value':self.env}),('scripts.okx_runtime._load_dotenv',{'return_value':{'OKXQUANT_AUTOTRADE_ENABLED':'0'}}),('scripts.trade_lock.writer',{'side_effect':lambda **kw:nullcontext()}),('scripts.statistics_epoch.time.time',{'return_value':NOW})]:
            p=patch(target,**kwargs);p.start();self.addCleanup(p.stop)
        self.positions=[];self.pending=[];self.equity='970'
        def get(method,path,params,env,**kwargs):
            self.assertEqual(method,'GET')
            if path.endswith('/positions'):return self.positions
            if path.endswith('/orders-pending'):return self.pending
            if path.endswith('/balance'):return [{'totalEq':self.equity}]
            self.fail('unexpected endpoint')
        p=patch('okxquant_backend.okx_trade_service._request',side_effect=get);self.get=p.start();self.addCleanup(p.stop)
    def reset(self,operation='reset-fixture-1',**kwargs):return stats.reset_demo_statistics(operation,expected_scope=SCOPE,**kwargs)
    def test_reset_is_scoped_atomic_idempotent_and_does_not_delete_raw_receipts(self):
        raw=self.ledger.read_bytes();before_other=copy.deepcopy(self.other)
        out=self.reset();self.assertEqual(out['initial_capital'],970);self.assertEqual(out['archived_trade_rows'],1)
        self.assertEqual(self.ledger.read_bytes(),raw)
        doc=json.loads(self.path.read_text());self.assertEqual(doc['baselines'][OTHER],before_other)
        self.assertEqual(doc['risk_reset_time'],self.old['reset_time']);self.assertEqual(doc['canonical_history_start'],self.old['reset_time'])
        saved=self.path.read_bytes();self.assertEqual(self.reset()['status'],'already_reset');self.assertEqual(self.path.read_bytes(),saved)
        self.assertEqual(self.get.call_count,3)
    def test_other_account_and_live_account_cannot_be_reset(self):
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):stats.reset_demo_statistics('reset-fixture-1',expected_scope=OTHER)
        self.env.mode='live'
        with self.assertRaises(ValueError):self.reset()
        self.assertEqual(self.path.read_bytes(),before);self.get.assert_not_called()
    def test_trading_must_be_paused(self):
        with patch('scripts.okx_runtime._load_dotenv',return_value={'OKXQUANT_AUTOTRADE_ENABLED':'1'}),self.assertRaises(ValueError):self.reset()
        self.get.assert_not_called()
    def test_positions_pending_orders_and_bad_equity_block_reset(self):
        before=self.path.read_bytes()
        for positions,pending,equity in [([{'pos':'1'}],[],'970'),([],[{'ordId':'pending'}],'970'),([],[],'NaN')]:
            self.positions=positions;self.pending=pending;self.equity=equity
            with self.assertRaises(ValueError):self.reset()
            self.assertEqual(self.path.read_bytes(),before)
    def test_stale_ledger_holding_blocks_reset_without_hiding_it(self):
        self.ledger.write_text(json.dumps([row(status='holding')]))
        with self.assertRaises(ValueError):self.reset()
    def test_late_history_corrections_cannot_restore_old_statistics(self):
        self.reset()
        late=row(closed='2026-10-11 09:00:00')
        renamed=row('unknown-old',opened='2026-10-09 01:00:00',closed='2026-10-11 01:00:00')
        forged_date=row(opened='2026-10-10 11:01:00',closed='2026-10-10 12:00:00')
        fresh=row('new',opened='2026-10-10 11:01:00',closed='2026-10-10 12:00:00',pnl=5)
        other=row('other',scope=OTHER)
        self.assertEqual([r['id'] for r in stats.filter_rows([late,renamed,forged_date,fresh,other],data_dir=self.root)],['new','other'])
        self.assertEqual(stats.performance([fresh])['win_rate'],100)
    def test_unknown_openings_excluded_but_active_positions_remain_visible(self):
        self.reset();unknown=row('missing',opened='--',closed='2026-10-10 12:00:00');active=row(status='holding')
        self.assertEqual(stats.filter_rows([unknown],data_dir=self.root),[])
        self.assertEqual(stats.filter_rows([active],data_dir=self.root),[active])
        self.assertEqual(stats.filter_rows([active],data_dir=self.root,keep_active=False),[])
    def test_monitor_cache_invalidates_on_epoch_even_when_ledger_file_did_not_change(self):
        before=monitor_statistics.read_periods(self.ledger,SCOPE,now=NOW)
        self.assertEqual(before['periods']['all']['swing']['closed'],1)
        self.reset();after=monitor_statistics.read_periods(self.ledger,SCOPE,now=NOW)
        self.assertEqual(after['periods']['all']['swing']['closed'],0)
        self.assertEqual([r['id'] for r in monitor_statistics.read_ledger(self.ledger)],['other'])
    def test_daily_risk_ledger_loss_is_not_cleared_by_statistics_reset(self):
        from scripts.risk_policy import ledger_daily_drawdown
        raw=[row()];now=datetime.fromtimestamp(NOW,SHANGHAI)
        before=ledger_daily_drawdown(now=now,rows=raw,initial_capital=1000,scope=SCOPE)
        self.reset();after=ledger_daily_drawdown(now=now,rows=raw,initial_capital=1000,scope=SCOPE)
        self.assertEqual(before['net_pnl'],after['net_pnl']);self.assertEqual(after['drawdown'],.03);self.assertTrue(after['blocked'])
    def test_cached_dashboard_old_statistics_are_removed_immediately(self):
        self.reset();data={'timestamp':'2026-10-10 10:59:00','account':{'total_eq':970,'pos_upl_total':0,'cum_net_pnl':-30},'trades':[row()], 'performance':{'all_trades':900}, 'snapshots':[{'time':'2026-10-09 01:00:00','total_eq':1000}], 'review':{'account_scope':SCOPE,'timestamp':'2026-10-09 01:00:00','total_trades':900}, 'risk_status':{'daily_blocked':True}}
        with patch('scripts.evolution_status.public_status',return_value={'sample_size':0}) as view:
            out=stats.project_snapshot(data,[row()],SCOPE,self.root)
        self.assertEqual(out['trades'],[]);self.assertEqual(out['performance']['all_trades'],0)
        self.assertEqual(out['review'],{});self.assertEqual(len(out['snapshots']),1)
        self.assertIsNone(out['account']['cum_net_pnl']);self.assertTrue(out['risk_status']['daily_blocked'])
        view.assert_called_once_with(self.root,scope=SCOPE)
        self.assertEqual(data['performance']['all_trades'],900)
    def test_new_equity_observation_uses_new_starting_capital(self):
        self.reset();data={'timestamp':'2026-10-10 11:01:00 (北京时间)','account':{'total_eq':975,'pos_upl_total':0}}
        with patch('scripts.evolution_status.public_status',return_value={}):out=stats.project_snapshot(data,[],SCOPE,self.root)
        self.assertEqual(out['account']['cum_net_pnl'],5)
    def test_corrupt_epoch_fails_closed_not_as_an_empty_configuration(self):
        self.reset();doc=json.loads(self.path.read_text());doc['statistics_excluded_ids']='bad';self.path.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):stats.filter_rows([row()],data_dir=self.root)
    def test_old_review_samples_and_markdown_do_not_reappear_in_the_new_period(self):
        self.reset()
        from scripts.evolution_status import public_status
        (self.root/'self_improvement_report.json').write_text(json.dumps({'account_scope':SCOPE,'timestamp':'2026-10-09 10:00:00','total_trades':671,'win_rate':40}))
        (self.root/'self_improvement_status.json').write_text(json.dumps({'account_scope':SCOPE,'last_attempt_at':'2026-10-09 10:00:00','status':'success'}))
        (self.root/'self_improvement_review.md').write_text('OLD REVIEW')
        with patch('scripts.memory_registry.public_view',return_value={'managed':False,'status':'published','active_version':3}):
            out=public_status(self.root,scope=SCOPE)
        self.assertEqual(out['sample_size'],0);self.assertEqual(out['review_markdown'],'');self.assertIsNone(out['last_success_at']);self.assertEqual(out['memory_version'],3)

    def test_new_operation_requires_explicit_previous_epoch(self):
        self.reset()
        with self.assertRaises(ValueError):self.reset('reset-fixture-2')
        self.assertEqual(self.reset('reset-fixture-2',expected_epoch_id='reset-fixture-1')['status'],'reset')

if __name__=='__main__':unittest.main()
