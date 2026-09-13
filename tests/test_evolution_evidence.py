import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from scripts import evolution_evidence as review, strategy_evidence as archive
from scripts.ledger_accounting import receipt_financials

SCOPE = 'okx:demo:test'
INST = 'BTC-USDT-SWAP'


def trade(**changes):
    return {'id':'t1', 'inst':'BTC', 'status':'closed', 'environment_id':SCOPE,
            'side':'long', 'net_pnl':1.23456789, 'fee':-.00345678,
            'close_time':'2026-09-08 10:00:00', **changes}


class ReviewAccountingTests(unittest.TestCase):
    def rows(self, values):
        return review.review_rows(values, SCOPE, ['BTC'])

    def test_explicit_unknown_net_never_falls_back_or_counts_as_zero(self):
        for value in (None, '', True, 'nan', float('inf')):
            rows, excluded = self.rows([trade(net_pnl=value, pnl=999)])
            self.assertEqual(rows, [])
            self.assertEqual(excluded, {'unknown_net_pnl':1})
        r = trade(pnl=2); del r['net_pnl']
        self.assertEqual(self.rows([r])[0][0]['net_pnl'], 2)
        self.assertEqual(self.rows([trade(net_pnl=0)])[0][0]['net_pnl'], 0)

    def test_malformed_or_duplicate_rows_do_not_hide_later_good_evidence(self):
        rows, excluded = self.rows([None, trade(), trade(), trade(id='valid', net_pnl=-2)])
        self.assertEqual([r['trade_id'] for r in rows], ['valid'])
        self.assertEqual(excluded['missing_or_duplicate_trade_id'], 2)
        self.assertEqual(excluded['malformed_row'], 1)

    def test_scope_pending_and_reset_are_enforced(self):
        rows, _ = self.rows([trade(account_source_id='other'), trade(environment_id='other'),
                             trade(settlement_status='unverified'), trade(close_time='--')])
        self.assertEqual(rows, [])
        rows, _ = review.review_rows([trade()], SCOPE, ['BTC'], '2026-09-08 11:00:00')
        self.assertEqual(rows, [])

    def test_missing_gross_and_signed_fee_precision_are_preserved(self):
        row = self.rows([trade()])[0][0]
        self.assertIsNone(row['gross_pnl'])
        self.assertEqual(row['fee'], -.00345678)
        self.assertEqual(row['net_pnl'], 1.23456789)
        values, _ = self.rows([trade(fee=-.01, gross_pnl=1, net_pnl=-.02),
                               trade(id='rebate', fee=.005, net_pnl=0)])
        stats = review.feedback(values)
        self.assertEqual(stats['fee_cost'], .01)
        self.assertEqual(stats['rebates'], .005)
        self.assertEqual(stats['losses'], 1)
        self.assertEqual(stats['breakeven'], 1)
        self.assertEqual(stats['friction_reversed_trades'], 1)
        self.assertFalse(stats['auto_promote'])

    def test_unknown_costs_and_zero_losses_do_not_produce_fake_profit_factor(self):
        stats = review.feedback(self.rows([trade(fee=None)])[0])
        self.assertIsNone(stats['fee_cost'])
        self.assertIsNone(stats['profit_factor'])

    def test_ledger_order_does_not_change_review_fingerprint(self):
        rows = [trade(id='b'), trade(id='a')]
        self.assertEqual(self.rows(rows), self.rows(list(reversed(rows))))


class ArchivedDecisionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.db = self.root/'strategy_evidence.db'
        p = patch.object(archive, 'DB_PATH', self.db); p.start(); self.addCleanup(p.stop)
        decision = {'instrument':INST, 'counterfactual':False, 'as_of_ms':1000, 'generated_at_ms':2000,
                    'strategy_version':'code-v1', 'execution_profile_signature':'execution-v1',
                    'decision':{'action':'BUY_LONG','memory_publication':{'prompt_hash':'memory-v1'}},
                    'features':{'price':100,'calculus':{'valid':True,'velocity':.2}}}
        archive.append(SCOPE, 'decision', decision, event_id='decision1')
        with patch.object(archive.time, 'time', return_value=3):
            self.client = archive.begin_intent(SCOPE, 'decision1', INST, {})
        fill = {'billId':'1','ordId':'order1','clOrdId':self.client,'instId':INST,
                'posSide':'long','side':'buy','ts':'4000'}
        archive.append(SCOPE, 'fill', fill, event_id='fill:'+SCOPE+':1')
        self.row = review.review_rows([trade(open_time='1970-01-01 08:00:01', close_time='1970-01-01 08:00:06', fee_allocation='verified_from_archived_fills', fee_reconciliation={'status':'verified','bill_ids':['1']})], SCOPE, ['BTC'])[0][0]

    def change(self, identity, **changes):
        with sqlite3.connect(self.db) as db:
            value = json.loads(db.execute('SELECT payload FROM events WHERE id=?',(identity,)).fetchone()[0])
            value.update(changes); raw = archive.canonical(value)
            db.execute('UPDATE events SET payload=?,digest=? WHERE id=?',
                       (raw,hashlib.sha256(raw.encode()).hexdigest(),identity))

    def test_exact_chain_exposes_only_actual_features_without_mutating_archive(self):
        before = self.db.read_bytes()
        rows = review.enrich([self.row], SCOPE, self.root)
        context = rows[0]['decision_evidence']
        self.assertEqual(context['status'], 'linked')
        self.assertEqual(context['entries'][0]['features']['calculus']['velocity'], .2)
        self.assertNotIn('exit_features', context)
        self.assertEqual(self.db.read_bytes(), before)
        stats = review.feedback(rows)
        self.assertEqual(stats['memory_cohorts'][0]['samples'], 1)
        self.assertEqual(stats['memory_cohorts'][0]['status'], 'descriptive_only')

    def test_missing_archive_never_creates_a_database(self):
        missing = self.root/'other'
        self.assertEqual(review.enrich([self.row], SCOPE, missing)[0]['decision_evidence']['status'], 'unobservable')
        self.assertFalse(missing.exists())

    def test_matching_time_or_symbol_without_client_intent_does_not_prove_entry(self):
        self.change('fill:'+SCOPE+':1', clOrdId='manual_external')
        context = review.enrich([self.row], SCOPE, self.root)[0]['decision_evidence']
        self.assertEqual(context['status'], 'unobservable')
        self.assertFalse(context['entries'])

    def test_unrelated_lifecycle_and_wrong_bill_identity_are_not_linked(self):
        self.change('fill:'+SCOPE+':1', ts='7000')
        self.assertEqual(review.enrich([self.row], SCOPE, self.root)[0]['decision_evidence']['status'], 'unobservable')
        self.change('fill:'+SCOPE+':1', ts='4000', billId='different')
        self.assertEqual(review.enrich([self.row], SCOPE, self.root)[0]['decision_evidence']['status'], 'unobservable')

    def test_foreign_scope_and_bad_digest_are_not_evidence(self):
        self.assertEqual(review.enrich([self.row], 'other', self.root)[0]['decision_evidence']['status'], 'unobservable')
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE events SET digest='bad' WHERE id='decision1'")
        self.assertEqual(review.enrich([self.row], SCOPE, self.root)[0]['decision_evidence']['status'], 'unobservable')

    def test_future_counterfactual_or_wrong_direction_decisions_are_rejected(self):
        for changes in ({'generated_at_ms':5000}, {'as_of_ms':2500}, {'counterfactual':True},
                        {'decision':{'action':'SELL_SHORT'}}, {'instrument':'ETH-USDT-SWAP'}):
            with self.subTest(changes=changes):
                with sqlite3.connect(self.db) as db:
                    original = db.execute('SELECT payload,digest FROM events WHERE id=?',('decision1',)).fetchone()
                self.change('decision1', **changes)
                self.assertEqual(review.enrich([self.row], SCOPE, self.root)[0]['decision_evidence']['status'], 'unobservable')
                with sqlite3.connect(self.db) as db:
                    db.execute('UPDATE events SET payload=?,digest=? WHERE id=?',(*original,'decision1'))

    def test_scale_in_without_exact_link_is_partial_and_not_attributed_to_one_memory(self):
        archive.append(SCOPE, 'fill', {'billId':'2','ordId':'manual','instId':INST,'posSide':'long','side':'buy','ts':'5000'},
                       event_id='fill:'+SCOPE+':2')
        self.row['fee_reconciliation']['bill_ids'].append('2')
        rows = review.enrich([self.row], SCOPE, self.root)
        self.assertEqual(rows[0]['decision_evidence']['status'], 'partial')
        self.assertEqual(review.feedback(rows)['memory_cohorts'], [])

    def test_cohorts_do_not_mix_strategy_or_execution_changes(self):
        rows = review.enrich([self.row], SCOPE, self.root)
        second = copy.deepcopy(rows[0]); second['trade_id']='t2'
        second['decision_evidence']['entries'][0]['execution_profile_signature']='execution-v2'
        self.assertEqual(len(review.feedback(rows+[second])['memory_cohorts']), 2)


class ReceiptFinancialTests(unittest.TestCase):
    def amounts(self, receipt, **changes):
        return receipt_financials(receipt, **{'entry_price':100,'size':2,'contract_value':1,'leverage':3,**changes})

    def test_explicit_realized_unknown_cannot_be_replaced_by_gross(self):
        value = self.amounts({'realizedPnl':None,'pnl':'10','fee':'-1','fundingFee':'0'})
        self.assertIsNone(value['net_pnl']); self.assertEqual(value['accounting_basis'],'unavailable')
        self.assertIsNone(value['roi_pct'])

    def test_only_complete_components_allow_derived_net(self):
        self.assertIsNone(self.amounts({'pnl':'10'})['net_pnl'])
        self.assertAlmostEqual(self.amounts({'pnl':'10','fee':'-.00123456','fundingFee':'0'})['net_pnl'],9.99876544)
        self.assertEqual(self.amounts({'realizedPnl':'0'})['net_pnl'],0)

    def test_missing_margin_never_turns_into_500u_or_zero_roi(self):
        for changes in ({'size':None},{'leverage':None},{'contract_value':0}):
            value = self.amounts({'realizedPnl':1,'pnlRatio':'.01'}, **changes)
            self.assertIsNone(value['margin']); self.assertIsNone(value['roi_pct'])
