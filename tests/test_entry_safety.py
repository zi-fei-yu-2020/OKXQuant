import copy,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts import entry_candidates as entries,position_lifecycle as lifecycle,strategy_origin as origin,strategy_evidence as evidence,trading_prompt as contract
from scripts.fill_accounting import FillArchive
from test_entry_candidates import package

class EntrySafetyTests(unittest.TestCase):
    def test_weak_rebound_against_hourly_trend_is_not_pullback(self):
        for side in ('long','short'):
            p=package(side);p['macro_4h']='4H_MACRO_RANGE'
            hours=p['entry_candles']['1H']['rows']
            for i,r in enumerate(hours):r['close']=105-i*.1 if side=='long' else 95+i*.1
            result=entries.catalog(p)
            self.assertFalse(any(x['action']==('BUY_LONG' if side=='long' else 'SELL_SHORT') and x['setup']=='pullback_reclaim' for x in result['plans']))
            self.assertTrue(any(x['side']==side and x['reason']=='pullback_requires_established_trend' for x in result['checks']))

    def test_aligned_pullback_still_generates_without_confidence_quota(self):
        for side in ('long','short'):
            self.assertTrue(any(p['setup']=='pullback_reclaim' for p in entries.catalog(package(side))['plans']))

    def test_closed_breakout_remains_available_without_hourly_unanimity(self):
        p=package();p['macro_4h']='4H_MACRO_RANGE';p['vol_ratio']=1.5
        bars=p['entry_candles']['15M']['rows']
        for r in bars:r.update(open=100,high=100.2,low=99.8,close=100,volume=100)
        bars[-1].update(open=100,high=100.8,low=100,close=100.6,volume=200)
        for i,r in enumerate(p['entry_candles']['1H']['rows']):r['close']=105-i*.1
        p.update(price=100.6,bidPx=100.59,askPx=100.61)
        plans=entries.catalog(p)['plans']
        self.assertTrue(any(x['setup']=='closed_range_breakout' and x['action']=='BUY_LONG' for x in plans),plans)

    def test_independent_model_cannot_bypass_direction_with_a_story(self):
        from test_trading_prompt_contract import package as p_factory,candidate
        p=p_factory();p['macro_4h']='4H_MACRO_RANGE';p['structure_1h']='1H_SWING_BEAR'
        d=candidate();d['supporting_evidence'][0]['value']='4H_MACRO_RANGE';d['confidence']=100
        result=contract.candidate(p,d,contract.facts_for(p))
        self.assertFalse(result['contract_valid']);self.assertIn('同向1H趋势',result['validation_reason'])

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=patch.object(evidence,'DB_PATH',Path(self.tmp.name)/'evidence.db');p.start();self.addCleanup(p.stop)
        self.pos={'instId':'TEST-USDT-SWAP','side':'long','posId':'new','cTime':'1000','avgPx':100,'pos':1}
        self.key='TEST-USDT-SWAP_long'

    def test_identity_change_discards_old_stop_and_peak_without_cloud_write(self):
        old={**self.pos,'posId':'old','cTime':'500'}
        t={self.key:{'positionIdentity':lifecycle.identity(old,'demo'),'trailingStopPx':99.9,'highWaterMark':120}}
        self.assertEqual(lifecycle.reconcile(t,self.key,self.pos,'demo'),'reset');self.assertNotIn(self.key,t)
        with evidence.connection() as db:self.assertEqual(db.execute("select count(*) from events where kind='tracker_lifecycle_reset'").fetchone()[0],1)

    def test_same_lifecycle_scale_in_preserves_tightened_stop(self):
        t={self.key:{'positionIdentity':lifecycle.identity(self.pos,'demo'),'trailingStopPx':101}}
        self.assertEqual(lifecycle.reconcile(t,self.key,{**self.pos,'pos':2,'avgPx':101},'demo'),'same')
        self.assertEqual(t[self.key]['trailingStopPx'],101)

    def test_scope_legacy_and_missing_identity(self):
        for stored in ({'trailingStopPx':99},{'positionIdentity':lifecycle.identity(self.pos,'live')}):
            t={self.key:stored};self.assertEqual(lifecycle.reconcile(t,self.key,self.pos,'demo'),'reset')
        t={self.key:{'trailingStopPx':99}}
        self.assertEqual(lifecycle.reconcile(t,self.key,{},'demo'),'unknown');self.assertIn(self.key,t)

    def test_real_manager_adopts_new_cloud_stop_not_old_peak(self):
        import ai_factor_trader as trader
        env=SimpleNamespace(identity='demo')
        f={'instId':self.pos['instId'],'name':'TEST','market_data_valid':True,'price':100,'atr':1,'atr_15m':1,'ctVal':1,'precision':2,'type':'crypto'}
        trackers={self.key:{'positionIdentity':lifecycle.identity({**self.pos,'posId':'old'},'demo'),'trailingStopPx':99.9,'highWaterMark':120}}
        orders=[{'slTriggerPx':'90','tpTriggerPx':'120'}]
        with patch.object(trader.market,'_selected',return_value=env),patch('scripts.initial_protection.verify',return_value={'status':'verified','orders':orders}),patch.object(trader,'evaluate_asset_signal',return_value=(0,'HOLD',[],'none','')),patch.object(trader,'ensure_cloud_position_protection',return_value=(True,'verified')),patch.object(trader,'close_position_confirmed') as close:
            trader.manage_position_tp_and_trailing(f,self.pos,trackers,'fixture',[])
        self.assertEqual(trackers[self.key]['trailingStopPx'],90)
        self.assertEqual(trackers[self.key]['highWaterMark'],100);close.assert_not_called()

class StrategyOriginTests(unittest.TestCase):
    def test_direction_is_not_a_trend_label(self):
        for side in ('long','short'):
            result=origin.resolve({'direction':side},FillArchive('missing',{}),{})
            self.assertNotIn('顺势',result['strategy']);self.assertEqual(result['strategy_evidence'],'unlinked')

    def test_exact_opening_order_only_and_no_cross_instrument_attribution(self):
        h={'direction':'long','instId':'BTC-USDT-SWAP','cTime':'10000'}
        f={'ordId':'a','fillTime':'10000','posSide':'long','side':'buy'}
        archive=FillArchive('ok',{'BTC-USDT-SWAP':[f]})
        source={'a':{'strategy':'回收反弹（旧规则）','strategy_evidence':'opening_fill_order_decision_link','instId':'BTC-USDT-SWAP','side':'long'}}
        self.assertIn('回收反弹',origin.resolve(h,archive,source,{'status':'verified','opening_order_ids':['a']})['strategy'])
        source['a']['instId']='ETH-USDT-SWAP'
        self.assertEqual(origin.resolve(h,archive,source,{'status':'verified','opening_order_ids':['a']})['strategy_evidence'],'unlinked')
        self.assertEqual(origin.resolve(h,FillArchive('ok',{'BTC-USDT-SWAP':[f,{**f,'ordId':'b'}]}),source)['strategy_evidence'],'unlinked')

if __name__=='__main__':unittest.main()
