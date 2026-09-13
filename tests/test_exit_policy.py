"""Offline regressions for the full entry/exit review; no real trades or models."""
import copy
import json
from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

from scripts import exit_policy, profit_protection, risk_policy
import ai_factor_trader as trader

NOW = 2_000_000_000
INST = 'TEST-USDT-SWAP'


def runtime(preset):
    return {'execution': {'id': preset}, 'signature': 'offline-' + preset}


class ExitPresetTests(unittest.TestCase):
    def test_last_verified_preset_survives_json_reload_and_unreadable_activation(self):
        tracker = {}
        expected, _ = exit_policy.resolve(tracker, lambda: runtime('small300'))
        reloaded = json.loads(json.dumps(tracker))
        snapshot = copy.deepcopy(reloaded['exitPolicy'])
        actual, status = exit_policy.resolve(reloaded, Mock(side_effect=ValueError('secret must not be logged')))
        self.assertEqual(expected, actual)
        self.assertEqual(status['source'], 'last_verified')
        self.assertEqual(reloaded['exitPolicy'], snapshot)
        self.assertNotIn('secret', json.dumps(reloaded))

    def test_unknown_or_corrupt_preset_uses_explicit_conservative_fallback(self):
        for saved in (None, [], {}, {'version': exit_policy.VERSION, 'preset_id': []},
                      {'version': 'old', 'preset_id': 'standard', 'execution_signature': 'old'}):
            with self.subTest(saved=saved):
                tracker = {'exitPolicy': saved}
                ex, status = exit_policy.resolve(tracker, lambda: runtime('unknown'))
                self.assertEqual(status['source'], 'conservative_fallback')
                self.assertEqual(status['preset_id'], 'fallback')
                self.assertEqual(ex['time_stop_seconds'], 14400)
                self.assertEqual(ex['tier1_breakeven_atr'], 1.8)
                self.assertEqual(ex['tier1_floor_atr'], .5)
                self.assertEqual(tracker['exitPolicy'], saved)

    def test_preset_copies_and_accounts_do_not_share_mutable_state(self):
        a, b = {}, {}
        ex, _ = exit_policy.resolve(a, lambda: runtime('small300'))
        ex['time_stop_seconds'] = 999999
        again, _ = exit_policy.resolve(a, Mock(side_effect=OSError()))
        other, status = exit_policy.resolve(b, Mock(side_effect=OSError()))
        self.assertEqual(again['time_stop_seconds'], 14400)
        self.assertEqual(other['time_stop_seconds'], 14400)
        self.assertEqual(status['source'], 'conservative_fallback')

    def test_degradation_warning_is_deduplicated_and_recovery_is_recorded(self):
        tracker, actions = {}, []
        with patch.object(trader, 'execution_runtime', return_value=runtime('small300')) as reader:
            trader._exit_preset(tracker, actions, 'TEST')
            reader.side_effect = ValueError('do not log details')
            trader._exit_preset(tracker, actions, 'TEST')
            trader._exit_preset(tracker, actions, 'TEST')
            self.assertEqual(len(actions), 1)
            self.assertIn('small300', actions[0])
            reader.side_effect = None
            trader._exit_preset(tracker, actions, 'TEST')
            self.assertEqual(tracker['exitPolicyStatus']['source'], 'active_profile')
            reader.side_effect = OSError()
            trader._exit_preset(tracker, actions, 'TEST')
            self.assertEqual(len(actions), 2)

    def test_atr_basis_prefers_observed_15m_and_never_invents_a_price_floor(self):
        self.assertEqual(exit_policy.volatility({'atr_15m': .1, 'atr': 5, 'price': 10000}),
                         {'value': .1, 'source': 'atr_15m'})
        for invalid in (True, 0, -1, None, float('nan'), float('inf'), 'broken'):
            self.assertEqual(exit_policy.volatility({'atr_15m': invalid, 'atr': 2})['source'], 'atr')
            self.assertEqual(exit_policy.volatility({'atr_15m': invalid, 'atr': invalid})['value'], 0)


class UnifiedProfitTests(unittest.TestCase):
    def test_short_and_long_share_preset_activation_without_tiny_R_shortcut(self):
        for preset, activation in [('standard', 2.5), ('small300', 1.8)]:
            for side, sign in [('long', 1), ('short', -1)]:
                for risk in (.1, 1.5, 10):
                    with self.subTest(preset=preset, side=side, risk=risk):
                        ex = exit_policy.thresholds(preset)
                        current = 100 + sign * (activation - .01)
                        early = profit_protection.floor_plan(side, 100, current, current, 100-sign*risk, 1, thresholds=ex)
                        self.assertFalse(early['active'])
                        self.assertFalse(early['kinetic_exit'])
                        current = 100 + sign * (activation + .01)
                        active = profit_protection.floor_plan(side, 100, current, current, 100-sign*risk, 1, thresholds=ex)
                        self.assertTrue(active['active'])
                        self.assertGreater(sign * (active['stop'] - 100), .3)

    def test_cost_gate_is_shared_by_floor_kinetic_and_ai(self):
        ex = exit_policy.thresholds('small300')
        early = profit_protection.floor_plan('long', 100, 100.1, 100.2, 99.5, .05, thresholds=ex)
        self.assertFalse(early['active'])
        self.assertFalse(early['kinetic_exit'])
        self.assertFalse(profit_protection.allow_ai_tightening('long', 100, 100.2, 100.1, .05, thresholds=ex))
        for preset in ('standard', 'small300'):
            ex = exit_policy.thresholds(preset)
            self.assertFalse(profit_protection.allow_ai_tightening('long', 100, 101.3, 100.6, 1, thresholds=ex))
            self.assertTrue(profit_protection.allow_ai_tightening('long', 100, 103, 101.5, 1, thresholds=ex))

    def test_kinetic_exit_and_tier_floor_use_the_selected_preset(self):
        for preset, peak, current in [('standard', 4., 2.6), ('small300', 3., 2.1)]:
            for side, sign in [('long', 1), ('short', -1)]:
                plan = profit_protection.floor_plan(side, 100, 100+sign*current, 100+sign*peak,
                                                     100-sign*1.5, 1, thresholds=exit_policy.thresholds(preset))
                self.assertTrue(plan['kinetic_exit'])
                self.assertFalse(plan['crossed'])
                self.assertEqual(plan['tier'], 2)


class PositionExitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.reader = self.stack.enter_context(patch.object(trader, 'execution_runtime', return_value=runtime('standard')))
        self.stack.enter_context(patch.object(trader.time, 'time', return_value=NOW))
        self.policy = self.stack.enter_context(patch.object(risk_policy, 'load_policy', return_value=risk_policy.Policy()))
        self.cloud = self.stack.enter_context(patch.object(trader, 'ensure_cloud_position_protection', return_value=(True, 'verified')))
        self.close = self.stack.enter_context(patch.object(trader, 'close_position_confirmed', return_value=(True, 'mock closed')))
        self.record = self.stack.enter_context(patch.object(trader, 'record_trade'))
        self.stack.enter_context(patch.object(trader, 'add_stop_cooldown'))
        self.stack.enter_context(patch.object(trader, 'notify_trade_close'))
        self.trackers = {}
        self.actions = []

    def cycle(self, price=101.3, *, side='long', age=0, atr=1., broad_atr=5.):
        sign = 1 if side == 'long' else -1
        key = f'{INST}_{side}'
        self.trackers.setdefault(key, {'entryTs': NOW-age, 'trailingStopPx': 100-sign*1.5,
            'exchangeStopPx': 100-sign*1.5, 'initialRiskStopPx': 100-sign*1.5,
            'takeProfitPx': 100+sign*10, 'highWaterMark': price, 'lowWaterMark': price})
        factor = {'market_data_valid': True, 'instId': INST, 'name': 'TEST', 'price': price,
                  'type': 'crypto', 'atr': broad_atr, 'atr_15m': atr, 'precision': 2, 'ctVal': 1.}
        pos = {'pos': 1., 'side': side, 'avgPx': 100., 'upl': sign*(price-100)}
        pos.update(instId=INST,posId='fixture-position',cTime=str(int((NOW-age)*1000)))
        from scripts.position_lifecycle import identity
        self.trackers[key]['positionIdentity']=identity(pos,trader.market._selected().identity)
        return trader.manage_position_tp_and_trailing(factor, pos, self.trackers, 'offline-test', self.actions)

    def test_previous_087R_early_exit_is_fixed_for_both_presets_and_sides(self):
        for preset in ('standard', 'small300'):
            for side, sign in [('long', 1), ('short', -1)]:
                with self.subTest(preset=preset, side=side):
                    self.trackers.clear(); self.close.reset_mock()
                    self.reader.return_value = runtime(preset)
                    self.assertFalse(self.cycle(100+sign*1.3, side=side)[0])
                    self.assertFalse(self.cycle(100+sign*.5, side=side)[0])
                    self.close.assert_not_called()
                    self.assertEqual(self.trackers[f'{INST}_{side}']['trailingStopPx'], 100-sign*1.5)

    def test_selected_activation_tightens_cloud_argument_and_preserves_stop_on_switch(self):
        self.reader.return_value = runtime('small300')
        self.cycle(101.9)
        key = f'{INST}_long'
        stop = self.trackers[key]['trailingStopPx']
        self.assertGreater(stop, 100.3)
        self.assertEqual(self.cloud.call_args.args[-1], stop)
        self.reader.return_value = runtime('standard')
        self.cycle(101.7)
        self.assertGreaterEqual(self.trackers[key]['trailingStopPx'], stop)
        self.close.assert_not_called()

    def test_existing_tighter_stops_are_enforced_even_below_new_activation(self):
        for side, sign in [('long', 1), ('short', -1)]:
            self.trackers.clear(); self.close.reset_mock()
            self.cycle(100+sign*1.3, side=side)
            self.trackers[f'{INST}_{side}']['trailingStopPx'] = 100+sign*.8
            self.assertTrue(self.cycle(100+sign*.5, side=side)[0])
            self.assertEqual(self.close.call_args.kwargs['exit_reason'], 'hard_stop')

    def test_last_verified_small_preset_still_exits_after_read_failure_and_restart(self):
        self.reader.return_value = runtime('small300')
        self.cycle(100.2, age=5*3600)
        self.trackers = json.loads(json.dumps(self.trackers))
        self.reader.side_effect = ValueError('bad profile')
        self.assertTrue(self.cycle(99.9)[0])
        self.assertEqual(self.close.call_args.kwargs['exit_reason'], 'time_exit')
        evidence = self.record.call_args.args[0]['exit_evidence']
        self.assertEqual(evidence['threshold_seconds'], 14400)
        self.assertEqual(evidence['policy']['source'], 'last_verified')

    def test_unreadable_preset_never_disables_hard_stop_or_cloud_fail_closed(self):
        self.reader.side_effect = ValueError('broken')
        self.assertTrue(self.cycle(98.)[0])
        self.assertEqual(self.close.call_args.kwargs['exit_reason'], 'hard_stop')
        self.trackers.clear()
        self.cloud.return_value = (False, 'UNKNOWN: unavailable')
        self.assertTrue(self.cycle(100.)[0])
        self.assertEqual(self.close.call_args.kwargs['exit_reason'], 'oco_unverified')

    def test_fresh_unknown_preset_cannot_silently_wait_six_hours(self):
        self.reader.side_effect = ValueError('broken')
        self.assertTrue(self.cycle(99.9, age=4*3600+60)[0])
        evidence = self.record.call_args.args[0]['exit_evidence']
        self.assertEqual(evidence['policy']['source'], 'conservative_fallback')
        self.assertEqual(evidence['threshold_seconds'], 14400)

    def test_time_log_and_ledger_explain_actual_signed_profit_and_threshold(self):
        for preset, hours in [('standard', 6), ('small300', 4)]:
            for side, sign in [('long', 1), ('short', -1)]:
                self.trackers.clear(); self.actions.clear()
                self.reader.return_value = runtime(preset)
                self.assertTrue(self.cycle(100-sign*.2, side=side, age=hours*3600+60)[0])
                row = self.record.call_args.args[0]
                reason = row['remark']; evidence = row['exit_evidence']
                self.assertIn(reason, self.actions[-1])
                self.assertIn(f'> {hours}h', reason)
                self.assertIn(preset, reason)
                self.assertNotIn('无波动', reason)
                self.assertEqual(evidence['hold_seconds'], hours*3600+60)
                self.assertAlmostEqual(evidence['profit_atr'], -.2)
                self.assertEqual(evidence['atr']['source'], 'atr_15m')

    def test_time_boundary_and_small_gain_condition_are_explicit(self):
        self.assertFalse(self.cycle(99.9, age=21600)[0])
        self.trackers.clear()
        self.assertFalse(self.cycle(100.2, age=21601)[0])
        self.trackers.clear()
        self.assertTrue(self.cycle(100.1, age=21601)[0])
        self.assertGreater(self.record.call_args.args[0]['exit_evidence']['profit_atr'], 0)

    def test_failed_exit_retains_tracker_and_exact_attempt_evidence(self):
        self.close.return_value = (False, 'unknown receipt')
        self.reader.return_value = runtime('small300')
        self.assertFalse(self.cycle(99.9, age=14401)[0])
        self.record.assert_not_called()
        self.assertEqual(self.trackers[f'{INST}_long']['lastExitAttempt']['threshold_seconds'], 14400)
        self.close.assert_called_once()

    def test_cost_policy_failure_preserves_existing_stop_without_kinetic_bypass(self):
        self.policy.side_effect = ValueError('unavailable')
        self.cycle(104.)
        self.cycle(102.6)
        self.close.assert_not_called()
        self.assertEqual(self.trackers[f'{INST}_long']['trailingStopPx'], 98.5)

    def test_real_manager_kinetic_exit_uses_same_atr_as_floor(self):
        for side, sign in [('long', 1), ('short', -1)]:
            self.trackers.clear(); self.close.reset_mock()
            self.cycle(100+sign*4, side=side)
            self.assertTrue(self.cycle(100+sign*2.6, side=side)[0])
            self.assertEqual(self.close.call_args.kwargs['exit_reason'], 'trailing_exit')
            self.assertEqual(self.record.call_args.args[0]['exit_evidence']['atr']['value'], 1.)


if __name__ == '__main__':
    unittest.main()
