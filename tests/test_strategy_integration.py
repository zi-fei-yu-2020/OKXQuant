import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from scripts import entry_gateway, strategy_evidence as evidence, trade_lock, position_guard, algo_reader, evidence_sync
from scripts.risk_policy import RiskRejected
from scripts.okx_runtime import OKXEnvironment
import ai_factor_trader as trader
import okx_runtime
from test_strategy_risk import META

class StrategyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for module,name,value in [(evidence,'DB_PATH',Path(self.temp.name)/'e.db'),(trade_lock,'PATH',Path(self.temp.name)/'writer.lock')]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        self.env=OKXEnvironment('demo','fake','fake','fake')
        cooldown=patch.object(trader,'add_stop_cooldown');cooldown.start();self.addCleanup(cooldown.stop)
        observer=patch.object(position_guard,'observe_equity',return_value={});observer.start();self.addCleanup(observer.stop)

    def test_full_final_preflight_uses_actual_leverage_and_commits_before_write(self):
        identity=evidence.append(self.env.identity,'decision',{'instrument':META['instId'],'features':{'structure_1h':'1H_SWING_BULL'},'decision':{'action':'BUY_LONG','contract_version':'trading-evidence-v1','contract_valid':True,'valid_until':time.time()+300}})
        def private(method,path,params,env):
            self.assertEqual(method,'GET');self.assertEqual(env.mode,'demo')
            if path.endswith('/positions') or path.endswith('/orders-pending'):return []
            if path.endswith('/balance'):return [{'totalEq':'10000','uTime':str(int(time.time()*1000)),'details':[{'ccy':'USDT','availEq':'5000'}]}]
            if path.endswith('/leverage-info'):return [{'posSide':'long','lever':'3'}]
            self.fail('Unexpected private endpoint '+path)
        def public(url,**kwargs):
            self.assertTrue(kwargs.get('simulated'))
            return {'data':[META] if '/instruments?' in url else [{'last':'100','ts':str(int(time.time()*1000))}]}
        with patch.object(entry_gateway,'_request',side_effect=private),patch.object(entry_gateway.public_market,'get_json',side_effect=public):
            plan,client=entry_gateway.prepare(self.env,inst_id=META['instId'],side='long',entry=100,stop=94,take_profit=125,
                requested_size=16,budget=15,decision_id=identity,decision_at=time.time())
        self.assertEqual(plan['leverage'],3);self.assertLessEqual(plan['risk_usdt'],15)
        self.assertEqual(evidence.unresolved(self.env.identity)[0][0],client)
        self.assertTrue(evidence.export_events(self.env.identity,'equity'))

    def test_gateway_rounds_real_tick_and_cli_sends_identical_verified_prices(self):
        from scripts import risk_policy
        identity=evidence.append(self.env.identity,'decision',{'instrument':META['instId'],
            'features':{'structure_1h':'1H_SWING_BULL'},'decision':{'action':'BUY_LONG','contract_version':'trading-evidence-v1','contract_valid':True,'valid_until':time.time()+300}})
        meta={**META,'tickSz':'0.00001'}
        def private(method,path,params,env):
            self.assertEqual(method,'GET')
            if path.endswith('/positions') or path.endswith('/orders-pending'):return []
            if path.endswith('/balance'):return [{'totalEq':'10000','uTime':str(int(time.time()*1000)),'details':[{'ccy':'USDT','availEq':'5000'}]}]
            if path.endswith('/leverage-info'):return [{'posSide':'long','lever':'3'}]
            self.fail(path)
        def public(url,**kwargs):
            return {'data':[meta] if '/instruments?' in url else [{'last':'100','ts':str(int(time.time()*1000))}]}
        with patch.object(entry_gateway,'_request',side_effect=private),patch.object(entry_gateway.public_market,'get_json',side_effect=public):
            plan,client=entry_gateway.prepare(self.env,inst_id=META['instId'],side='long',entry=100.000024,stop=94.000016,take_profit=125.000023,
                requested_size=1,budget=15,decision_id=identity,decision_at=time.time())
        self.assertEqual((plan['entry'],plan['stop'],plan['take_profit']),(100.00002,94.00002,125.00002))
        with patch.object(trader.market,'_selected',return_value=self.env),patch.object(trader.support,'opening_status',return_value={'can_open':True}), \
             patch.object(trader,'run_json_cmd',return_value=[]),patch.object(trader.entry_gateway,'prepare',return_value=(plan,client)), \
             patch.object(trader,'okx_private_command',side_effect=lambda x:x), \
             patch.object(trader,'run_cmd_result',return_value={'ok':True,'data':[{'ordId':'verified'}]}) as wire:
            self.assertTrue(trader.submit_protected_limit_order(META['instId'],'buy','long',1,100.000024,125.000023,94.000016)[0])
        command=wire.call_args.args[0]
        for token in ('--px 100.00002','--tpTriggerPx 125.00002','--slTriggerPx 94.00002'):
            self.assertIn(token,command)

    def test_final_entry_accepts_profitable_stop_using_mark_and_preserves_budget(self):
        position = {'instId': META['instId'], 'posSide': 'long', 'pos': '2',
                    'markPx': '120', 'avgPx': '100', 'imr': '10'}
        oco = {'algoId': 'existing-oco', 'instId': META['instId'], 'ordType': 'oco',
               'state': 'live', 'reduceOnly': 'true', 'posSide': 'long', 'side': 'sell',
               'sz': '2', 'slTriggerPx': '110', 'tpTriggerPx': '150'}
        identity = evidence.append(self.env.identity, 'decision', {
            'instrument': META['instId'], 'position_basis': {'size': 2},
            'features':{'structure_1h':'1H_SWING_BULL'},'decision': {'action': 'BUY_LONG', 'contract_version': 'trading-evidence-v1',
                         'contract_valid': True, 'valid_until': time.time() + 300}})

        def private(method, path, params, env):
            self.assertEqual(method, 'GET')
            if path.endswith('/positions'): return [position]
            if path.endswith('/orders-pending'): return []
            if path.endswith('/balance'):
                return [{'totalEq': '10000', 'uTime': str(int(time.time() * 1000)),
                         'details': [{'ccy': 'USDT', 'availEq': '5000'}]}]
            if path.endswith('/leverage-info'): return [{'posSide': 'long', 'lever': '3'}]
            self.fail('Unexpected private endpoint ' + path)

        def public(url, **kwargs):
            self.assertTrue(kwargs.get('simulated'))
            return {'data': [META] if '/instruments?' in url else [{'last': '120', 'ts': str(int(time.time() * 1000))}]}

        with (
            patch.object(entry_gateway, '_request', side_effect=private),
            patch.object(entry_gateway.public_market, 'get_json', side_effect=public),
            patch.object(entry_gateway, 'read_algo_orders', return_value=[oco]) as read,
        ):
            plan, client = entry_gateway.prepare(self.env, inst_id=META['instId'], side='long',
                entry=120, stop=114, take_profit=145, requested_size=16, budget=15,
                decision_id=identity, decision_at=time.time())
        read.assert_called_once_with(self.env, priority='risk', force=True)
        self.assertAlmostEqual(plan['portfolio_before']['total'], 20.408)
        self.assertEqual(plan['portfolio_before']['long'], plan['portfolio_before']['group'])
        self.assertEqual(plan['leverage'], 3)
        self.assertLessEqual(plan['risk_usdt'], 15)
        self.assertEqual(evidence.unresolved(self.env.identity)[0][0], client)

    def test_invalid_existing_oco_blocks_final_entry_before_reservation(self):
        position = {'instId': META['instId'], 'posSide': 'long', 'pos': '2',
                    'markPx': '120', 'avgPx': '100', 'imr': '10'}
        oco = {'algoId': 'existing-oco', 'instId': META['instId'], 'ordType': 'oco',
               'state': 'live', 'reduceOnly': 'true', 'posSide': 'long', 'side': 'sell',
               'sz': '2', 'slTriggerPx': '110', 'tpTriggerPx': '150'}
        cases = [[{key: value for key, value in oco.items() if key != field}]
                 for field in ('ordType', 'state', 'reduceOnly', 'side', 'tpTriggerPx')]
        cases += [[{**oco, **changes}] for changes in (
            {'state': 'effective'}, {'ordType': 'conditional'}, {'sz': 'nan'},
            {'slTriggerPx': '120'}, {'tpTriggerPx': '119'})]
        cases += [[{**oco, 'sz': '1'}, {**oco, 'sz': '1'}],
                  [oco, {**oco, 'algoId': 'unknown', 'state': 'effective'}]]
        identity = evidence.append(self.env.identity, 'decision', {
            'instrument': META['instId'], 'position_basis': {'size': 2},
            'features':{'structure_1h':'1H_SWING_BULL'},'decision': {'action': 'BUY_LONG', 'contract_version': 'trading-evidence-v1',
                         'contract_valid': True, 'valid_until': time.time() + 300}})

        snapshot_ts = str(int(time.time() * 1000))

        def private(method, path, params, env):
            self.assertEqual(method, 'GET')
            self.assertEqual(env, self.env)
            if path.endswith('/positions'): return [position]
            if path.endswith('/orders-pending'): return []
            if path.endswith('/balance'):
                return [{'totalEq': '10000', 'uTime': snapshot_ts,
                         'details': [{'ccy': 'USDT', 'availEq': '5000'}]}]
            self.fail('Invalid coverage must reject before later preflight reads: ' + path)

        for rows in cases:
            with (
                self.subTest(rows=rows),
                patch.object(entry_gateway, '_request', side_effect=private),
                patch.object(entry_gateway.public_market, 'get_json', return_value={'data': [META]}),
                patch.object(entry_gateway, 'read_algo_orders', return_value=rows) as read,
                patch.object(evidence, 'begin_intent', wraps=evidence.begin_intent) as reserve,
            ):
                with self.assertRaisesRegex(RiskRejected, 'coverage'):
                    entry_gateway.prepare(self.env, inst_id=META['instId'], side='long',
                        entry=120, stop=114, take_profit=145, requested_size=1, budget=15,
                        decision_id=identity, decision_at=time.time())
                read.assert_called_once_with(self.env, priority='risk', force=True)
                reserve.assert_not_called()
                self.assertEqual(evidence.unresolved(self.env.identity), [])

    def test_stale_or_foreign_decision_stops_before_private_reads(self):
        with patch.object(entry_gateway,'_request') as get:
            for identity,at in [('missing',time.time()),('old',time.time()-301)]:
                with self.assertRaises(RiskRejected):entry_gateway.prepare(self.env,inst_id=META['instId'],side='long',entry=100,stop=94,take_profit=125,requested_size=1,budget=15,decision_id=identity,decision_at=at)
        get.assert_not_called()

    def test_guard_no_positions_has_no_model_or_trade_calls(self):
        with patch.object(okx_runtime,'freeze_environment',return_value=self.env),patch.object(okx_runtime,'unfreeze_environment'),patch.object(position_guard,'read_positions',return_value=[]),patch.object(trader,'load_trackers',return_value={}),patch.object(trader,'save_trackers'),patch.object(trader,'execute_batch_ai_brain_cycle') as llm,patch.object(trader,'submit_protected_limit_order') as entry,patch.object(trader,'run_cmd_result') as write:
            result=position_guard.run_guard()
        self.assertEqual(result['positions'],0);llm.assert_not_called();entry.assert_not_called();write.assert_not_called()

    def test_guard_public_failure_does_not_disable_private_fail_closed(self):
        p={'instId':META['instId'],'posSide':'long','pos':'1','avgPx':'100','markPx':'100'}
        for observe in [False,True]:
            with patch.object(okx_runtime,'freeze_environment',return_value=self.env),patch.object(okx_runtime,'unfreeze_environment'),patch.object(position_guard,'read_positions',return_value=[p]),patch.object(trader,'load_trackers',return_value={}),patch.object(trader,'save_trackers'),patch.object(trader.market,'get_json',side_effect=RuntimeError('public unavailable')),patch.object(algo_reader,'read_algo_orders',side_effect=RuntimeError('private unknown')),patch.object(trader,'close_position_confirmed',return_value=(True,'closed')) as close,patch.object(trader,'execute_batch_ai_brain_cycle') as llm:
                result=position_guard.run_guard(observe_only=observe)
            self.assertEqual(close.call_count,0 if observe else 1);llm.assert_not_called()
            self.assertEqual(result['observe_only'],observe)

    def test_guard_unknown_does_not_delete_tracker_when_close_failed(self):
        p={'instId':META['instId'],'posSide':'long','pos':'1','avgPx':'100','markPx':'100'}
        trackers={META['instId']+'_long':{'trailingStopPx':90}}
        with patch.object(okx_runtime,'freeze_environment',return_value=self.env),patch.object(okx_runtime,'unfreeze_environment'),patch.object(position_guard,'read_positions',return_value=[p]),patch.object(trader,'load_trackers',return_value=trackers),patch.object(trader,'save_trackers') as save,patch.object(trader.market,'get_json',side_effect=RuntimeError()),patch.object(algo_reader,'read_algo_orders',side_effect=RuntimeError()),patch.object(trader,'close_position_confirmed',return_value=(False,'unknown')):
            position_guard.run_guard()
        self.assertIn(META['instId']+'_long',save.call_args.args[0])

    def test_ledger_unknown_read_never_replaces_existing_file(self):
        import sync_full_ledger as ledger
        from types import SimpleNamespace
        with patch.object(ledger.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='unknown')):
            with self.assertRaises(RuntimeError):ledger.read_cli_list('okx account positions-history --json')
        with patch.object(ledger.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{}',stderr='')):
            with self.assertRaises(RuntimeError):ledger.read_cli_list('okx account positions-history --json')

    def test_ledger_preserves_archived_closed_rows_outside_recent_page(self):
        import sync_full_ledger as ledger
        root=Path(self.temp.name);path=root/'ledger.json'
        old={'id':'old','status':'closed','close_time':'2026-09-01 00:00:00','pnl':1}
        path.write_text(json.dumps([old]))
        with patch.object(ledger,'DATA_DIR',str(root)),patch.object(ledger,'LEDGER_JSON_FILE',str(path)),patch.object(ledger,'INITIAL_STATE_FILE',str(root/'missing')),patch.object(ledger,'POSITION_TRACKER_FILE',str(root/'missing_tracker')),patch.object(ledger,'read_cli_list',return_value=[]):
            rows=ledger.build_lifecycle_ledger()
        self.assertEqual(rows,[old]);self.assertEqual(json.loads(path.read_text()),[old])

    def test_partial_fill_archive_resumes_cursor_without_claiming_full_history(self):
        now=int(time.time()*1000)
        page=[{'billId':str(1000-i),'ordId':'2','instId':'TEST','ts':str(now-i)} for i in range(100)]
        with patch.object(evidence_sync,'_request',return_value=page):
            first=evidence_sync.collect_fills(self.env,max_pages=1)
        self.assertFalse(first['complete'])
        with patch.object(evidence_sync,'_request',return_value=[]) as read:
            second=evidence_sync.collect_fills(self.env,max_pages=1)
        self.assertTrue(second['complete']);self.assertEqual(read.call_args.args[2]['after'],page[-1]['billId'])
        self.assertEqual(len(evidence.export_events(self.env.identity,'fill')),100)

if __name__=='__main__':unittest.main()
