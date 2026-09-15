"""Offline regression of the real partial-fill/3x/close evidence failures."""
import copy,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from scripts import execution_leverage as leverage,close_execution,initial_protection
from scripts import strategy_evidence as evidence,close_evidence,close_attribution,trade_quality,horizon_stats
from scripts.risk_policy import Policy,RiskRejected
from scripts.okx_runtime import OKXEnvironment
from test_close_evidence import history,order

INST='TEST-USDT-SWAP'
def pos(size=1390):
    return {'instId':INST,'posSide':'long','pos':str(size),'posId':'position-1','cTime':'100000',
            'avgPx':'0.7097','markPx':'0.7096','upl':'-0.1','lever':'3'}
def oco(size=2536):
    return {'algoId':'a','instId':INST,'ordType':'oco','state':'live','posSide':'long','side':'sell',
            'reduceOnly':'true','sz':str(size),'tpTriggerPx':'0.72','slTriggerPx':'0.708','actualSz':'0'}

class LeverageTests(unittest.TestCase):
    def setUp(self):
        self.env=OKXEnvironment('demo','fake','fake','fake')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(evidence,'DB_PATH',Path(self.temp.name)/'e.db');p.start();self.addCleanup(p.stop)
    def test_mode_ceiling_is_independent_but_live_policy_unchanged(self):
        self.assertEqual(leverage.mode_policy(Policy(),'scalp',self.env).max_leverage,20)
        self.assertEqual(leverage.mode_policy(Policy(),'swing',self.env).max_leverage,5)
        self.assertEqual(leverage.mode_policy(Policy(),'scalp',SimpleNamespace(mode='live')).max_leverage,5)
        self.assertEqual(leverage.mode_policy(Policy(scalp_max_leverage=12),'scalp',self.env).max_leverage,12)
    def test_targets_follow_horizon_without_releveraging_existing_positions(self):
        policy=leverage.mode_policy(Policy(),'scalp',self.env)
        self.assertEqual(leverage.choose(policy,'scalp',self.env,3,[],{}),10)
        self.assertEqual(leverage.choose(policy,'scalp',self.env,3,[],{'leverage':20}),20)
        self.assertEqual(leverage.choose(policy,'scalp',self.env,3,[pos()],{'leverage':20}),3)
        with self.assertRaises(RiskRejected):leverage.choose(policy,'scalp',self.env,3,[],{'leverage':True})
    def test_set_once_then_readback_before_authorization(self):
        def request(method,path,params,env):
            if path.endswith('/positions') or path.endswith('/orders-pending'):return []
            if method=='POST':return [{'lever':'10'}]
            return [{'posSide':'long','lever':'10'}]
        call=Mock(side_effect=request)
        self.assertEqual(leverage.apply(self.env,INST,'long',10,3,'decision',call),10)
        writes=[c for c in call.call_args_list if c.args[0]=='POST']
        self.assertEqual(len(writes),1);self.assertEqual(writes[0].args[2],{'instId':INST,'mgnMode':'cross','lever':'10'})
        self.assertEqual(evidence.export_events(self.env.identity,'leverage_change_result')[0]['payload']['verified'],True)
    def test_timeout_can_be_read_reconciled_but_never_retried(self):
        calls=[]
        def request(method,path,params,env):
            calls.append((method,path))
            if method=='POST':raise TimeoutError()
            return [{'posSide':'long','lever':'10'}] if path.endswith('/leverage-info') else []
        self.assertEqual(leverage.apply(self.env,INST,'long',10,3,'decision',request),10)
        self.assertEqual(sum(m=='POST' for m,p in calls),1)
    def test_failed_readback_blocks_entry_and_existing_exposure_blocks_change(self):
        for actual in ([],[{'posSide':'long','lever':'3'}]):
            request=Mock(side_effect=[[],[],[{'lever':'10'}],actual])
            with self.assertRaises(RiskRejected):leverage.apply(self.env,INST,'long',10,3,'d',request)
        request=Mock(side_effect=[[pos()],[]])
        with self.assertRaises(RiskRejected):leverage.apply(self.env,INST,'long',10,3,'d',request)
        self.assertFalse(any(c.args[0]=='POST' for c in request.call_args_list))

class PartialFillTests(unittest.TestCase):
    def setUp(self):
        self.env=SimpleNamespace(identity='demo',configured=True)
        self.owned={'size':2536.,'stop':.708,'client_id':'owned'}
    def test_partial_then_full_fill_waits_for_full_protection_with_fresh_size(self):
        reader=Mock(side_effect=[[],[],[oco()]])
        positions=Mock(side_effect=[(True,[pos()],''),(True,[pos(2536)],''),(True,[pos(2536)],'')])
        with patch.object(initial_protection,'owned_entry',return_value=self.owned),patch.object(evidence,'best_effort'),patch.object(initial_protection.time,'sleep'):
            result=initial_protection.verify(self.env,INST,'long',1390,pos(),positions,read_orders=reader)
        self.assertEqual(result['status'],'verified');self.assertEqual(result['actual_size'],2536)
        self.assertEqual(reader.call_count,3);self.assertEqual(result['current_position']['pos'],'2536')
    def test_grace_is_bounded_and_does_not_hide_real_stop_breach(self):
        reader=Mock(return_value=[]);positions=Mock(return_value=(True,[{**pos(),'markPx':'.707'}],''))
        with patch.object(initial_protection,'owned_entry',return_value=self.owned),patch.object(evidence,'best_effort'),patch.object(initial_protection.time,'sleep'):
            result=initial_protection.verify(self.env,INST,'long',1390,pos(),positions,read_orders=reader)
        self.assertEqual(result['status'],'unverified');reader.assert_called_once()
        positions.return_value=(True,[pos()],'')
        with patch.object(initial_protection,'owned_entry',return_value=self.owned),patch.object(evidence,'best_effort'),patch.object(initial_protection.time,'sleep'):
            result=initial_protection.verify(self.env,INST,'long',1390,pos(),positions,read_orders=reader)
        self.assertEqual(result['status'],'unverified');self.assertLessEqual(result['attempts'],10)
    def test_unowned_or_other_lifecycle_has_no_growth_permission(self):
        positions=Mock(return_value=(True,[{**pos(2536),'cTime':'200000'}],''))
        with patch.object(initial_protection,'owned_entry',return_value=self.owned),patch.object(evidence,'best_effort'):
            result=initial_protection.verify(self.env,INST,'long',1390,pos(),positions,read_orders=Mock(return_value=[]))
        self.assertEqual(result['status'],'changed')

class SizedCloseTests(unittest.TestCase):
    def setUp(self):
        self.env=OKXEnvironment('demo','fake','fake','fake')
        self.events=[]
        p=patch.object(close_evidence,'record_close',side_effect=lambda env,**kw:self.events.append(kw));p.start();self.addCleanup(p.stop)
        p=patch.object(evidence,'append');p.start();self.addCleanup(p.stop)
        p=patch.object(close_execution.time,'sleep');p.start();self.addCleanup(p.stop)
    def test_refresh_after_cancel_uses_full_quantity_and_records_exact_id(self):
        pending={'instId':INST,'posSide':'long','ordId':'entry'}
        request=Mock(side_effect=[[pending],[{'sCode':'0'}],[],[pos(2536)],[{'ordId':'123','sCode':'0'}],[]])
        closed,_=close_execution.close(self.env,INST,'long',1390,pos(),'oco_unverified',request=request)
        self.assertTrue(closed)
        writes=[c for c in request.call_args_list if c.args[0]=='POST' and c.args[1].endswith('/order')]
        self.assertEqual(len(writes),1);payload=writes[0].args[2]
        self.assertTrue(payload['reduceOnly']);self.assertEqual(payload['sz'],'2536');self.assertLessEqual(len(payload['clOrdId']),32)
        self.assertEqual(self.events[-1]['size'],2536);self.assertEqual(self.events[-1]['observed_size'],1390)
        self.assertEqual(self.events[-1]['result'],[{'ordId':'123'}]);self.assertEqual(self.events[-1]['status'],'confirmed')
    def test_changed_lifecycle_is_not_closed(self):
        request=Mock(side_effect=[[],[],[{**pos(),'cTime':'200000'}]])
        closed,_=close_execution.close(self.env,INST,'long',1390,pos(),'oco_unverified',request=request)
        self.assertFalse(closed);self.assertFalse(any(c.args[0]=='POST' for c in request.call_args_list))
    def test_unknown_market_ack_is_looked_up_by_client_id_without_duplicate_write(self):
        sent=[];reads=[0]
        def request(method,path,params,env,**kw):
            if path.endswith('/orders-pending'):return []
            if path.endswith('/positions'):
                reads[0]+=1;return [pos()] if reads[0]==1 else []
            if method=='POST':sent.append(params);raise TimeoutError()
            return [{'ordId':'456','clOrdId':sent[0]['clOrdId']}]
        closed,_=close_execution.close(self.env,INST,'long',1390,pos(),'oco_unverified',request=request)
        self.assertTrue(closed);self.assertEqual(len(sent),1);self.assertEqual(self.events[-1]['result'],[{'ordId':'456'}])
    def test_pending_cancel_not_terminal_does_not_close_old_quantity(self):
        row={'instId':INST,'posSide':'long','ordId':'entry'}
        request=Mock(side_effect=[[row],[],[row]])
        closed,_=close_execution.close(self.env,INST,'long',1390,pos(),'oco_unverified',request=request)
        self.assertFalse(closed);self.assertFalse(any(c.args[1].endswith('/order') for c in request.call_args_list))

class AttributionAndQualityTests(unittest.TestCase):
    def test_legacy_full_close_quantity_growth_is_only_corroborated_with_lifecycle(self):
        h={**history(),'openMaxPos':'5'}
        e={'scope':'demo','instId':INST,'posSide':'long','position_id':'p','position_created_at':'100000',
           'size':3,'transport':'existing_cli_close','status':'confirmed','started_at':199,'confirmed_at':201,'reason_code':'oco_unverified'}
        r=close_attribution.reason(h,[order()],executions=[e],scope='demo')
        self.assertEqual(r['attribution_status'],'corroborated');self.assertIn('保护核验失败',r['exit_reason'])
        for change in ({'position_created_at':'90000'},{'transport':'rest_reduce_only_market'},{'scope':'other'},{'status':'flat_observed'}):
            r=close_attribution.reason(h,[order()],executions=[{**e,**change}],scope='demo')
            self.assertEqual(r['attribution_status'],'unknown')
    def test_complete_opening_chain_does_not_mean_complete_trade(self):
        row={'status':'closed','strategy_version':'local','setup':'pullback','decision_id':'d','candidate_id':'c',
             'opening_order_ids':['o'],'opening_trade_ids':['f'],'opening_features':{'price':100},'news_snapshot':{'status':'unavailable'},
             'horizon':'scalp','attribution_status':'unknown','exit_source':'unknown','net_pnl':-2,'fee_reconciliation':{'status':'verified'}}
        trade_quality.annotate(row,history(),{},[])
        self.assertEqual(row['evidence_status'],'partial')
        for k in ('version_not_reproducible','exit_attribution','exit_market_snapshot'):self.assertIn(k,row['evidence_gaps'])
        self.assertIsNone(row['holding_observations']['sampled_max_unrealized_pnl'])
    def test_pending_not_loss_and_zero_not_loss_and_execution_costs_separate(self):
        rows=[{'status':'closed_pending','horizon':'scalp','net_pnl':None},
              {'status':'closed','horizon':'scalp','net_pnl':0},
              {'status':'closed','horizon':'scalp','net_pnl':-2,'gross_pnl':-.5,'fee':-1.5,'attribution_status':'verified',
               'close_order_sources':[{'reason_code':'oco_unverified'}],'strategy_version':'v1'},
              {'status':'closed','horizon':'scalp','net_pnl':-.5,'gross_pnl':1,'fee':-1.5,'attribution_status':'verified','strategy_version':'v2'}]
        stats=horizon_stats.rebuild(rows)
        self.assertEqual(stats['pending_settlements'],1);self.assertEqual(stats['scalp']['closed'],3)
        self.assertEqual(stats['scalp']['losses'],2);self.assertEqual(stats['scalp']['breakeven'],1)
        self.assertEqual(stats['by_loss_class']['execution_safety_exit']['net_pnl'],-2)
        self.assertEqual(stats['by_loss_class']['cost_loss']['net_pnl'],-.5)
        self.assertEqual(len(stats['by_version']),3)
