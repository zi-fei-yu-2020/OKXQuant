import copy
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from scripts import close_attribution as attribution, close_evidence as receipts, strategy_evidence, ledger_monitor, algo_reader
from scripts.okx_runtime import OKXEnvironment
from scripts.decision_reporting import summarize, format_summary, format_actions
from scripts import decision_reporting as reporting


def history():return {'instId':'TEST-USDT-SWAP','direction':'long','cTime':'100000','uTime':'200000','closeTotalPos':'5','posId':'p'}
def order(**changes):return {**{'instId':'TEST-USDT-SWAP','posSide':'long','side':'sell','state':'filled','ordId':'1','fillTime':'200000','uTime':'200000','accFillSz':'5','clOrdId':'','source':'','algoId':''},**changes}


class CloseAttributionTests(unittest.TestCase):
    def test_whole_lifecycle_includes_earlier_partial_exit(self):
        h={**history(),'closeTotalPos':'17'}
        rows=[order(ordId='early',fillTime='140000',uTime='140000',accFillSz='12',algoId='a'),order(ordId='late',accFillSz='5')]
        algo={'algoId':'a','instId':h['instId'],'posSide':'long','side':'sell','state':'effective','ordIdList':['early'],'actualSide':'sl'}
        event={'scope':'demo','instId':h['instId'],'posSide':'long','started_at':190,'confirmed_at':201,'status':'confirmed','reason_code':'hard_stop','size':5}
        result=attribution.reason(h,rows,algos=[algo],executions=[event],scope='demo')
        self.assertEqual(result['exit_source'],'mixed');self.assertEqual(result['attribution_status'],'corroborated')
        self.assertEqual(result['close_order_ids'],['early','late'])
        self.assertIn('云端止损',result['exit_reason']);self.assertIn('策略硬止损',result['exit_reason'])

    def test_algo_trigger_side_not_pnl_determines_tp_sl(self):
        for side,label in [('sl','云端止损触发'),('tp','云端止盈触发')]:
            a={'algoId':'a','instId':history()['instId'],'side':'sell','posSide':'long','state':'effective','ordIdList':['1'],'actualSide':side}
            result=attribution.reason({**history(),'pnl':'100'},[order()],algos=[a])
            self.assertEqual(result['exit_reason'],label);self.assertEqual(result['attribution_status'],'verified')

    def test_source_seven_is_algo_without_parent_id(self):
        self.assertEqual(attribution.reason(history(),[order(source='7')])['exit_source'],'exchange_algo')

    def test_unknown_is_not_mislabeled_external_manual(self):
        result=attribution.reason(history(),[order()])
        self.assertEqual(result['exit_source'],'unknown');self.assertNotIn('外部平仓',result['exit_reason'])

    def test_receipt_before_lifecycle_does_not_pollute_quantity(self):
        result=attribution.reason(history(),[order(),order(ordId='old',fillTime='99999')])
        self.assertEqual(result['close_order_ids'],['1'])

    def test_partial_canceled_close_uses_filled_not_requested_quantity(self):
        h={**history(),'closeTotalPos':'5'}
        rows=[order(ordId='part',state='canceled',fillTime='140000',accFillSz='2',sz='10'),order(accFillSz='3')]
        self.assertEqual(len(attribution.reason(h,rows)['close_order_ids']),2)
        self.assertEqual(attribution.reason(h,[order(state='canceled',accFillSz='0',sz='5')])['close_order_ids'],[])

    def test_strategy_journal_requires_scope_and_unique_order(self):
        event={'scope':'demo','instId':history()['instId'],'posSide':'long','started_at':190,'confirmed_at':201,'status':'confirmed','reason_code':'time_exit','size':5}
        r=attribution.reason(history(),[order()],executions=[event],scope='demo')
        self.assertEqual(r['attribution_status'],'corroborated');self.assertIn('策略时间止损',r['exit_reason'])
        self.assertEqual(attribution.reason(history(),[order()],executions=[event],scope='live')['exit_source'],'unknown')
        event['status']='accepted'
        self.assertEqual(attribution.reason(history(),[order()],executions=[event],scope='demo')['exit_source'],'unknown')
        event['status']='failed'
        self.assertEqual(attribution.reason(history(),[order()],executions=[event],scope='demo')['exit_source'],'unknown')

    def test_direct_close_receipt_id_can_confirm_strategy_source(self):
        event={'scope':'demo','instId':history()['instId'],'started_at':190,'confirmed_at':201,'status':'accepted','reason_code':'ai_exit','response_order_ids':['1']}
        r=attribution.reason(history(),[order()],executions=[event],scope='demo')
        self.assertEqual(r['attribution_status'],'verified');self.assertEqual(r['exit_reason'],'AI主动退出')

    def test_competing_orders_prevent_log_only_attribution(self):
        event={'scope':'demo','instId':history()['instId'],'started_at':190,'confirmed_at':201,'status':'confirmed','reason_code':'time_exit'}
        r=attribution.reason(history(),[order(accFillSz='2'),order(ordId='2',accFillSz='3')],executions=[event],scope='demo')
        self.assertEqual(r['exit_source'],'unknown')

    def test_algo_wrong_instrument_cannot_relabel_order(self):
        a={'algoId':'a','instId':'OTHER','side':'sell','posSide':'long','state':'effective','ordIdList':['1'],'actualSide':'tp'}
        self.assertEqual(attribution.reason(history(),[order()],algos=[a])['exit_source'],'unknown')


class EvidenceStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        for module,name,value in ((strategy_evidence,'DB_PATH',root/'e.db'),(ledger_monitor,'DATA',root),(receipts,'GATEWAY_DB',root/'missing.db')):
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        self.env=OKXEnvironment('demo','fake','fake','fake')

    def test_archived_terminal_order_survives_recent_page_aging(self):
        receipts.archive(self.env.identity,'close_order_receipt',[order(clOrdId='okxquantclose1234567890')],receipts.ORDER_FIELDS)
        data=receipts.load_inputs(self.env,[])
        r=attribution.reason(history(),data['orders'])
        self.assertEqual(r['exit_source'],'manual_admin')

    def test_history_failure_is_bounded_and_does_not_clear_receipts(self):
        with patch.object(algo_reader,'read_algo_history',side_effect=RuntimeError('429')) as get:
            first=receipts.load_inputs(self.env,[order(source='7')]);second=receipts.load_inputs(self.env,[order(source='7')])
        self.assertEqual(get.call_count,1)
        self.assertTrue(first['orders']);self.assertTrue(second['orders'])

    def test_close_journal_is_local_and_scoped(self):
        receipts.record_close(self.env,inst_id='TEST-USDT-SWAP',side='long',size=5,started_at=190,confirmed_at=201,reason='time_exit',position={'posId':'p'},result=[{'ordId':'123','api_key':'NEVER_STORE'}])
        events=receipts.local_close_events(self.env.identity)
        self.assertEqual(events[0]['scope'],self.env.identity)
        self.assertEqual(events[0]['response_order_ids'],['123']);self.assertNotIn('NEVER_STORE',json.dumps(events))
        self.assertEqual(receipts.local_close_events('other'),[])

    def test_legacy_log_requires_account_marker_and_confirmed_close_phrase(self):
        row={'started_at':'2026-09-06 16:30:00','finished_at':'2026-09-06 16:30:30',
             'detail':'[Trader] OKX environment frozen for cycle: DEMO / demo-key\n巡检完成 | 动作: [BTC] 超过8小时，时间止损平仓释放保证金'}
        self.assertEqual(len(receipts.legacy_job_events([row],'demo-key')),1)
        self.assertEqual(receipts.legacy_job_events([row],'other-key'),[])
        row['detail']=row['detail'].replace('时间止损平仓释放保证金','时间止损平仓失败')
        self.assertEqual(receipts.legacy_job_events([row],'demo-key'),[])


class HistoryAdmissionTests(unittest.TestCase):
    def test_history_is_fixed_get_with_monitor_admission(self):
        env=OKXEnvironment('demo','fake','fake','fake')
        with patch.object(algo_reader,'_turn',return_value=nullcontext()) as turn,patch.object(algo_reader,'_reserve') as reserve,patch.object(algo_reader,'_check'),patch('okxquant_backend.okx_trade_service._request',return_value=[]) as get:
            algo_reader.read_algo_history(env,ord_type='oco')
        self.assertEqual(turn.call_args.args[1],'monitor');self.assertEqual(reserve.call_args.args[1],'monitor')
        self.assertEqual(get.call_args.args[:2],('GET','/api/v5/trade/orders-algo-history'))
        self.assertLessEqual(get.call_args.kwargs['timeout'],2)

    def test_history_429_has_no_retry_or_risk_cooldown_poisoning(self):
        env=OKXEnvironment('demo','fake','fake','fake')
        with patch.object(algo_reader,'_turn',return_value=nullcontext()),patch.object(algo_reader,'_reserve'),patch.object(algo_reader,'_check'),patch.object(algo_reader,'_cooldown') as cooldown,patch('okxquant_backend.okx_trade_service._request',side_effect=urllib.error.HTTPError('x',429,'rate',{},None)) as get:
            with self.assertRaises(algo_reader.AlgoReadError):algo_reader.read_algo_history(env,ord_type='oco')
        self.assertEqual(get.call_count,1);cooldown.assert_not_called()


class CloseTransportTests(unittest.TestCase):
    def test_journal_does_not_change_close_command_or_add_write_retry(self):
        import ai_factor_trader as trader
        with patch.object(trader,'run_cmd_result',side_effect=[{'ok':True,'data':[]},{'ok':True,'data':[]}]) as command,patch.object(trader,'okx_private_command',side_effect=lambda s:s),patch.object(trader,'query_positions',return_value=(True,[],'')),patch.object(trader.time,'sleep'),patch.object(receipts,'record_close',side_effect=OSError('disk')):
            ok,_=trader.close_position_confirmed('TEST-USDT-SWAP','long',5,exit_reason='time_exit')
        self.assertTrue(ok);self.assertEqual(command.call_count,2)
        self.assertEqual(command.call_args_list[1].args[0],'okx swap close --instId TEST-USDT-SWAP --mgnMode cross --posSide long --autoCxl --json')

    def test_failed_close_never_gets_confirmed_journal(self):
        import ai_factor_trader as trader
        with patch.object(trader,'run_cmd_result',side_effect=[{'ok':True,'data':[]},{'ok':False,'stderr':'failed','stdout':''}]),patch.object(trader,'okx_private_command',side_effect=lambda s:s),patch.object(trader,'query_positions',return_value=(False,[],'unavailable')),patch.object(trader.time,'sleep'),patch.object(receipts,'record_close') as journal:
            ok,_=trader.close_position_confirmed('TEST-USDT-SWAP','long',5)
        self.assertFalse(ok)
        self.assertEqual(journal.call_args_list[0].kwargs['status'],'submitted')
        self.assertNotIn('confirmed',[call.kwargs['status'] for call in journal.call_args_list])


class CompactReportingTests(unittest.TestCase):
    def test_five_asset_wait_reasons_are_kept_out_of_summary(self):
        cache={f'{s}-USDT-SWAP':{'name':s,'decision':{'action':'WAIT','contract_valid':True,'decision_status':'audited_wait','summary_reason':'VERBOSE'*100,'wait_audit':{'long':{'reason':'LONG'*50},'short':{'reason':'SHORT'*50}}}} for s in ('BTC','ETH','SOL','DOGE','SUI')}
        summary=summarize(cache,['WLD：模拟盘不支持，仅观察']);summary.update(wait_alert=True,no_entry_candidate_streak=9)
        text=format_summary(summary)
        self.assertLess(len(text),100);self.assertNotIn('VERBOSE',text);self.assertIn('WAIT5',text)
        self.assertIn('连续9轮',text);self.assertTrue(summary['items'][0]['long_blocker'])

    def test_action_summary_keeps_failure_and_omits_long_rationale(self):
        text=format_actions(['[BTC] 云端止损收紧至 80000: VERY_LONG_REASON'*5,'[ETH] AI平仓请求未获交易所确认: HTTP 429'])
        self.assertNotIn('VERY_LONG_REASON',text);self.assertIn('未获交易所确认',text);self.assertIn('429',text)
        self.assertEqual(format_actions([]),'无开平仓')


class FinalEvidenceRegressionTests(unittest.TestCase):
    def test_confirmed_journal_retains_closed_position_identity(self):
        from scripts import ai_factor_trader as trader
        original={'instId':'TEST-USDT-SWAP','posSide':'long','posId':'original','pos':'5'}
        other={'instId':'OTHER-USDT-SWAP','posSide':'long','posId':'other','pos':'10'}
        with patch.object(trader,'run_cmd_result',return_value={'ok':True,'data':[]}),patch.object(trader,'okx_private_command',side_effect=lambda s:s),patch.object(trader,'query_positions',return_value=(True,[other],'')),patch.object(trader.time,'sleep'),patch.object(receipts,'record_close') as journal:
            closed,_=trader.close_position_confirmed('TEST-USDT-SWAP','long',5,exit_reason='time_exit',position=original)
        self.assertTrue(closed)
        self.assertEqual(journal.call_count,3)
        self.assertEqual(journal.call_args.kwargs['status'],'confirmed')
        self.assertEqual(journal.call_args.kwargs['position'],original)

    def test_execution_history_is_scoped_read_only_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(strategy_evidence,'DB_PATH',Path(directory)/'e.db'):
            self.assertEqual(reporting.execution_history('demo'),[])
            self.assertFalse(strategy_evidence.DB_PATH.exists())
            strategy_evidence.append('live','execution_cycle',{'timestamp':'other','actions':['private']})
            strategy_evidence.append('demo','execution_cycle',{'timestamp':'one','actions':['full reason']})
            strategy_evidence.append('demo','execution_cycle',{'timestamp':'two','actions':[]})
            rows=reporting.execution_history('demo',1)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['timestamp'],'two')
            self.assertEqual(len(reporting.execution_history('demo')),2)

    def test_direct_order_id_does_not_depend_on_event_clock_window(self):
        event={'scope':'demo','instId':history()['instId'],'started_at':1,'confirmed_at':2,'status':'accepted','reason_code':'ai_exit','response_order_ids':['1']}
        self.assertEqual(attribution.reason(history(),[order()],executions=[event],scope='demo')['attribution_status'],'verified')
        event['response_order_ids']='123'
        self.assertEqual(attribution.reason(history(),[order()],executions=[event],scope='demo')['exit_source'],'unknown')

    def test_dictionary_close_receipt_keeps_only_order_id(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(strategy_evidence,'DB_PATH',Path(directory)/'e.db'):
            env=OKXEnvironment('demo','fake','fake','fake')
            receipts.record_close(env,inst_id='TEST-USDT-SWAP',side='long',size=5,started_at=190,confirmed_at=201,reason='ai_exit',status='accepted',result={'ordId':'123','api_key':'NEVER_STORE'})
            rows=receipts.read_events(env.identity,'close_execution')
            self.assertEqual(rows[0]['response_order_ids'],['123'])
            self.assertNotIn('NEVER_STORE',str(rows))
