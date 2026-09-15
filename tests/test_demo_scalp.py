import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from scripts import entry_candidates, scalp_candidates, demo_scalp, risk_policy, strategy_evidence, trade_lock
from test_entry_candidates import package, selection


def minute_package(side='long'):
    p=package(side); at=int(p['data_as_of']*1000)
    p.update(strategy_engine='demo_scalp_v2',instId='BTC-USDT-SWAP',name='BTC')
    for tf,width in [('1M',60000),('5M',300000),('15M',900000)]:
        end=at//width*width; rows=[]
        for i in range(24):
            c=98+i*.04 if tf!='1M' else 100
            rows.append([end-(24-i)*width,c,c+1,c-1,c,100,0,0,'1'])
        if tf=='1M':rows[-1][1:6]=[100,102,99.9,101.5,160]
        if side=='short':
            for row in rows: row[1:5]=[200-row[1],200-row[3],200-row[2],200-row[4]]
        p['entry_candles'][tf]=entry_candidates.seal_candles(rows,tf,at)
    price=101.5 if side=='long' else 98.5
    p.update(price=price,bidPx=price-.01,askPx=price+.01)
    return p


class MinuteScalpTests(unittest.TestCase):
    def test_both_directions_can_generate_validate_and_materialize(self):
        for side in ('long','short'):
            p=minute_package(side);before=copy.deepcopy(p)
            result=entry_candidates.catalog(p)
            self.assertTrue(result['plans'],result)
            plan=result['plans'][0]
            self.assertGreaterEqual(plan['net_rr'],2)
            self.assertTrue(plan['target_observation']['extrapolated'])
            self.assertEqual(plan['horizon'],'scalp')
            row=demo_scalp.materialize(p,plan,p['data_as_of'],risk_policy.Policy())
            self.assertTrue(row['decision']['contract_valid'],row)
            self.assertEqual(row['decision']['invalidation']['timeframe'],'1M')
            self.assertEqual(entry_candidates.validate_live_quote(p,plan['id'],p['price']),plan)
            self.assertEqual(p,before)

    def test_confirmed_five_minute_turn_does_not_wait_for_slow_average_cross(self):
        p=minute_package()
        rows=p['entry_candles']['5M']['rows']
        for i,r in enumerate(rows): r.update(open=102-i*.1,close=102-i*.1,high=104,low=98)
        rows[-2].update(open=101,close=99.8)
        rows[-1].update(open=99.8,close=100.9)
        result=entry_candidates.catalog(p)
        self.assertTrue(any(x['setup']=='scalp_reversal_1m' for x in result['plans']),result)

    def test_no_trigger_and_missing_minute_data_never_force_entry(self):
        p=minute_package();p['entry_candles']['1M']['rows'][-1].update(open=100,close=100,high=101,low=99)
        self.assertFalse(entry_candidates.catalog(p)['plans'])
        del p['entry_candles']['1M']
        self.assertIn('error',entry_candidates.catalog(p))

    def test_expensive_costs_are_not_rescued_by_target_padding(self):
        p=minute_package();cfg=vars(risk_policy.Policy());cfg={**cfg,'slippage':.1}
        result=entry_candidates.catalog(p,cfg)
        self.assertFalse(result['plans'])
        self.assertTrue(any(r['reason']=='net_rr_below_policy' for r in result['checks']))

    def test_short_can_trade_oversold_if_actual_structure_confirms(self):
        p=minute_package('short');p.update(rsi_1h=18,rsi_15m=22,vwap_bias=-1.2)
        self.assertTrue(entry_candidates.catalog(p)['plans'])

    def test_minute_quote_checks_minute_level_not_15m_breakout(self):
        p=minute_package();plan=entry_candidates.catalog(p)['plans'][0]
        for price in (plan['trigger_level']-.01, p['price']+plan['entry_atr']):
            with self.assertRaises(ValueError):entry_candidates.validate_live_quote(p,plan['id'],price)

    def test_legacy_ai_does_not_accidentally_enable_minute_engine(self):
        p=minute_package();del p['strategy_engine']
        self.assertTrue(all(not r['setup'].startswith('scalp_') for r in entry_candidates.catalog(p)['plans']))

    def test_enabled_requires_demo_explicit_switch_and_operator_permission(self):
        with TemporaryDirectory() as temp,patch.object(demo_scalp,'CONFIG',Path(temp)/'flag.json'),patch('scripts.okx_runtime._load_dotenv',return_value={}) as config:
            self.assertFalse(demo_scalp.enabled(SimpleNamespace(mode='demo')))
            demo_scalp.CONFIG.write_text(json.dumps({'enabled':True,'version':'demo-scalp-v2'}))
            self.assertTrue(demo_scalp.enabled(SimpleNamespace(mode='demo')))
            self.assertFalse(demo_scalp.enabled(SimpleNamespace(mode='live')))
            config.return_value={'OKXQUANT_AUTOTRADE_ENABLED':'0'}
            self.assertFalse(demo_scalp.enabled(SimpleNamespace(mode='demo')))

    def test_minute_boundary_waits_in_current_invocation_not_next_minute(self):
        with patch.object(demo_scalp.time,'time',side_effect=[120.7,123.05]),patch.object(demo_scalp.time,'sleep') as sleep:
            self.assertEqual(demo_scalp.closed_frame_time(),123.05)
            self.assertAlmostEqual(sleep.call_args.args[0],2.35)
        with patch.object(demo_scalp.time,'time',return_value=124),patch.object(demo_scalp.time,'sleep') as sleep:
            self.assertEqual(demo_scalp.closed_frame_time(),124)
            sleep.assert_not_called()

    def test_scheduler_keeps_pause_and_minute_frequency(self):
        from okxquant_gateway.scheduler import JOBS,current_jobs
        self.assertEqual(next(j.interval_seconds for j in JOBS if j.name=='demo_scalp'),60)
        with patch('scripts.okx_runtime._load_dotenv',return_value={'OKXQUANT_AUTOTRADE_ENABLED':'0'}):
            self.assertNotIn('demo_scalp',[j.name for j in current_jobs()])

    def test_one_minute_runner_submits_once_and_reuses_no_old_cycle(self):
        import ai_factor_trader as trader
        import public_market as market
        from scripts import ai_brain_trader, instrument_pool, instrument_support, news_connection, ledger_monitor
        from contextlib import ExitStack
        p=minute_package();when=p['data_as_of']+8
        env=SimpleNamespace(mode='demo',identity='offline-minute',configured=True)
        item={'instId':p['instId'],'name':'BTC','ctVal':1,'risk_per_trade_usd':15}
        with TemporaryDirectory() as temp,ExitStack() as stack:
            for target,name,value in [(strategy_evidence,'DB_PATH',Path(temp)/'evidence.db'),(trade_lock,'PATH',Path(temp)/'writer'),(ledger_monitor,'DATA',Path(temp))]:
                stack.enter_context(patch.object(target,name,value))
            stack.enter_context(patch('scripts.okx_runtime.freeze_environment',return_value=env))
            stack.enter_context(patch('scripts.okx_runtime.unfreeze_environment'))
            stack.enter_context(patch.object(trader,'freeze_okx_environment',return_value=env))
            stack.enter_context(patch.object(trader,'unfreeze_okx_environment'))
            stack.enter_context(patch('okxquant_backend.account_connections.assert_current'))
            stack.enter_context(patch.object(demo_scalp,'enabled',return_value=True))
            stack.enter_context(patch.object(demo_scalp.time,'time',return_value=when))
            stack.enter_context(patch.object(market,'begin_signal_frame'))
            stack.enter_context(patch.object(instrument_pool,'load_instruments',return_value=[item]))
            stack.enter_context(patch.object(instrument_support,'pool_support',return_value={'items':{p['instId']:{'can_open':True}}}))
            stack.enter_context(patch.object(ai_brain_trader,'fetch_single_instrument_package',side_effect=lambda _:copy.deepcopy(p)))
            stack.enter_context(patch.object(news_connection,'load_strategy_snapshot',return_value={}))
            stack.enter_context(patch.object(demo_scalp,'load_policy',return_value=risk_policy.Policy()))
            stack.enter_context(patch.object(trader,'is_circuit_breaker_active',return_value=(False,'')))
            stack.enter_context(patch.object(trader,'query_positions',return_value=(True,[],'')))
            stack.enter_context(patch.object(trader,'okx_private_command',side_effect=lambda x:x))
            stack.enter_context(patch.object(trader,'run_cmd_result',return_value={'ok':True,'data':[]}))
            stack.enter_context(patch.object(trader,'execution_limits',return_value={'max_positions':6}))
            def archive(scope,cache,*args,**kwargs):
                for row in cache.values():row['decision_id']='archived-before-send'
            arch=stack.enter_context(patch.object(strategy_evidence,'record_decisions',side_effect=archive))
            submit=stack.enter_context(patch.object(trader,'submit_protected_limit_order',return_value=(True,'exchange-order')))
            result=demo_scalp.run();self.assertEqual(result['status'],'submitted',result)
            submit.assert_called_once();self.assertEqual(submit.call_args.kwargs['horizon'],'scalp')
            self.assertEqual(submit.call_args.kwargs['decision_id'],'archived-before-send')
            self.assertEqual(demo_scalp.run()['status'],'already_evaluated')
            submit.assert_called_once();arch.assert_called_once()
