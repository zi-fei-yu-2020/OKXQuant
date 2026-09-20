"""A04 -> A03 persistence handoff: never erase unknown state or consume early."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scripts import position_lifecycle as lifecycle, strategy_evidence as evidence
from scripts import entry_gateway, trade_lock
import ai_factor_trader as trader


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for module,name,value in ((trader,'DATA_DIR',str(self.root)),
                                  (trader,'POSITION_TRACKER_FILE',str(self.root/'trackers.json')),
                                  (trader,'HORIZON_INTENTS_FILE',str(self.root/'horizon.json')),
                                  (evidence,'DB_PATH',self.root/'e.db'),
                                  (trade_lock,'PATH',self.root/'writer.lock')):
            self.stack.enter_context(patch.object(module,name,value))
        self.env=SimpleNamespace(identity='a03-lifecycle')
        self.stack.enter_context(patch.object(trader.market,'_selected',return_value=self.env))
        self.pos={'instId':'BTC-USDT-SWAP','posSide':'long','side':'long','pos':'1','posId':'p','cTime':'1'}
        self.key='BTC-USDT-SWAP_long'
        self.item={'horizon':'scalp','decision_id':'d','mode':{'engine':'demo_scalp_v2'},'entry_context':{'id':'frozen'}}

    def test_corrupt_tracker_file_is_unknown_and_never_overwritten(self):
        for raw in ('{broken', '[]', '{"BTC-USDT-SWAP_long": null}', '{"x":{"peak":NaN}}'):
            with self.subTest(raw=raw):
                Path(trader.POSITION_TRACKER_FILE).write_text(raw)
                snapshot=trader.load_trackers()
                self.assertFalse(snapshot.readable)
                self.assertEqual(lifecycle.reconcile(snapshot,self.key,self.pos,self.env.identity),'unknown')
                self.assertFalse(trader.save_trackers(snapshot))
                self.assertFalse(trader.save_trackers({}))
                self.assertEqual(Path(trader.POSITION_TRACKER_FILE).read_text(),raw)

    def test_missing_file_is_new_but_valid_tracker_roundtrips(self):
        snapshot=trader.load_trackers()
        self.assertTrue(snapshot.readable)
        snapshot[self.key]={'positionIdentity':lifecycle.identity(self.pos,self.env.identity),'pending_write':{'id':'x'}}
        self.assertTrue(trader.save_trackers(snapshot))
        restored=trader.load_trackers()
        self.assertEqual(restored,snapshot)
        self.assertEqual(lifecycle.reconcile(restored,self.key,self.pos,self.env.identity),'same')

    def test_prune_archives_pending_state_and_preserves_on_archive_failure(self):
        old={'pending_write':{'id':'unknown-amend'},'entry_context':{'id':'frozen'},'peak':120}
        trackers={self.key:old}
        with patch.object(evidence,'best_effort',return_value=None):
            self.assertEqual(trader.prune_trackers(trackers,{}),0)
        self.assertEqual(trackers[self.key],old)
        self.assertEqual(trader.prune_trackers(trackers,{}),1)
        event=evidence.export_events(self.env.identity,'tracker_lifecycle_reset')[-1]['payload']
        self.assertEqual(event['previous'],old)

    def test_horizon_is_consumed_only_after_full_tracker_is_durable(self):
        trader._save_horizon_intents({self.key:self.item})
        trackers={self.key:{'instId':self.pos['instId'],'side':'long','positionIdentity':lifecycle.identity(self.pos,self.env.identity)}}
        real_consume=trader.consume_horizon_intent
        def consume(*args,**kw):
            persisted=json.loads(Path(trader.POSITION_TRACKER_FILE).read_text())
            self.assertEqual(persisted[self.key]['entryIntent'],self.item)
            return real_consume(*args,**kw)
        with patch.object(trader,'consume_horizon_intent',side_effect=consume):
            self.assertTrue(trader._persist_horizon_adoption(trackers,self.key,self.item))
        self.assertNotIn(self.key,trader.load_horizon_intents())

    def test_tracker_save_failure_leaves_horizon_for_restart(self):
        trader._save_horizon_intents({self.key:self.item})
        trackers={self.key:{'instId':self.pos['instId'],'side':'long'}}
        with patch.object(trader,'save_trackers',side_effect=OSError('disk failure')):
            self.assertFalse(trader._persist_horizon_adoption(trackers,self.key,self.item))
        self.assertEqual(trader.load_horizon_intents()[self.key],self.item)

    def test_real_manager_persists_mode_and_source_before_later_evaluation(self):
        trader._save_horizon_intents({self.key:self.item})
        position={**self.pos,'avgPx':'100','markPx':'101'}
        factors={'instId':self.pos['instId'],'name':'BTC','market_data_valid':True,
                 'price':101,'atr':1,'atr_15m':1,'ctVal':1,'precision':2,'type':'crypto'}
        trackers={}
        orders=[{'slTriggerPx':'90','tpTriggerPx':'120'}]
        with patch('scripts.initial_protection.verify',return_value={'status':'verified','orders':orders}), \
             patch.object(trader,'evaluate_asset_signal',return_value=(0,'HOLD',[],'none','')), \
             patch('scripts.minute_exit.enrich_volatility'), \
             patch.object(trader,'_exit_preset',return_value={}), \
             patch.object(trader.exit_policy,'volatility',side_effect=RuntimeError('after-adoption')):
            with self.assertRaisesRegex(RuntimeError,'after-adoption'):
                trader.manage_position_tp_and_trailing(factors,position,trackers,'fixture',[])
        stored=trader.load_trackers()[self.key]
        self.assertEqual(stored['horizon'],'scalp')
        self.assertEqual(stored['mode'],self.item['mode'])
        self.assertEqual(stored['entryIntent'],self.item)
        self.assertNotIn(self.key,trader.load_horizon_intents())

    def test_corrupt_trackers_still_verify_cloud_protection_without_local_amendment(self):
        Path(trader.POSITION_TRACKER_FILE).write_text('{damaged')
        position={**self.pos,'avgPx':'100','markPx':'101'}
        factors={'instId':self.pos['instId'],'name':'BTC','market_data_valid':True,
                 'price':101,'atr':1,'atr_15m':1,'ctVal':1,'precision':2,'type':'crypto'}
        with patch('scripts.initial_protection.verify',return_value={'status':'verified','orders':[]}) as verify, \
             patch.object(trader,'close_position_confirmed') as close, \
             patch.object(trader,'run_cmd_result') as write:
            trader.manage_position_tp_and_trailing(factors,position,trader.load_trackers(),'fixture',[])
        verify.assert_called_once();close.assert_not_called();write.assert_not_called()
        self.assertEqual(Path(trader.POSITION_TRACKER_FILE).read_text(),'{damaged')

    def test_consumption_does_not_remove_a_replaced_intent(self):
        newer={**self.item,'decision_id':'new-decision'}
        trader._save_horizon_intents({self.key:newer})
        trader.consume_horizon_intent(self.pos['instId'],'long',expected=self.item)
        self.assertEqual(trader.load_horizon_intents()[self.key],newer)


class PendingSnapshotTests(unittest.TestCase):
    def test_shared_unresolved_keeps_pending_and_fresh_snapshot_avoids_extra_get(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(evidence,'DB_PATH',Path(tmp)/'e.db'):
            env=SimpleNamespace(identity='a03-pending')
            cid=evidence.begin_intent(env.identity,'d','BTC-USDT-SWAP',{'instId':'BTC-USDT-SWAP'})
            order={'clOrdId':cid,'instId':'BTC-USDT-SWAP','ordId':'123','state':'live','uTime':'1'}
            evidence.finish_intent(cid,'pending',order)
            self.assertEqual(evidence.unresolved(env.identity)[0][0],cid)
            with patch.object(entry_gateway,'_request',side_effect=TimeoutError()) as read:
                entry_gateway.reconcile_intents(env,pending_orders=[order])
            read.assert_not_called()
            # Once absent from the current pending snapshot, an exact terminal lookup resumes.
            with patch.object(entry_gateway,'_request',return_value=[{**order,'state':'filled','uTime':'2'}]) as read:
                entry_gateway.reconcile_intents(env,pending_orders=[])
            read.assert_called_once()
            self.assertEqual(evidence.unresolved(env.identity),[])
