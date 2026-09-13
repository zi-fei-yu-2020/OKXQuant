import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts import entry_opportunities as o, entry_candidates as old, strategy_evidence as ev
from test_entry_candidates import package,selection
from scripts.trading_prompt import candidate,facts_for

class OpportunityTests(unittest.TestCase):
    def test_first_cycle_not_collected_is_pending_without_read_side_effects(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp, patch.object(ev,'DB_PATH',Path(tmp)/'evidence.db'):
            self.assertEqual(o.public_status('demo:a')['status'],'pending')
            self.assertFalse(ev.DB_PATH.exists())
            with sqlite3.connect(ev.DB_PATH) as db:db.execute('CREATE TABLE other(x)')
            before=ev.DB_PATH.read_bytes()
            self.assertEqual(o.public_status('demo:a')['status'],'pending')
            self.assertEqual(before,ev.DB_PATH.read_bytes())

    def test_corrupt_shadow_database_is_not_disguised_as_pending(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ev,'DB_PATH',Path(tmp)/'evidence.db'):
            ev.DB_PATH.write_text('corrupt fixture')
            self.assertEqual(o.public_status('demo:a')['status'],'unavailable')

    def test_shadow_is_pure_and_never_materializable(self):
        for side in ('long','short'):
            p=package(side); before=copy.deepcopy(p);r=o.scan(p)
            self.assertEqual(p,before);self.assertEqual(r['mode'],'shadow');self.assertFalse(r['order_authorized'])
            for op in r['opportunities']:
                self.assertFalse(op['order_authorized'])
                proposal={'candidate_id':op['id'],'action':'BUY_LONG' if side=='long' else 'SELL_SHORT'}
                self.assertFalse(candidate(p,proposal,facts_for(p))['contract_valid'])
                # A breakout-chase entry keeps the 1.5xATR volatility floor; a pullback/retest
                # entry anchors its stop to the retest structure, which may be tighter because
                # the entry sits near defined support/resistance.
                if op['stop_basis']=='max_structural_and_1.5x_closed_atr':
                    self.assertGreaterEqual(abs(op['entry_price']-op['stop_loss_price'])+1e-9,op['atr']*1.5)
                else:
                    self.assertGreater(abs(op['entry_price']-op['stop_loss_price']),0)
            self.assertEqual(old.catalog(p),old.catalog(before))

    def test_bad_data_never_produces_candidates(self):
        for change in ('unsupported','stale','gap','macro','quote','missing'):
            p=package()
            if change=='unsupported':p['environment_support']['can_open']=False
            elif change=='stale':p['data_as_of']+=900
            elif change=='gap':p['entry_candles']['15M']['rows'].pop(-4)
            elif change=='macro':p['macro_4h']='unknown'
            elif change=='quote':p['bidPx']=101
            else:p.pop('entry_candles')
            r=o.scan(p);self.assertTrue(r.get('error'),change);self.assertEqual(r['ready_count'],0)

    def test_macro_veto_is_unchanged(self):
        for side in ('long','short'):
            r=o.scan(package(side))
            self.assertTrue(all(x['side']==side for x in r['opportunities']))
            self.assertTrue(any(x['reason']=='existing_macro_direction_veto' for x in r['checks']))

    def test_target_is_nearest_observed_and_never_padding(self):
        h=[{'close_ms':i*3600000,'high':v,'low':90} for i,v in enumerate([101,105,102,104,110,103]*4)]
        t=o.observed_target(h,'long',100,h[-1]['close_ms'])
        self.assertEqual(t['price'],105);self.assertFalse(t['extrapolated'])
        h[-1]['high']=1000
        t=o.observed_target(h,'long',100,h[-2]['close_ms'])
        self.assertEqual(t['price'],105)
        self.assertIsNone(o.observed_target(h,'long',2000,h[-1]['close_ms']))

    def test_breakout_and_later_retest_use_sealed_volume(self):
        p=package(); bars=p['entry_candles']['15M']['rows']
        bars[-2].update(open=101,high=103.3,low=100.8,close=103,volume=200)
        bars[-1].update(open=102.1,high=103.1,low=101.9,close=102.8,volume=120)
        p.update(price=102.8,bidPx=102.79,askPx=102.81,vol_ratio=0)
        r=o.scan(p)
        retests=[x for x in r['opportunities'] if x['setup']=='breakout_retest']
        self.assertEqual(len(retests),1,r);self.assertTrue(retests[0]['triggered'])
        self.assertEqual(retests[0]['trigger_level'],102)
        self.assertEqual(retests[0]['origin_close_ms'],bars[-2]['close_ms'])
        other=copy.deepcopy(p);other['vol_ratio']=999
        self.assertEqual(o.scan(p),o.scan(other))
        bars[-1].update(close=101.95)
        p.update(price=101.95,bidPx=101.94,askPx=101.96)
        self.assertEqual(next(x for x in o.scan(p)['opportunities'] if x['setup']=='breakout_retest')['state'],'invalidated')

    def test_quote_chasing_is_watch_not_auto_entry(self):
        p=package();b=p['entry_candles']['15M']['rows'][-1]
        b.update(open=101,high=103.3,low=100.8,close=103,volume=200)
        p.update(price=108,bidPx=107.99,askPx=108.01)
        op=next(x for x in o.scan(p)['opportunities'] if x['setup']=='closed_range_breakout')
        self.assertEqual(op['state'],'awaiting_retest');self.assertFalse(op['order_authorized'])

    def test_no_synthetic_target_beyond_history(self):
        p=package();bars=p['entry_candles']['15M']['rows'];bars[-1].update(open=101,high=120,low=100,close=119,volume=200)
        p.update(price=119,bidPx=118.99,askPx=119.01)
        op=next(x for x in o.scan(p)['opportunities'] if x['setup']=='closed_range_breakout')
        self.assertEqual(op['reason'],'no_observed_target');self.assertIsNone(op['target_observation'])

    def test_terminal_idempotence_expiry_and_bounded_retention(self):
        op={'id':'a','created_at':100,'expires_at':200,'state':'ready'}
        first=o.advance([],[op],101);self.assertEqual(first,o.advance(first,[op],101))
        expired=o.advance(first,[],201);self.assertEqual(expired[0]['state'],'expired')
        self.assertEqual(o.advance(expired,[op],202)[0]['state'],'expired')
        self.assertEqual(len(o.advance([],[dict(op,id=str(i)) for i in range(100)],101)),60)

    def test_account_policy_and_out_of_order_isolation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ev,'DB_PATH',Path(tmp)/'evidence.db'):
            p=package();policy=vars(o.Policy())
            first=o.record_cycle('demo:a',[p],policy,'sig')
            second=o.record_cycle('demo:a',[p],policy,'sig')
            self.assertEqual(first['items'],second['items'])
            with ev.connection() as db:
                self.assertEqual(db.execute("select count(*) from events where kind='entry_opportunity_shadow'").fetchone()[0],1)
            self.assertEqual(o.public_status('live:b')['status'],'pending')
            p['data_as_of']-=900
            third=o.record_cycle('demo:a',[p],policy,'sig')
            self.assertEqual(first['items'],third['items'])
            fourth=o.record_cycle('demo:a',[package()],dict(policy,minimum_net_rr=3),'sig')
            self.assertNotEqual(first['namespace'],fourth['namespace'])
            public=o.public_status('demo:a');self.assertNotIn('tracked',public['items'][0])
            self.assertNotIn('scope',public)

if __name__=='__main__':unittest.main()
