import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts import horizon_allocation as allocation
from scripts.risk_policy import RiskRejected


BJ=timezone(timedelta(hours=8))


class HorizonAllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.now=datetime(2026,10,7,12,0,tzinfo=BJ).timestamp()
        self.env=SimpleNamespace(mode='demo',identity='okx:demo:test')
        self.stack=[]
        legacy=patch.dict("os.environ",{"OKXQUANT_ENTRY_QUOTAS_ENABLED":"1"});legacy.start();self.stack.append(legacy)
        for name,path in [('LEDGER_PATH',self.root/'ledger.json'),('TRACKERS_PATH',self.root/'trackers.json'),
                          ('INTENTS_PATH',self.root/'intents.json'),('STATUS_PATH',self.root/'status.json')]:
            item=patch.object(allocation,name,path);item.start();self.stack.append(item)
        patcher=patch.object(allocation,'_durable_intents',return_value=({},None));patcher.start();self.stack.append(patcher)
    def tearDown(self):
        for item in reversed(self.stack):item.stop()
        self.tmp.cleanup()
    def row(self,index,inst='BTC-USDT-SWAP',horizon='scalp',day=7):
        return {'id':f't{index}','environment_id':self.env.identity,'instId':inst,'side':'long','horizon':horizon,
                'status':'closed','open_time':f'2026-10-{day:02d} 09:{index%60:02d}:00'}
    def admit(self,**kwargs):
        data=dict(horizon='scalp',inst_id='ETH-USDT-SWAP',side='long',requested_budget=15,
                  positions=[],pending=[],now=self.now,ledger_rows=[],trackers={},intents={},durable_intents={})
        data.update(kwargs);return allocation.admit(self.env,**data)

    def test_eighteenth_scalp_is_allowed_and_nineteenth_is_rejected(self):
        rows=[self.row(i,inst=f'I{i}-USDT-SWAP') for i in range(17)]
        result=self.admit(ledger_rows=rows)
        self.assertEqual(result['outcome'],'admitted');self.assertEqual(result['adjusted_budget_usdt'],8)
        with self.assertRaisesRegex(RiskRejected,'daily filled/reserved limit'):
            self.admit(ledger_rows=rows+[self.row(18,inst='OTHER-USDT-SWAP')])

    def test_fifth_scalp_for_same_instrument_is_rejected(self):
        rows=[self.row(i,inst='ETH-USDT-SWAP') for i in range(4)]
        with self.assertRaisesRegex(RiskRejected,'instrument daily limit'):
            self.admit(ledger_rows=rows)

    def test_active_scalp_limit_blocks_new_scalp(self):
        positions=[
            {'instId':'BTC-USDT-SWAP','posSide':'long','pos':'1','cTime':str(int(self.now*1000))},
            {'instId':'ETH-USDT-SWAP','posSide':'short','pos':'1','cTime':str(int(self.now*1000))},
        ]
        trackers={'BTC-USDT-SWAP_long':{'horizon':'scalp'},'ETH-USDT-SWAP_short':{'horizon':'scalp'}}
        with self.assertRaisesRegex(RiskRejected,'active/pending limit'):
            self.admit(inst_id='SUI-USDT-SWAP',positions=positions,trackers=trackers)

    def test_unknown_active_position_is_conservative_for_scalp_only(self):
        positions=[
            {'instId':'BTC-USDT-SWAP','posSide':'long','pos':'1','cTime':str(int(self.now*1000))},
            {'instId':'SOL-USDT-SWAP','posSide':'short','pos':'1','cTime':str(int(self.now*1000))},
        ]
        with self.assertRaisesRegex(RiskRejected,'active/pending limit'):
            self.admit(inst_id='SUI-USDT-SWAP',positions=positions)
        result=self.admit(horizon='swing',positions=positions,requested_budget=30)
        self.assertEqual(result['outcome'],'admitted');self.assertEqual(result['adjusted_budget_usdt'],20)

    def test_swing_is_not_blocked_by_scalp_daily_cap(self):
        rows=[self.row(i,inst=f'I{i}-USDT-SWAP') for i in range(20)]
        result=self.admit(horizon='swing',ledger_rows=rows,requested_budget=20)
        self.assertEqual(result['adjusted_budget_usdt'],20)

    def test_live_is_disabled_by_default(self):
        env=SimpleNamespace(mode='live',identity='okx:live:test')
        result=allocation.admit(env,horizon='scalp',inst_id='BTC-USDT-SWAP',side='long',requested_budget=15,
                                positions=[],pending=[],now=self.now,ledger_rows=[self.row(i) for i in range(20)],
                                trackers={},intents={},durable_intents={})
        self.assertFalse(result['enabled']);self.assertEqual(result['adjusted_budget_usdt'],15)

    def test_unreadable_ledger_fails_closed_for_scalp_but_not_swing(self):
        allocation.LEDGER_PATH.write_text('{broken',encoding='utf-8')
        with self.assertRaisesRegex(RiskRejected,'ledger unavailable'):
            allocation.admit(self.env,horizon='scalp',inst_id='BTC-USDT-SWAP',side='long',requested_budget=5,
                             positions=[],pending=[],now=self.now,trackers={},intents={},durable_intents={})
        result=allocation.admit(self.env,horizon='swing',inst_id='BTC-USDT-SWAP',side='long',requested_budget=15,
                                positions=[],pending=[],now=self.now,trackers={},intents={},durable_intents={})
        self.assertEqual(result['outcome'],'admitted')

    def test_pending_reservation_prevents_concurrent_limit_bypass(self):
        rows=[self.row(i,inst=f'I{i}-USDT-SWAP') for i in range(17)]
        pending={'instId':'BTC-USDT-SWAP','posSide':'long','sz':'1','accFillSz':'0','clOrdId':'c1'}
        durable={'c1':{'horizon':'scalp'}}
        with self.assertRaisesRegex(RiskRejected,'daily filled/reserved limit'):
            self.admit(ledger_rows=rows,pending=[pending],durable_intents=durable)

    def test_recent_durable_intent_counts_as_reservation_without_pending_snapshot(self):
        rows=[self.row(i,inst=f'I{i}-USDT-SWAP') for i in range(17)]
        durable={'c1':{'instId':'BTC-USDT-SWAP','side':'long','horizon':'scalp','_intent_at':self.now-10}}
        with self.assertRaisesRegex(RiskRejected,'daily filled/reserved limit'):
            self.admit(ledger_rows=rows,durable_intents=durable)

    def test_small_execution_profile_clamps_active_scalp_slots(self):
        config=allocation.effective_config('demo',2,values={'OKXQUANT_ENTRY_QUOTAS_ENABLED':'1'})
        self.assertEqual(config.total_active_slot_limit,2)
        self.assertEqual(config.scalp_active_position_limit,1)
        self.assertEqual(config.swing_reserved_slots,1)

    def test_beijing_day_boundary(self):
        prior=self.row(1);prior['open_time']='2026-10-06 23:59:59'
        current=self.row(2);current['open_time']='2026-10-07 00:00:01'
        counts=allocation.ledger_counts([prior,current],scope=self.env.identity,now=self.now)
        self.assertEqual(counts['filled']['scalp'],1)

    def test_public_status_does_not_expose_internal_dedupe_sets(self):
        result=allocation.public_status(self.env.identity,'demo',rows=[self.row(1)],now=self.now)
        self.assertNotIn('opening_order_ids',result['ledger']);self.assertEqual(result['ledger']['filled']['scalp'],1)


if __name__=='__main__':unittest.main()
