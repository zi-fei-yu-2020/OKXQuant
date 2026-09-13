"""Offline safety regressions: compile only the four allowed production functions.

No trader module import (which loads runtime config); every I/O boundary is a mock.
Run on Windows or WSL: python -m unittest discover -s tests -p test_protection_policy.py
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open

from scripts.protection_policy import oco_coverage, rounded_stop, trigger_geometry
from scripts import exit_policy


INST = 'TEST-USDT-SWAP'


def order(identity='a', **changes):
    return dict({'algoId': identity, 'instId': INST, 'ordType': 'oco', 'state': 'live',
                 'posSide': 'long', 'side': 'sell', 'reduceOnly': 'true', 'sz': '1',
                 'tpTriggerPx': '150', 'slTriggerPx': '90'}, **changes)


def position(side='long', **changes):
    return dict({'instId': INST, 'posSide': side, 'pos': '2', 'markPx': '120', 'avgPx': '100'}, **changes)


def trader_functions():
    names = {'_live_oco_coverage', 'ensure_cloud_position_protection', 'execute_ai_position_management', '_exit_preset'}
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'ai_factor_trader.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(selected) == 4
    env = SimpleNamespace(identity='offline-fake', simulated=True)
    ns = dict(List=list, Dict=dict, Any=object, Tuple=tuple, exit_policy=exit_policy,
              execution_runtime=Mock(return_value={'execution':{'id':'standard'},'signature':'offline-standard'}),
              os=SimpleNamespace(path=SimpleNamespace(exists=Mock(return_value=True))),
              time=SimpleNamespace(time=lambda: 1000, monotonic=lambda: 1000, sleep=Mock()),
              json=json, AI_POSITION_MANAGEMENT_FILE='never-open-a-real-file.json',
              market=SimpleNamespace(_selected=Mock(return_value=env),
                                     get_json=Mock(return_value={'data': [{'instId': INST, 'tickSz': '0.1'}]})),
              algo_reader=SimpleNamespace(AlgoReadError=RuntimeError, read_algo_orders=Mock(),
                  orders_for_instrument=lambda rows, inst: [r for r in rows if r.get('instId') == inst]),
              query_positions=Mock(return_value=(True, [position()], '')),
              okx_private_command=Mock(side_effect=lambda command: command),
              run_cmd_result=Mock(return_value={'ok': True}),
              strategy_evidence=SimpleNamespace(best_effort=Mock()),
              close_position_confirmed=Mock(side_effect=AssertionError('Unexpected close')))
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), ns)
    return ns


class CoveragePolicyTests(unittest.TestCase):
    def test_only_explicit_complete_oco_counts(self):
        for key in ('algoId', 'ordType', 'state', 'side', 'posSide', 'reduceOnly', 'sz', 'tpTriggerPx', 'slTriggerPx'):
            row = order(); row.pop(key)
            with self.subTest(missing=key):
                result = oco_coverage([row], 'long')
                self.assertEqual(result.size, 0)
                self.assertTrue(result.unknown)
        self.assertEqual(oco_coverage([order()], 'long').size, 1)

    def test_invalid_sizes_and_triggers_are_not_coverage(self):
        for key in ('sz', 'tpTriggerPx', 'slTriggerPx'):
            for value in ('', None, True, False, '0', '-1', 'nan', 'inf', '-inf', 'garbage'):
                with self.subTest(key=key, value=value):
                    self.assertEqual(oco_coverage([order(**{key: value})], 'long').size, 0)
        self.assertEqual(oco_coverage([order(sz='', actualSz='2')], 'long').size, 0)
        self.assertEqual(oco_coverage([order(actualSz='0.5')], 'long').size, 0)

    def test_type_state_reduce_only_and_direction_are_explicit(self):
        for changes in ({'ordType': 'conditional'}, {'ordType': 'trigger'}, {'state': 'effective'},
                        {'state': 'partially_filled'}, {'state': ''}, {'reduceOnly': False},
                        {'reduceOnly': 'yes'}, {'side': 'buy'}, {'posSide': ''}):
            with self.subTest(changes=changes):
                self.assertEqual(oco_coverage([order(**changes)], 'long').size, 0)
        for value in (True, 'true', '1', 1):
            self.assertEqual(oco_coverage([order(reduceOnly=value)], 'long').size, 1)
        self.assertEqual(oco_coverage([order(posSide='net')], 'long').size, 1)
        self.assertTrue(oco_coverage([order()], 'net').unknown)

    def test_geometry_uses_mark_before_entry_for_profitable_trailing(self):
        self.assertEqual(oco_coverage([order(slTriggerPx='110')], 'long', 120, 100).size, 1)
        self.assertEqual(oco_coverage([order()], 'long', None, 100).size, 1)
        for mark in (90, 85, 150, 160):
            result = oco_coverage([order()], 'long', mark, 100)
            self.assertEqual(result.size, 0); self.assertTrue(result.unknown)
        short = order(posSide='short', side='buy', slTriggerPx='90', tpTriggerPx='60')
        self.assertEqual(oco_coverage([short], 'short', 80, 100).size, 1)
        self.assertEqual(oco_coverage([short], 'short', 95, 100).size, 0)
        self.assertFalse(trigger_geometry('long', 90, 100))
        self.assertFalse(trigger_geometry('short', 110, 100))

    def test_duplicate_ids_do_not_double_count_and_conflicts_are_unknown(self):
        row = order()
        self.assertEqual(oco_coverage([row, dict(row)], 'long').size, 1)
        result = oco_coverage([row, order(sz='2')], 'long')
        self.assertEqual(result.size, 0); self.assertTrue(result.unknown)
        result = oco_coverage([order(sz='1e308'), order('b', sz='1e308')], 'long')
        self.assertEqual(result.size, 0); self.assertTrue(result.unknown)

    def test_terminal_and_opposite_orders_are_not_protection_or_ambiguity(self):
        rows = [order(state='canceled'), order('b', posSide='short', side='buy')]
        result = oco_coverage(rows, 'long')
        self.assertEqual(result.size, 0); self.assertFalse(result.unknown)

    def test_rounding_strictly_tightens_without_crossing_market(self):
        self.assertEqual(rounded_stop('long', '112.29', 110, 120, '.1'), '112.2')
        self.assertEqual(rounded_stop('short', '87.71', 90, 80, '.1'), '87.8')
        for args in [('long', 110.09, 110, 120, '.1'), ('long', 120, 110, 120, '.1'),
                     ('short', 90.01, 90, 80, '.1'), ('short', 80, 90, 80, '.1'),
                     ('long', 112, 110, 120, 'nan'), ('long', 'nan', 110, 120, '.1'),
                     ('long', 112, 110, 120, 0)]:
            self.assertIsNone(rounded_stop(*args))


class RepairMockTests(unittest.TestCase):
    def setUp(self):
        self.ns = trader_functions()
        self.read = self.ns['algo_reader'].read_algo_orders
        self.write = self.ns['run_cmd_result']

    def ensure(self, **changes):
        args = dict(inst_id=INST, pos_side='long', size=2, tp_px=150, sl_px=90)
        args.update(changes)
        return self.ns['ensure_cloud_position_protection'](**args)

    def test_valid_full_coverage_never_writes_and_initial_read_is_fresh(self):
        self.read.return_value = [order(sz='2')]
        self.assertTrue(self.ensure()[0]); self.write.assert_not_called()
        self.assertTrue(self.read.call_args.kwargs['force'])

    def test_ambiguous_or_crossed_orders_never_trigger_blind_repair(self):
        for row in [order(state='effective'), order(slTriggerPx='121'), order(tpTriggerPx='119'),
                    order(ordType='conditional'), order(reduceOnly=None), order(sz='-2')]:
            with self.subTest(row=row):
                self.read.return_value = [row]
                ok, detail = self.ensure()
                self.assertFalse(ok); self.assertTrue(detail.startswith('UNKNOWN:'))
                self.write.assert_not_called()

    def test_unknown_rows_block_confirmation_even_alongside_full_valid_coverage(self):
        self.read.return_value = [order(sz='2'), order('b', state='effective')]
        self.assertFalse(self.ensure()[0]); self.write.assert_not_called()

    def test_invalid_request_unknown_read_and_changed_position_do_not_write(self):
        for size in (0, -2, float('nan'), float('inf')):
            self.assertFalse(self.ensure(size=size)[0])
        self.read.side_effect = RuntimeError('offline unknown')
        self.assertFalse(self.ensure()[0]); self.write.assert_not_called()
        self.read.side_effect = None; self.read.return_value = []
        self.ns['query_positions'].return_value = (True, [position(pos='1')], '')
        self.assertFalse(self.ensure()[0]); self.write.assert_not_called()

    def test_invalid_repair_geometry_and_missing_price_do_not_write(self):
        self.read.return_value = []
        self.assertFalse(self.ensure(sl_px=121)[0]); self.write.assert_not_called()
        self.ns['query_positions'].return_value = (True, [position(markPx='', avgPx='')], '')
        self.assertFalse(self.ensure()[0]); self.write.assert_not_called()

    def test_exact_gap_is_written_once_and_uncertain_write_is_read_back(self):
        for outcome in ({'ok': True}, {'ok': False}, RuntimeError('write timeout')):
            with self.subTest(outcome=outcome):
                self.write.reset_mock(side_effect=True)
                if isinstance(outcome, Exception): self.write.side_effect = outcome
                else: self.write.return_value = outcome
                self.read.side_effect = [[order()], [order(), order('b')]]
                self.assertTrue(self.ensure()[0]); self.write.assert_called_once()
                self.assertIn('--sz 1.0 ', self.write.call_args.args[0])
                self.assertTrue(self.read.call_args.kwargs['force'])

    def test_fractional_gap_keeps_decimal_contract_precision(self):
        self.ns['query_positions'].return_value = (True, [position(pos='0.3')], '')
        self.read.side_effect = [[order(sz='0.1')], [order(sz='0.3')]]
        self.assertTrue(self.ensure(size=0.3)[0]); self.write.assert_called_once()
        self.assertIn('--sz 0.2 ', self.write.call_args.args[0])

    def test_legacy_coverage_wrapper_rejects_unknown_snapshots(self):
        coverage = self.ns['_live_oco_coverage']
        self.assertEqual(coverage([order(sz='2')], 'long', mark_px=120), 2)
        self.assertEqual(coverage([order(sz='2'), order('b', state='effective')], 'long'), 0)
        self.assertEqual(coverage([order(sz='2')], 'long', mark_px=85), 0)

    def test_small_real_gap_is_not_tolerance_verified(self):
        self.read.side_effect = [[order(sz='1.999')], [order(sz='2')]]
        self.assertTrue(self.ensure()[0]); self.write.assert_called_once()

    def test_unknown_post_write_is_not_retried(self):
        self.read.side_effect = [[], RuntimeError('read timeout')]
        ok, detail = self.ensure()
        self.assertFalse(ok); self.assertTrue(detail.startswith('UNKNOWN:'))
        self.write.assert_called_once()

    def test_market_cross_after_repair_remains_unknown(self):
        self.read.side_effect = [[], [order(sz='2')]]
        self.ns['query_positions'].side_effect = [(True, [position()], ''), (True, [position(markPx='85')], '')]
        ok, detail = self.ensure()
        self.assertFalse(ok); self.assertTrue(detail.startswith('UNKNOWN:')); self.write.assert_called_once()


class AmendmentMockTests(unittest.TestCase):
    def setUp(self):
        self.ns = trader_functions()
        self.read = self.ns['algo_reader'].read_algo_orders
        self.write = self.ns['run_cmd_result']
        self.positions = {INST: position()}
        self.trackers = {INST + '_long': {'trailingStopPx': 105}}
        self.actions = []

    def amend(self, proposed=112.29):
        for tracker in self.trackers.values():
            tracker.setdefault('exitVolatility', {'value':1.,'source':'atr_15m','observed_at':1000})
        payload = {'timestamp': 1000, 'instructions': [{'instId': INST, 'action': 'UPDATE_SL', 'suggested_sl_price': proposed}]}
        self.ns['open'] = mock_open(read_data=json.dumps(payload))
        self.ns['execute_ai_position_management'](self.positions, self.trackers, 'offline', self.actions)

    def test_ai_amendment_cannot_bypass_preset_activation(self):
        self.positions[INST]['markPx'] = '101.3'
        self.amend(100.6)
        self.read.assert_not_called()
        self.write.assert_not_called()
        self.assertTrue(self.actions)

    def test_missing_stale_or_future_exit_atr_cannot_authorize_amendment(self):
        for volatility in ({}, {'value':1.,'observed_at':699}, {'value':1.,'observed_at':1001},
                           {'value':0,'observed_at':1000}, {'value':'nan','observed_at':1000}):
            with self.subTest(volatility=volatility):
                self.trackers[INST+'_long']['exitVolatility'] = volatility
                self.amend()
                self.read.assert_not_called()
                self.write.assert_not_called()

    def test_explicit_ai_exit_is_not_blocked_by_profit_amendment_activation(self):
        payload = {'timestamp':1000,'instructions':[{'instId':INST,'action':'CLOSE_MARKET','confidence':90}]}
        self.ns['open'] = mock_open(read_data=json.dumps(payload))
        self.ns['close_position_confirmed'].side_effect = None
        self.ns['close_position_confirmed'].return_value = (True, 'confirmed')
        self.ns['execute_ai_position_management'](self.positions, self.trackers, 'offline', self.actions)
        self.ns['close_position_confirmed'].assert_called_once()
        self.assertNotIn(INST+'_long', self.trackers)

    def test_every_segment_is_amended_with_write_read_interleaving(self):
        a, b = order(slTriggerPx='105'), order('b', slTriggerPx='110')
        aa, bb = dict(a, slTriggerPx='112.2'), dict(b, slTriggerPx='112.2')
        events = []
        snapshots = iter([[a, b], [aa, b], [aa, bb]])
        self.read.side_effect = lambda *args, **kw: (events.append('read'), next(snapshots))[1]
        self.write.side_effect = lambda *args, **kw: (events.append('write'), {'ok': False})[1]
        self.amend()
        self.assertEqual(events, ['read', 'write', 'read', 'write', 'read'])
        self.assertEqual(self.write.call_count, 2)
        self.assertIn('--algoId b ', self.write.call_args.args[0])
        self.assertIn('--newSlTriggerPx 112.2 ', self.write.call_args.args[0])
        self.assertEqual(self.trackers[INST + '_long']['trailingStopPx'], 112.2)
        self.assertEqual(self.trackers[INST + '_long']['cloudProtection']['status'], 'verified')

    def test_stronger_segment_is_unchanged_and_weakest_segment_is_tracked(self):
        a, b = order(slTriggerPx='115'), order('b', slTriggerPx='105')
        self.read.side_effect = [[a, b], [a, dict(b, slTriggerPx='112.2')]]
        self.amend()
        self.write.assert_called_once(); self.assertIn('--algoId b ', self.write.call_args.args[0])
        self.assertEqual(self.trackers[INST + '_long']['trailingStopPx'], 112.2)

    def test_short_segments_round_up_and_never_loosen(self):
        self.positions = {INST: position('short', markPx='80')}
        self.ns['query_positions'].return_value = (True, list(self.positions.values()), '')
        self.trackers = {INST + '_short': {'trailingStopPx': 95}}
        a = order(posSide='short', side='buy', slTriggerPx='95', tpTriggerPx='60')
        b = order('b', posSide='short', side='buy', slTriggerPx='85', tpTriggerPx='60')
        self.read.side_effect = [[a, b], [dict(a, slTriggerPx='87.8'), b]]
        self.amend(87.71)
        self.write.assert_called_once(); self.assertIn('--newSlTriggerPx 87.8 ', self.write.call_args.args[0])
        self.assertEqual(self.trackers[INST + '_short']['trailingStopPx'], 87.8)

    def test_write_exception_is_reconciled_once_without_resubmission(self):
        a = order(sz='2', slTriggerPx='105')
        self.read.side_effect = [[a], [dict(a, slTriggerPx='112.2')]]
        self.write.side_effect = RuntimeError('write timeout')
        self.amend(); self.write.assert_called_once()
        self.assertEqual(self.trackers[INST + '_long']['cloudProtection']['status'], 'verified')
        self.assertNotIn('pendingStopAmendment', self.trackers[INST + '_long'])

    def test_acknowledgement_without_changed_read_back_stays_unknown(self):
        self.read.return_value = [order(sz='2', slTriggerPx='105')]
        self.amend(); self.write.assert_called_once()
        self.assertEqual(self.trackers[INST + '_long']['cloudProtection']['status'], 'unknown')
        self.assertEqual(self.trackers[INST + '_long']['trailingStopPx'], 105)

    def test_new_concurrent_segment_does_not_publish_partial_confirmation(self):
        a = order(sz='2', slTriggerPx='105')
        self.read.side_effect = [[a], [dict(a, slTriggerPx='112.2'), order('new', slTriggerPx='100')]]
        self.amend(); self.write.assert_called_once()
        self.assertEqual(self.trackers[INST + '_long']['cloudProtection']['status'], 'unknown')

    def test_unknown_first_write_stops_later_segments_and_freezes_confirmation(self):
        a, b = order(slTriggerPx='105'), order('b', slTriggerPx='110')
        self.read.side_effect = [[a, b], RuntimeError('read timeout')]
        self.amend()
        self.write.assert_called_once()
        tracker = self.trackers[INST + '_long']
        self.assertEqual(tracker['trailingStopPx'], 105)
        self.assertEqual(tracker['cloudProtection']['status'], 'unknown')
        self.assertNotIn('verifiedAt', tracker['cloudProtection'])
        self.assertIn('pendingStopAmendment', tracker)
        # The outer management loop may overwrite ordinary coverage verification.
        tracker['cloudProtection'] = {'verifiedAt': 'other-loop', 'detail': 'coverage verified'}
        self.read.side_effect = None; self.read.return_value = [a, b]
        self.amend(); self.write.assert_called_once()  # No next-cycle blind retry.

    def test_partial_success_does_not_publish_full_position_confirmation(self):
        a, b = order(slTriggerPx='105'), order('b', slTriggerPx='110')
        self.read.side_effect = [[a, b], [dict(a, slTriggerPx='112.2'), b], RuntimeError('read timeout')]
        self.amend()
        self.assertEqual(self.write.call_count, 2)
        self.assertEqual(self.trackers[INST + '_long']['trailingStopPx'], 105)
        self.assertEqual(self.trackers[INST + '_long']['cloudProtection']['status'], 'unknown')

    def test_read_back_requires_real_full_coverage_not_just_the_new_stop(self):
        a, b = order(slTriggerPx='105'), order('b', slTriggerPx='110')
        for changes in ({'state': 'effective'}, {'reduceOnly': False}, {'ordType': 'conditional'}, {'sz': '0'}, {'side': 'buy'}, {'tpTriggerPx': '0'}):
            with self.subTest(changes=changes):
                self.setUp()
                self.read.side_effect = [[a, b], [dict(a, slTriggerPx='112.2', **changes), b]]
                self.amend(); self.write.assert_called_once()
                self.assertEqual(self.trackers[INST + '_long']['cloudProtection']['status'], 'unknown')

    def test_other_segment_disappearance_or_loosening_stops_the_batch(self):
        a, b = order(slTriggerPx='105'), order('b', slTriggerPx='110')
        for fresh in ([dict(a, slTriggerPx='112.2', sz='2')],
                      [dict(a, slTriggerPx='112.2'), dict(b, slTriggerPx='100')]):
            with self.subTest(fresh=fresh):
                self.setUp(); self.read.side_effect = [[a, b], fresh]
                self.amend(); self.write.assert_called_once()
                self.assertEqual(self.trackers[INST + '_long']['cloudProtection']['status'], 'unknown')

    def test_concurrently_tightened_later_segment_is_not_overwritten(self):
        a, b = order(slTriggerPx='105'), order('b', slTriggerPx='110')
        self.read.side_effect = [[a, b], [dict(a, slTriggerPx='112.2'), dict(b, slTriggerPx='115')]]
        self.amend(); self.write.assert_called_once()
        self.assertEqual(self.trackers[INST + '_long']['trailingStopPx'], 112.2)

    def test_invalid_position_prices_never_write(self):
        self.read.return_value = [order(sz='2')]
        for value in ('invalid', 'nan', 'inf', None, -1):
            with self.subTest(value=value):
                self.positions[INST]['markPx'] = value
                self.amend(); self.write.assert_not_called()

    def test_missing_tick_and_incomplete_coverage_never_write(self):
        self.read.return_value = [order(sz='2')]
        self.ns['market'].get_json.return_value = {'data': [{'instId': INST, 'tickSz': ''}]}
        self.amend(); self.write.assert_not_called()
        self.read.return_value = [order()]
        self.amend(); self.write.assert_not_called()

    def test_market_moves_across_target_before_write_or_after_write(self):
        self.read.return_value = [order(sz='2', slTriggerPx='105')]
        self.ns['query_positions'].return_value = (True, [position(markPx='112')], '')
        self.amend(); self.write.assert_not_called()
        self.setUp()
        self.read.side_effect = [[order(sz='2', slTriggerPx='105')], [order(sz='2', slTriggerPx='112.2')]]
        self.ns['query_positions'].side_effect = [(True, [position()], ''), (True, [position(markPx='112')], '')]
        self.amend(); self.write.assert_called_once()
        self.assertEqual(self.trackers[INST + '_long']['cloudProtection']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
