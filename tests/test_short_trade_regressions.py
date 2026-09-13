import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scripts import initial_protection, algo_reader, close_evidence, close_attribution
from scripts.ledger_duration import duration_seconds, format_duration
import ai_factor_trader as trader

INST='TEST-USDT-SWAP'

def order():
    return {'algoId':'a','instId':INST,'ordType':'oco','state':'live','posSide':'long','side':'sell',
            'reduceOnly':'true','sz':'5','tpTriggerPx':'120','slTriggerPx':'90','actualSz':'0'}

def position():
    return {'instId':INST,'posSide':'long','pos':'5','posId':'position-1','cTime':'1000','markPx':'100','avgPx':'100'}

class DurationTests(unittest.TestCase):
    def test_receipt_milliseconds_do_not_become_zero_minutes(self):
        self.assertEqual(format_duration(duration_seconds(1788948941.590,1788948966.015)),'24.4秒')
        self.assertEqual(format_duration(duration_seconds(1788930978.346,1788931023.695)),'45.3秒')
        for value,expected in [(0,'0秒'),(.1,'不足1秒'),(59.99,'59.9秒'),(60,'1分钟'),(61,'1分1秒'),(3721,'1时2分1秒')]:
            self.assertEqual(format_duration(value),expected)
    def test_unknown_and_reversed_times_do_not_invent_duration(self):
        for start,end in [(None,2),(0,2),(3,2),(True,3),(float('nan'),5)]:
            self.assertIsNone(duration_seconds(start,end))
        for value in [None,-1,True,float('nan'),float('inf')]:self.assertEqual(format_duration(value),'--')

class InitialProtectionTests(unittest.TestCase):
    def setUp(self):
        self.env=SimpleNamespace(identity='demo-unit')
        self.positions=Mock(return_value=(True,[position()],''))
        p=patch.object(initial_protection.strategy_evidence,'best_effort');self.audit=p.start();self.addCleanup(p.stop)
        p=patch.object(initial_protection.time,'sleep');p.start();self.addCleanup(p.stop)
    def verify(self,reader):
        return initial_protection.verify(self.env,INST,'long',5,position(),self.positions,read_orders=reader)
    def test_complete_empty_snapshot_gets_one_recheck_without_repair(self):
        reader=Mock(side_effect=[[],[order()]])
        result=self.verify(reader)
        self.assertEqual(result['status'],'verified');self.assertEqual(reader.call_count,2)
        self.assertTrue(all(c.kwargs['force'] and c.kwargs['priority']=='risk' for c in reader.call_args_list))
        self.assertTrue(all(0<c.kwargs['timeout']<=6 for c in reader.call_args_list))
        self.assertEqual(result['orders'][0]['slTriggerPx'],'90')
    def test_reader_429_has_no_outer_retry_and_records_error(self):
        reader=Mock(side_effect=algo_reader.AlgoReadError('rate_limited',3,429))
        result=self.verify(reader)
        self.assertEqual(result['status'],'unverified');reader.assert_called_once();self.positions.assert_not_called()
        self.assertIn('429',result['detail']);self.audit.assert_called_once()
    def test_full_valid_oco_is_adopted_not_replaced_or_closed(self):
        reader=Mock(return_value=[order()]);result=self.verify(reader)
        self.assertEqual(result['status'],'verified');reader.assert_called_once()
        self.assertEqual(result['orders'],[order()])
    def test_ambiguity_and_missing_fields_remain_unverified(self):
        for changes in [{'reduceOnly':False},{'state':'effective'},{'sz':'1'},{'slTriggerPx':'101'}]:
            reader=Mock(return_value=[{**order(),**changes}])
            self.assertEqual(self.verify(reader)['status'],'unverified');self.assertEqual(reader.call_count,2)
    def test_external_flat_or_changed_position_never_authorizes_stale_close(self):
        self.positions.return_value=(True,[],'')
        self.assertEqual(self.verify(Mock(return_value=[]))['status'],'flat')
        for change in [{'pos':'4'},{'posId':'new'},{'cTime':'2000'}]:
            self.positions.return_value=(True,[{**position(),**change}],'')
            self.assertEqual(self.verify(Mock(return_value=[]))['status'],'changed')
    def test_unavailable_position_read_is_not_empty_account(self):
        self.positions.return_value=(False,[],'network')
        reader=Mock(return_value=[])
        self.assertEqual(self.verify(reader)['status'],'unverified');reader.assert_called_once()

class CloseOutcomeTests(unittest.TestCase):
    def test_failed_transport_is_reconciled_by_reads_only_and_not_falsely_attributed(self):
        with patch.object(trader,'run_cmd_result',side_effect=[{'ok':True,'data':[]},{'ok':False,'data':None,'stderr':'HTTP 503','stdout':''}]) as writes,patch.object(trader,'okx_private_command',side_effect=lambda c:c),patch.object(trader,'query_positions',return_value=(True,[],'')) as reads,patch.object(trader.time,'sleep'),patch.object(close_evidence,'record_close') as journal:
            closed,detail=trader.close_position_confirmed(INST,'long',5,position=position())
        self.assertTrue(closed);self.assertIn('source requires order evidence',detail)
        self.assertEqual(writes.call_count,2);reads.assert_called_once()
        self.assertLessEqual(reads.call_args.kwargs['timeout'],2)
        self.assertEqual([c.kwargs['status'] for c in journal.call_args_list],['submitted','unconfirmed','flat_observed'])
        self.assertEqual(journal.call_args.kwargs['transport_code'],'503')
        self.assertEqual(len({c.kwargs['attempt_id'] for c in journal.call_args_list}),1)
    def test_failed_transport_with_open_position_is_not_retried(self):
        with patch.object(trader,'run_cmd_result',side_effect=[{'ok':True,'data':[]},{'ok':False,'stderr':'failed','stdout':''}]) as writes,patch.object(trader,'okx_private_command',side_effect=lambda c:c),patch.object(trader,'query_positions',return_value=(True,[position()],'')) as reads,patch.object(trader.time,'sleep'),patch.object(close_evidence,'record_close') as journal:
            closed,_=trader.close_position_confirmed(INST,'long',5)
        self.assertFalse(closed);self.assertEqual(writes.call_count,2);self.assertLessEqual(reads.call_count,6)
        self.assertNotIn('confirmed',[c.kwargs['status'] for c in journal.call_args_list])
    def test_ambiguous_journal_cannot_be_promoted_by_outer_guard_log(self):
        event={'scope':'demo','instId':INST,'started_at':100,'confirmed_at':104,'status':'flat_observed','reason_code':'oco_unverified','response_order_ids':[]}
        guard={'at':105,'actions':[{'instrument':INST,'closed':True}]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(close_evidence,'GATEWAY_DB',Path(tmp)/'absent.db'),patch.object(close_evidence,'read_events',side_effect=lambda scope,kind,limit: [copy.deepcopy(event)] if kind=='close_execution' else [guard] if kind=='position_guard' else []):
            events=close_evidence.local_close_events('demo')
        self.assertEqual(len(events),1);self.assertEqual(events[0]['status'],'flat_observed')
    def test_exact_order_link_can_resolve_uncertain_ack_but_flat_alone_cannot(self):
        from test_close_evidence import history,order as closing_order
        event={'scope':'demo','instId':history()['instId'],'started_at':190,'confirmed_at':201,'status':'flat_observed','reason_code':'oco_unverified','response_order_ids':[]}
        self.assertEqual(close_attribution.reason(history(),[closing_order()],executions=[event],scope='demo')['attribution_status'],'unknown')
        event['response_order_ids']=['1']
        self.assertEqual(close_attribution.reason(history(),[closing_order()],executions=[event],scope='demo')['attribution_status'],'verified')
    def test_new_completed_log_phrases_keep_their_real_exit_reason(self):
        self.assertEqual(close_evidence.action_reason('[TEST] 预设 small300：时间退出，交易所确认平仓'),'time_exit')
        self.assertEqual(close_evidence.action_reason('[TEST] 动能止盈已确认'),'trailing_exit')
        self.assertIsNone(close_evidence.action_reason('[TEST] 安全退出确认=False'))
        self.assertIsNone(close_evidence.action_reason('[TEST] 持仓已归零，未发送平仓指令'))
