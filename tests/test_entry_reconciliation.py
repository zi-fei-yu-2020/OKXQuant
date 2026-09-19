import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts import entry_gateway as gateway, strategy_evidence as evidence
from scripts.entry_reconciliation import locate
from scripts.entry_diagnostics import submission_diagnostics
from scripts.risk_policy import RiskRejected
from okxquant_backend.okx_trade_service import OKXAPIError


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(evidence, 'DB_PATH', Path(self.temp.name)/'evidence.db')
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.env = SimpleNamespace(identity='test-demo',api_key='SECRETKEY',secret_key='SECRETVAL',passphrase='PASSPHRASE')
        self.cid = evidence.begin_intent(self.env.identity,'decision','SUI-USDT-SWAP',{'instId':'SUI-USDT-SWAP'})
        with evidence.connection() as db:
            db.execute('UPDATE intents SET at=? WHERE id=?',(time.time()-300,self.cid))
        self.calls=[]
        self.overrides={}

    def request(self, method, path, params, env, **kw):
        self.assertEqual(method,'GET')
        self.assertIs(env,self.env)
        self.calls.append((path,params))
        value = self.overrides.get(path, [] if path!='/api/v5/trade/order' else OKXAPIError('51603','Order does not exist'))
        if callable(value):return value(params)
        if isinstance(value,Exception):raise value
        return value

    def run_reconcile(self):
        with patch.object(gateway,'_request',side_effect=self.request):
            gateway.reconcile_intents(self.env)

    def state(self):
        with evidence.connection() as db:
            return db.execute('SELECT state FROM intents WHERE id=?',(self.cid,)).fetchone()[0]

    def order(self, **kw):
        return {'instId':'SUI-USDT-SWAP','clOrdId':self.cid,'ordId':'123','state':'filled',**kw}

    def test_51603_reconciles_all_sources_before_retiring_intent(self):
        self.run_reconcile()
        self.assertEqual(self.state(),'not_found')
        self.assertEqual(len(self.calls),5)
        for path,params in self.calls:
            if path.endswith(('orders-pending','orders-history')):
                self.assertNotIn('clOrdId',params)
        outcomes=evidence.export_events(self.env.identity,'entry_reconciliation')
        self.assertEqual(outcomes[-1]['payload']['outcome'],'resolved_absent')
        # Idempotent: reconciled intent is not queried or submitted again.
        self.run_reconcile();self.assertEqual(len(self.calls),5)

    def test_successful_empty_order_response_uses_same_proof(self):
        self.overrides['/api/v5/trade/order']=[]
        self.run_reconcile();self.assertEqual(self.state(),'not_found')

    def test_transient_or_auth_errors_do_not_trigger_missing_order_path(self):
        for error in (TimeoutError('timeout'), RuntimeError('HTTP 503'),OKXAPIError('50113','signature'),RuntimeError('OKX 51603: fake string')):
            with self.subTest(error=type(error).__name__):
                self.calls=[]; self.overrides['/api/v5/trade/order']=error
                with self.assertRaises(RiskRejected):self.run_reconcile()
                self.assertEqual(len(self.calls),1);self.assertEqual(self.state(),'unknown')

    def test_every_secondary_read_failure_retains_reservation(self):
        for path in ('orders-pending','orders-history','fills-history','positions'):
            with self.subTest(path=path):
                url='/api/v5/'+('account/' if path=='positions' else 'trade/')+path
                self.overrides={url:TimeoutError('read failed')}
                with self.assertRaises(RiskRejected):self.run_reconcile()
                self.assertEqual(self.state(),'unknown')

    def test_order_states_are_adopted_without_resending(self):
        for state,expected in [('live','pending'),('partially_filled','pending'),('filled','filled'),('canceled','canceled'),('mmp_canceled','mmp_canceled')]:
            with self.subTest(state=state):
                with evidence.connection() as db:db.execute("UPDATE intents SET state='unknown'")
                self.overrides={'/api/v5/trade/order':[self.order(state=state,uTime=state)]}
                self.run_reconcile();self.assertEqual(self.state(),expected)

    def test_exact_history_match_is_adopted(self):
        self.overrides['/api/v5/trade/orders-history']=[self.order(clOrdId='other',ordId='122'),self.order()]
        self.run_reconcile();self.assertEqual(self.state(),'filled')

    def test_live_order_match_stops_absence_path(self):
        self.overrides['/api/v5/trade/orders-pending']=[self.order(state='live')]
        self.run_reconcile();self.assertEqual(self.state(),'pending');self.assertEqual(len(self.calls),2)

    def test_history_pagination_scans_later_pages(self):
        def pages(params):
            if 'after' not in params:return [self.order(clOrdId='other'+str(i),ordId=str(i)) for i in range(100)]
            return [self.order()]
        self.overrides['/api/v5/trade/orders-history']=pages
        self.run_reconcile();self.assertEqual(self.state(),'filled')
        self.assertTrue(any(p.get('after')=='99' for _,p in self.calls))

    def test_repeated_cursor_and_page_limit_do_not_release(self):
        self.overrides['/api/v5/trade/orders-history']=[self.order(clOrdId='other',ordId=str(i)) for i in range(100)]
        with self.assertRaises(RiskRejected):self.run_reconcile()
        self.assertEqual(self.state(),'unknown')

    def test_page_budget_exhausted_is_not_absence(self):
        counter=[0]
        def pages(params):
            counter[0]+=100
            return [self.order(clOrdId='other',ordId=str(i+counter[0])) for i in range(100)]
        self.overrides['/api/v5/trade/orders-history']=pages
        with self.assertRaises(RiskRejected):self.run_reconcile()
        self.assertEqual(counter[0],500);self.assertEqual(self.state(),'unknown')

    def test_fills_without_identity_block_absence(self):
        for row in ({'instId':'SUI-USDT-SWAP','ordId':'456'},self.order()):
            self.overrides={'/api/v5/trade/fills-history':[row]}
            with self.assertRaises(RiskRejected):self.run_reconcile()
            self.assertEqual(self.state(),'unknown')

    def test_unrelated_identified_fills_do_not_block(self):
        self.overrides['/api/v5/trade/orders-history']=[self.order(clOrdId='other',ordId='456')]
        self.overrides['/api/v5/trade/fills-history']=[{'instId':'SUI-USDT-SWAP','ordId':'456'}]
        self.run_reconcile();self.assertEqual(self.state(),'not_found')

    def test_active_or_invalid_positions_cannot_release(self):
        for size in ('1','nan','bad'):
            self.overrides={'/api/v5/account/positions':[{'instId':'SUI-USDT-SWAP','pos':size}]}
            with self.assertRaises(RiskRejected):self.run_reconcile()
            self.assertEqual(self.state(),'unknown')

    def test_recent_or_too_old_intents_stay_unresolved(self):
        for age in (10,8*86400,-1):
            with evidence.connection() as db:db.execute('UPDATE intents SET at=?',(time.time()-age,))
            with self.assertRaises(RiskRejected):self.run_reconcile()
            self.assertEqual(self.state(),'unknown')

    def test_acknowledged_intent_not_erased_by_absence(self):
        evidence.finish_intent(self.cid,'acknowledged',{'order_id':'123'})
        with self.assertRaises(RiskRejected):self.run_reconcile()
        self.assertEqual(self.state(),'acknowledged')

    def test_malformed_or_mismatched_or_duplicate_order_retained(self):
        for rows in ({},[None],[self.order(instId='BTC-USDT-SWAP')],[self.order(clOrdId='other')],[self.order(),self.order()], [self.order(state='mystery')]):
            self.overrides={'/api/v5/trade/order':rows}
            with self.assertRaises(RiskRejected):self.run_reconcile()
            self.assertEqual(self.state(),'unknown')

    def test_legacy_invalid_id_skips_detail_but_requires_full_proof(self):
        with evidence.connection() as db:db.execute('UPDATE intents SET id=?',('x'*40,))
        self.cid='x'*40
        self.run_reconcile();self.assertEqual(self.state(),'not_found')
        self.assertNotIn('/api/v5/trade/order',[p for p,_ in self.calls])

    def test_diagnostics_retain_code_not_credentials(self):
        result={'ok':False,'returncode':1,'stderr':'OKX 51000: api_key=SECRETKEY secret_key=SECRETVAL passphrase=PASSPHRASE token URL https://host/path?access_token=bad Bearer abcdef'}
        d=submission_diagnostics(result,self.env)
        self.assertEqual(d['exchange_code'],'51000');self.assertEqual(d['returncode'],1)
        for s in ('SECRETKEY','SECRETVAL','PASSPHRASE','abcdef','access_token=bad'):self.assertNotIn(s,json.dumps(d))

    def test_exception_evidence_does_not_capture_raw_secrets(self):
        self.overrides['/api/v5/trade/order']=RuntimeError('secret=HIDDEN')
        with self.assertRaises(RiskRejected):self.run_reconcile()
        self.assertNotIn('HIDDEN',str(evidence.export_events(self.env.identity)))


if __name__=='__main__':unittest.main()
