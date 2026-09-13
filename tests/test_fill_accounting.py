"""Fee reconciliation must prove volume/fees, never invent allocation."""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts import fill_accounting as accounting, strategy_evidence as evidence, ledger_monitor

INST='BTC-USDT-SWAP'
def history(**changes):
    return {**{'instId':INST,'direction':'long','posId':'position1','cTime':'1000','uTime':'3000',
               'closeTotalPos':'2','openMaxPos':'1','fee':'-.03','ccy':'USDT'},**changes}
def fill(identity, side, size, fee, ts, **changes):
    return {**{'instId':INST,'posSide':'long','ordId':'order'+identity,'billId':identity,
               'side':side,'fillSz':str(size),'fee':str(fee),'feeCcy':'USDT','ts':str(ts)},**changes}
def fills():
    return [fill('1','buy',1,'-.01',1000),fill('2','sell',1,'-.005',1500),
            fill('3','buy',1,'-.01',2000),fill('4','sell',1,'-.005',3000)]
def archive(rows):return accounting.FillArchive('available',{INST:rows})

class FeeReconciliationTests(unittest.TestCase):
    def checked(self, rows=None, h=None, **kwargs):
        return accounting.reconcile(h or history(),archive(fills() if rows is None else rows),**kwargs)

    def test_scaled_partial_exits_balance_full_lifecycle_not_open_max(self):
        result=self.checked()
        self.assertEqual(result['fee_allocation'],'verified_from_archived_fills')
        self.assertEqual(result['open_fee'],-.02)
        self.assertEqual(result['close_fee'],-.01)
        self.assertEqual(result['fee_reconciliation']['matched_fills'],4)

    def test_short_and_rebates_keep_fee_sign_and_actual_zero(self):
        rows=[fill('1','sell',2,'.01',1000,posSide='short'),fill('2','buy',2,'-.02',3000,posSide='short')]
        result=self.checked(rows,history(direction='short',fee='-.01'))
        self.assertEqual((result['open_fee'],result['close_fee']),(.01,-.02))
        for row in rows:row['fee']='0'
        result=self.checked(rows,history(direction='short',fee='0'))
        self.assertEqual((result['open_fee'],result['close_fee']),(0,0))

    def test_missing_volume_or_fee_never_allocates(self):
        for rows in (fills()[:-1],fills()[1:],[*fills(),fill('extra','buy',1,0,1200)]):
            self.assertIsNone(self.checked(rows)['open_fee'])
        rows=fills();del rows[0]['fee']
        self.assertIsNone(self.checked(rows)['open_fee'])
        result=self.checked(h=history(fee='-.031'))
        self.assertEqual(result['fee_reconciliation']['reason'],'fee_total_mismatch')

    def test_currency_and_net_mode_not_assumed(self):
        for key,value in (('feeCcy','BTC'),('feeCcy',''),('posSide','net'),('posSide',None)):
            rows=fills();rows[0][key]=value
            self.assertIsNone(self.checked(rows)['open_fee'])
        self.assertIsNone(self.checked(h=history(direction='net'))['open_fee'])

    def test_duplicates_are_deduplicated_but_conflicts_block(self):
        rows=fills();rows.append(dict(rows[0]))
        self.assertEqual(self.checked(rows)['fee_reconciliation']['matched_fills'],4)
        rows[-1]['fee']='-.02'
        self.assertEqual(self.checked(rows)['fee_reconciliation']['reason'],'conflicting_fill_identity')

    def test_overlapping_and_adjacent_lifecycles_are_ambiguous(self):
        for peer in (history(posId='other',cTime='3000',uTime='4000'),history(posId='other',cTime='1500',uTime='2500')):
            self.assertIsNone(self.checked(peers=[history(),peer])['open_fee'])
        self.assertEqual(self.checked(peers=[history()])['fee_reconciliation']['status'],'verified')
        self.assertIsNone(self.checked(active_positions=[{'instId':INST,'posSide':'long','pos':'1','cTime':'2000'}])['open_fee'])

    def test_wrong_instrument_direction_or_outside_window_not_consumed(self):
        rows=fills()+[fill('x','buy',5,'-.1',999),fill('y','buy',5,'-.1',3001),fill('z','buy',5,'-.1',2000,posSide='short')]
        self.assertEqual(self.checked(rows)['fee_reconciliation']['matched_fills'],4)

    def test_nonfinite_and_boolean_fields_are_rejected(self):
        for key in ('fillSz','fee','ts'):
            for value in ('nan','inf','1e1000',True,None):
                rows=fills();rows[0][key]=value
                self.assertIsNone(self.checked(rows)['open_fee'],(key,value))

    def test_inventory_cannot_be_negative(self):
        rows=fills();rows[0]['ts']='1400';rows[1]['ts']='1100'
        self.assertEqual(self.checked(rows)['fee_reconciliation']['reason'],'missing_opening_inventory')

    def test_bill_timestamp_can_follow_matching_engine_fill_time(self):
        rows=fills();rows[0]['fillTime']='999'
        self.assertEqual(self.checked(rows)['fee_reconciliation']['status'],'verified')

    def test_scope_read_is_readonly_bounded_and_does_not_create_missing_db(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'e.db'
            self.assertEqual(accounting.read_archive('scope',path).status,'missing')
            self.assertFalse(path.exists())
            with patch.object(evidence,'DB_PATH',path):
                for r in fills():evidence.append('mine','fill',r)
                evidence.append('other','fill',fill('evil','buy',500,-5,1200))
            self.assertEqual(self.checked()['fee_reconciliation']['status'],'verified')
            scoped=accounting.read_archive('mine',path)
            self.assertEqual(len(scoped.by_instrument[INST]),4)
            self.assertEqual(accounting.reconcile(history(),scoped)['fee_reconciliation']['status'],'verified')
            with patch.object(accounting,'MAX_ARCHIVED_FILLS',2):
                self.assertEqual(accounting.read_archive('mine',path).status,'capacity_exceeded')
            before=path.read_bytes();accounting.read_archive('none',path);self.assertEqual(path.read_bytes(),before)

    def test_tampered_archived_payload_cannot_verify_fees(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'e.db'
            with patch.object(evidence,'DB_PATH',path):
                for row in fills():evidence.append('mine','fill',row)
            with sqlite3.connect(path) as db:db.execute("UPDATE events SET digest='bad'")
            result=accounting.read_archive('mine',path)
            self.assertEqual(result.status,'invalid_digest')
            self.assertIsNone(accounting.reconcile(history(),result)['open_fee'])

    def test_missing_archive_and_malformed_receipt_preserve_unknown(self):
        self.assertIsNone(accounting.reconcile(history(),accounting.FillArchive('missing',{}))['open_fee'])
        for changes in ({'closeTotalPos':None},{'fee':None},{'uTime':'999'},{'cTime':'1000.2'}):
            self.assertIsNone(self.checked(h=history(**changes))['open_fee'])

class LedgerFeeIntegrationTests(unittest.TestCase):
    def test_real_ledger_consumes_scoped_archive_without_touching_realized_pnl(self):
        from scripts import sync_full_ledger as ledger
        with tempfile.TemporaryDirectory() as td,ExitStack() as stack:
            root=Path(td);env=SimpleNamespace(identity='mine',mode='demo')
            for module,name,value in ((evidence,'DB_PATH',root/'e.db'),(ledger_monitor,'DATA',root),
                (ledger,'DATA_DIR',str(root)),(ledger,'LEDGER_JSON_FILE',str(root/'ledger.json')),
                (ledger,'INITIAL_STATE_FILE',str(root/'initial.json')),(ledger,'POSITION_TRACKER_FILE',str(root/'trackers.json'))):
                stack.enter_context(patch.object(module,name,value))
            for r in fills():evidence.append('mine','fill',r)
            h=history(openAvgPx='100',closeAvgPx='101',pnl='2',fundingFee='-.01',realizedPnl='1.95',lever='3')
            stack.enter_context(patch.object(ledger,'selected_environment',return_value=env))
            stack.enter_context(patch.object(ledger,'TARGET_INSTRUMENTS',[{'name':'BTC','instId':INST,'ctVal':1}]))
            stack.enter_context(patch.object(ledger,'read_snapshot',side_effect=[[h],[],[]]))
            stack.enter_context(patch.object(ledger,'close_inputs',return_value={'orders':[],'algos':[],'executions':[]}))
            row=ledger.build_lifecycle_ledger(notify=False)[0]
            self.assertEqual(row['net_pnl'],1.95)
            self.assertEqual(row['accounting_basis'],'exchange_realized_pnl')
            self.assertEqual(row['open_fee']+row['close_fee'],-.03)
            self.assertEqual(row['fee_allocation'],'verified_from_archived_fills')
            self.assertEqual(json.loads((root/'ledger.json').read_text())[0]['close_fee'],-.01)

    def test_pending_settlement_clears_prior_fee_allocations(self):
        with tempfile.TemporaryDirectory() as td,patch.object(ledger_monitor,'DATA',Path(td)):
            row={'status':'holding','instId':INST,'side':'long','environment_id':'mine',
                 'open_fee':-.1,'close_fee':0,'pnl':3,'fee_allocation':'verified'}
            pending=ledger_monitor.project_rows([row],'mine',positions=[])[0]
            self.assertIsNone(pending['open_fee']);self.assertIsNone(pending['close_fee'])
            self.assertEqual(pending['fee_allocation'],'pending_settlement')

    def test_independent_monitor_updates_mirror_and_reports_partial_failures(self):
        from scripts import sync_full_ledger, db_manager
        with tempfile.TemporaryDirectory() as td,ExitStack() as stack:
            root=Path(td)
            for module,name,value in ((ledger_monitor,'DATA',root),(db_manager,'DATA_DIR',str(root)),(db_manager,'DB_PATH',str(root/'mirror.db'))):
                stack.enter_context(patch.object(module,name,value))
            stack.enter_context(patch('scripts.okx_runtime.selected_environment',return_value=SimpleNamespace(identity='mine',mode='demo')))
            row={'id':'closed-1','inst':'BTC','status':'closed','close_time':'test','closed_size':2,
                 'close_px':100,'pnl':None,'gross_pnl':0,'fee':None}
            def build(**kwargs):
                (root/'trading_ledger.json').write_text(json.dumps([row]))
                return [row]
            stack.enter_context(patch.object(sync_full_ledger,'build_lifecycle_ledger',side_effect=build))
            status=ledger_monitor.sync_once()
            self.assertEqual(status['sqlite_mirror'],{'status':'ok','rows':1})
            with sqlite3.connect(root/'mirror.db') as db:
                self.assertEqual(db.execute('select size,fee,gross_pnl,pnl from trades').fetchone(),(2,None,0,None))
            original=(root/'trading_ledger.json').read_bytes()
            with patch.object(db_manager,'sync_json_to_sqlite',side_effect=sqlite3.OperationalError('test lock')):
                status=ledger_monitor.sync_once()
            self.assertEqual(status['status'],'partial')
            self.assertEqual(status['sqlite_mirror']['error_type'],'OperationalError')
            self.assertEqual((root/'trading_ledger.json').read_bytes(),original)
