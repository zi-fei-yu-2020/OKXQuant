import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from scripts import risk_policy as risk, strategy_evidence as evidence, trade_lock, entry_gateway
from scripts.okx_runtime import OKXEnvironment
import ai_factor_trader as trader

META={'instId':'TEST-USDT-SWAP','ctType':'linear','settleCcy':'USDT','state':'live','ctVal':'1','ctMult':'1','lotSz':'0.1','minSz':'0.1','tickSz':'0.01'}

class StrategyRiskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for module,name,value in [(evidence,'DB_PATH',Path(self.temp.name)/'evidence.db'),(trade_lock,'PATH',Path(self.temp.name)/'writer.lock')]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        self.env=OKXEnvironment('demo','fake','fake','fake')

    def plan(self,**extra):
        params=dict(metadata=META,side='long',entry=100,stop=94,take_profit=125,requested_size=16,budget_usdt=15,equity=10000,available=5000,leverage=3)
        params.update(extra);return risk.order_plan(**params)

    def test_final_stop_budget_never_rounds_up_to_original_16_contracts(self):
        p=self.plan();self.assertLessEqual(p['risk_usdt'],15);self.assertLess(p['size'],3)
        self.assertAlmostEqual(round(p['size']/.1)*.1,p['size'])

    def test_too_small_budget_is_rejected_not_forced_to_one_lot(self):
        with self.assertRaises(risk.RiskRejected):self.plan(budget_usdt=.01)

    def test_short_geometry_and_actual_leverage_are_checked(self):
        self.assertLessEqual(self.plan(side='short',stop=106,take_profit=75)['risk_usdt'],15)
        for changes in [{'leverage':20},{'stop':101},{'entry':100.001},{'metadata':{**META,'lotSz':''}},{'requested_size':float('nan')},{'take_profit':112}]:
            with self.subTest(changes=changes),self.assertRaises(risk.RiskRejected):self.plan(**changes)

    def test_portfolio_margin_and_group_budget_include_reserved_risk(self):
        p=self.plan(portfolio={'total':199,'long':199,'group':199})
        self.assertLessEqual(p['risk_usdt'],1)
        with self.assertRaises(risk.RiskRejected):self.plan(existing_margin=600)
        with self.assertRaises(risk.RiskRejected):self.plan(portfolio={'total':200,'long':200,'group':200})

    def test_stop_is_strictly_monotonic_both_directions(self):
        self.assertFalse(risk.monotonic_stop('long',110,105,120))
        self.assertFalse(risk.monotonic_stop('short',90,95,80))
        self.assertFalse(risk.monotonic_stop('long',110,110,120))
        self.assertTrue(risk.monotonic_stop('long',110,112,120))
        self.assertTrue(risk.monotonic_stop('short',90,88,80))

    def test_stop_function_refuses_loosen_and_does_not_advance_on_unknown(self):
        position = {'TEST-USDT-SWAP': {'instId': 'TEST-USDT-SWAP', 'posSide': 'long',
                                     'pos': '1', 'markPx': '120', 'avgPx': '100'}}
        old = {'algoId': 'a', 'instId': 'TEST-USDT-SWAP', 'ordType': 'oco', 'posSide': 'long',
               'side': 'sell', 'state': 'live', 'reduceOnly': 'true', 'sz': '1',
               'tpTriggerPx': '150', 'slTriggerPx': '110'}
        for proposed, last, expected_calls, expected_stop in [
            (105, old, 0, 110),
            (112, {**old, 'slTriggerPx': '112'}, 1, 112),
            (112, RuntimeError('unknown'), 1, 110),
        ]:
            tracker = {'TEST-USDT-SWAP_long': {'trailingStopPx': 110,
                'exitVolatility': {'value': 1., 'source': 'atr_15m', 'observed_at': int(time.time())}}}
            payload = {'timestamp': int(time.time()), 'instructions': [
                {'instId': 'TEST-USDT-SWAP', 'action': 'UPDATE_SL', 'suggested_sl_price': proposed}]}
            with (
                self.subTest(proposed=proposed, unknown=isinstance(last, Exception)),
                patch.object(trader.os.path, 'exists', return_value=True),
                patch('builtins.open', side_effect=lambda *args, **kwargs: io.StringIO(json.dumps(payload))),
                patch.object(trader.algo_reader, 'read_algo_orders', side_effect=[
                    [old], last if isinstance(last, Exception) else [last]]) as read,
                patch.object(trader.market, '_selected', return_value=self.env),
                patch.object(trader.market, 'get_json', return_value={'data': [META]}) as metadata,
                patch.object(trader, 'query_positions', return_value=(True, list(position.values()), '')) as positions,
                patch.object(trader, 'okx_private_command', side_effect=lambda command: command),
                patch.object(trader, 'run_cmd_result', return_value={'ok': False}) as write,
                patch.object(trader.strategy_evidence, 'best_effort') as evidence_write,
            ):
                trader.execute_ai_position_management(position, tracker, 'test', [])
                self.assertEqual(write.call_count, expected_calls)
                state = tracker['TEST-USDT-SWAP_long']
                self.assertEqual(state['trailingStopPx'], expected_stop)
                self.assertEqual(read.call_count, 1 + expected_calls)
                self.assertTrue(all(call.kwargs['force'] for call in read.call_args_list))
                metadata.assert_called_once()
                if not expected_calls:
                    positions.assert_not_called()
                    evidence_write.assert_not_called()
                    self.assertNotIn('pendingStopAmendment', state)
                    self.assertNotIn('cloudProtection', state)
                elif isinstance(last, Exception):
                    positions.assert_called_once_with()
                    self.assertEqual(state['cloudProtection']['status'], 'unknown')
                    self.assertNotIn('verifiedAt', state['cloudProtection'])
                    pending = dict(state['pendingStopAmendment'])
                    self.assertEqual(pending['algoId'], 'a')
                    self.assertEqual(float(pending['requestedStop']), proposed)
                    self.assertFalse(evidence_write.call_args.args[2]['verified'])
                    # Ordinary coverage verification must not erase an unresolved write.
                    state['cloudProtection'] = {'verifiedAt': 'other-loop', 'detail': 'coverage verified'}
                    read.side_effect = [[old]]
                    trader.execute_ai_position_management(position, tracker, 'test', [])
                    write.assert_called_once()
                    positions.assert_called_once_with()
                    metadata.assert_called_once()
                    evidence_write.assert_called_once()
                    self.assertEqual(read.call_count, 3)
                    self.assertEqual(state['pendingStopAmendment'], pending)
                    self.assertEqual(state['trailingStopPx'], 110)
                else:
                    self.assertEqual(positions.call_count, 2)
                    self.assertEqual(state['cloudProtection']['status'], 'verified')
                    self.assertEqual(state['cloudProtection']['verifiedAt'], 'test')
                    self.assertNotIn('pendingStopAmendment', state)
                    # An uncertain write can succeed only through authoritative read-back.
                    self.assertFalse(evidence_write.call_args.args[2]['transport_ok'])
                    self.assertTrue(evidence_write.call_args.args[2]['verified'])

    def test_drawdown_uses_marked_equity_and_adjusts_external_flow(self):
        first=risk.update_equity_state(None,equity=1000,at=100000,cash_flow=0,complete=True)
        deposit=risk.update_equity_state(first,equity=1500,at=100001,cash_flow=500,complete=True)
        self.assertEqual(deposit['peak_drawdown'],0)
        loss=risk.update_equity_state(deposit,equity=1400,at=100002,cash_flow=0,complete=True)
        self.assertTrue(loss['blocked'])
        with self.assertRaises(risk.RiskRejected):risk.update_equity_state(first,equity=1000,at=100002,cash_flow=0,complete=False)

    def test_unknown_existing_or_pending_stop_blocks_exposure(self):
        position={'instId':META['instId'],'pos':'1','posSide':'long','markPx':'100'}
        with self.assertRaises(risk.RiskRejected):risk.exposure([position],[],[],{META['instId']:META})
        algo={'algoId':'existing-oco','instId':META['instId'],'ordType':'oco','state':'live','reduceOnly':'true','side':'sell','posSide':'long','sz':'1','slTriggerPx':'90','tpTriggerPx':'125'}
        x=risk.exposure([position],[],[algo],{META['instId']:META});self.assertGreater(x['total'],10)
        with self.assertRaises(risk.RiskRejected):risk.exposure([],[{'instId':META['instId'],'posSide':'long','sz':'1','px':'100'}],[],{META['instId']:META})

    def test_durable_intent_prevents_duplicate_decision_and_redacts_secrets(self):
        identity=evidence.begin_intent('demo','decision','TEST',{'instId':'TEST','api_key':'NEVERSTORE','size':1})
        with self.assertRaises(sqlite3.IntegrityError):evidence.begin_intent('demo','decision','TEST',{})
        self.assertEqual(len(evidence.unresolved('demo')),1)
        self.assertNotIn('NEVERSTORE',str(evidence.unresolved('demo')))
        evidence.finish_intent(identity,'filled');self.assertEqual(evidence.unresolved('demo'),[])

    def test_evidence_is_idempotent_but_rejects_mutated_same_identity(self):
        evidence.append('demo','fill',{'fee':1},'fill1');evidence.append('demo','fill',{'fee':1},'fill1')
        self.assertEqual(len(evidence.export_events('demo')),1)
        with self.assertRaises(ValueError):evidence.append('demo','fill',{'fee':2},'fill1')

    def test_writer_reentrant_and_released_only_during_inference(self):
        entered=threading.Event()
        def other():
            with trade_lock.writer(timeout=2):entered.set()
        with trade_lock.writer():
            with trade_lock.writer():pass
            thread=threading.Thread(target=other);thread.start()
            self.assertFalse(entered.wait(.05))
            with trade_lock.inference_window():self.assertTrue(entered.wait(1))
        thread.join(2);self.assertFalse(thread.is_alive())

    def test_uncertain_entry_is_reconciled_read_only_not_resent(self):
        evidence.begin_intent(self.env.identity,'d','TEST',{'instId':'TEST'})
        with patch.object(entry_gateway,'_request',side_effect=RuntimeError('not found')) as get:
            with self.assertRaises(risk.RiskRejected):entry_gateway.reconcile_intents(self.env)
        get.assert_called_once();self.assertEqual(get.call_args.args[0],'GET')

class ExposureCoverageTests(unittest.TestCase):
    def setUp(self):
        self.position = {'instId': META['instId'], 'posSide': 'long', 'pos': '2',
                         'markPx': '120', 'avgPx': '100'}
        self.oco = {'algoId': 'existing-oco', 'instId': META['instId'], 'ordType': 'oco',
                    'state': 'live', 'reduceOnly': 'true', 'posSide': 'long', 'side': 'sell',
                    'sz': '2', 'slTriggerPx': '110', 'tpTriggerPx': '150'}

    def exposure(self, rows, position=None, **kwargs):
        return risk.exposure([self.position if position is None else position], [], rows,
                             {META['instId']: META}, **kwargs)

    def test_every_required_oco_field_is_explicit(self):
        for field in ('algoId', 'instId', 'ordType', 'state', 'reduceOnly', 'posSide',
                      'side', 'sz', 'slTriggerPx', 'tpTriggerPx'):
            row = dict(self.oco); row.pop(field)
            with self.subTest(missing=field), self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
                self.exposure([row])

    def test_invalid_types_states_and_reduce_only_block_final_risk(self):
        for changes in ({'ordType': 'conditional'}, {'ordType': 'trigger'}, {'ordType': 'limit'},
                        {'state': 'effective'}, {'state': 'partially_filled'}, {'state': 'unknown'},
                        {'state': 'canceled'}, {'reduceOnly': False}, {'reduceOnly': 'yes'},
                        {'posSide': 'short'}, {'side': 'buy'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
                self.exposure([{**self.oco, **changes}])

    def test_nonpositive_nonfinite_or_missing_quantities_and_triggers_are_rejected(self):
        for field in ('sz', 'slTriggerPx', 'tpTriggerPx'):
            for value in (None, '', True, False, 'invalid', '-1', '0', 'nan', 'inf', '-inf'):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
                    self.exposure([{**self.oco, field: value}])
        with self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
            self.exposure([{**self.oco, 'sz': '', 'actualSz': '2'}])
        with self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
            self.exposure([{**self.oco, 'actualSz': '1'}])

    def test_duplicate_rows_cannot_fill_a_gap_or_inflate_risk(self):
        half = {**self.oco, 'sz': '1'}
        with self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
            self.exposure([half, dict(half)])
        self.assertEqual(self.exposure([self.oco, dict(self.oco)]), self.exposure([self.oco]))
        with self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
            self.exposure([self.oco, {**self.oco, 'slTriggerPx': '109'}])

    def test_unknown_rows_block_even_when_valid_rows_cover_the_whole_position(self):
        for changes in ({'state': 'effective'}, {'reduceOnly': None}, {'ordType': 'conditional'},
                        {'side': None}, {'posSide': None}, {'sz': 'nan'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
                self.exposure([self.oco, {**self.oco, 'algoId': 'ambiguous', **changes}])
        for rows in (None, {}, [None], [self.oco, {'algoId': 'missing-instrument'}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
                self.exposure(rows)

    def test_real_gap_below_old_point_one_percent_tolerance_is_rejected(self):
        with self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
            self.exposure([{**self.oco, 'sz': '1.9995'}])

    def test_mark_not_entry_controls_profitable_stop_geometry_in_both_directions(self):
        for side, mark, stop, tp, expected in [('long', '120', '110', '150', 20.408),
                                               ('short', '80', '90', '60', 20.272)]:
            with self.subTest(side=side):
                position = {**self.position, 'posSide': side, 'markPx': mark}
                row = {**self.oco, 'posSide': side, 'side': 'sell' if side == 'long' else 'buy',
                       'slTriggerPx': stop, 'tpTriggerPx': tp}
                result = self.exposure([row], position)
                self.assertAlmostEqual(result['total'], expected)
                self.assertEqual(result[side], result['total'])
                self.assertEqual(result['group'], result['total'])
                self.assertEqual(result['short' if side == 'long' else 'long'], 0)
                for field, prices in [('slTriggerPx', (mark, '121' if side == 'long' else '79')),
                                      ('tpTriggerPx', (mark, '119' if side == 'long' else '81'))]:
                    for price in prices:
                        with self.subTest(field=field, price=price), self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
                            self.exposure([{**row, field: price}], position)

    def test_net_position_sign_and_close_side_are_preserved(self):
        for signed_size, mark, stop, tp, close_side in [('2', '120', '110', '150', 'sell'),
                                                       ('-2', '80', '90', '60', 'buy')]:
            with self.subTest(size=signed_size):
                position = {**self.position, 'posSide': 'net', 'pos': signed_size, 'markPx': mark}
                row = {**self.oco, 'posSide': 'net', 'side': close_side,
                       'slTriggerPx': stop, 'tpTriggerPx': tp}
                self.assertGreater(self.exposure([row], position)['total'], 20)
                with self.assertRaisesRegex(risk.RiskRejected, 'coverage'):
                    self.exposure([{**row, 'side': 'buy' if close_side == 'sell' else 'sell'}], position)

    def test_segment_worst_stop_contract_multiplier_and_cost_budget_are_unchanged(self):
        metadata = {META['instId']: {**META, 'ctVal': '3', 'ctMult': '2'}}
        policy = risk.Policy(taker_fee=.0007, slippage=.0013)
        for side, mark, stops, tp, expected in [('long', '120', ('105', '110'), '150', 183.168),
                                               ('short', '80', ('90', '95'), '60', 182.112)]:
            with self.subTest(side=side):
                position = {**self.position, 'posSide': side, 'markPx': mark}
                rows = [{**self.oco, 'algoId': str(index), 'sz': '1', 'posSide': side,
                         'side': 'sell' if side == 'long' else 'buy', 'slTriggerPx': stop, 'tpTriggerPx': tp}
                        for index, stop in enumerate(stops)]
                result = risk.exposure([position], [], rows, metadata, policy)
                self.assertAlmostEqual(result['total'], expected)
                self.assertEqual(result['group'], result[side])
                self.assertEqual(result['total'], result[side])

    def test_missing_or_invalid_mark_never_falls_back_to_entry(self):
        for mark in (None, '', 'nan', 'inf', '-1', '0'):
            with self.subTest(mark=mark), self.assertRaises(risk.RiskRejected):
                self.exposure([self.oco], {**self.position, 'markPx': mark})

    def test_valid_oco_still_must_respect_liquidation_boundary(self):
        with self.assertRaisesRegex(risk.RiskRejected, 'liquidation'):
            self.exposure([self.oco], {**self.position, 'liqPx': '111'})
        row = {**self.oco, 'posSide': 'short', 'side': 'buy', 'slTriggerPx': '90', 'tpTriggerPx': '60'}
        with self.assertRaisesRegex(risk.RiskRejected, 'liquidation'):
            self.exposure([row], {**self.position, 'posSide': 'short', 'markPx': '80', 'liqPx': '89'})

    def test_other_instrument_and_opposite_position_do_not_pollute_valid_coverage(self):
        unrelated = {**self.oco, 'algoId': 'other', 'instId': 'OTHER-USDT-SWAP', 'state': 'effective'}
        opposite = {**self.oco, 'algoId': 'opposite', 'posSide': 'short', 'side': 'buy'}
        self.assertEqual(self.exposure([self.oco, unrelated, opposite]), self.exposure([self.oco]))


if __name__=='__main__':unittest.main()
