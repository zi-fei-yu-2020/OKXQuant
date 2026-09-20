"""Authorized A01 handoff: observe only existing small300 allocations in guard."""
import copy
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts import capital_pool as pool, execution_profiles as profiles
from scripts import strategy_evidence as evidence, risk_policy as risk, position_guard, entry_gateway


def observation(equity=300, at=1000, flow=0):
    return {'equity': equity, 'at': at, 'equity_currency': 'USDT',
            'external_flow_total': flow, 'external_flow_origin': 1000}


def balance(equity=300, at=1000):
    return {'totalEq': str(equity), 'uTime': str(at*1000),
            'details': [{'ccy': 'USDT', 'eq': str(equity), 'liab': '0'}]}


class ExistingSmall300ObservationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(evidence, 'DB_PATH', self.root/'e.db'))
        self.stack.enter_context(patch.object(pool, 'CONFIG_FILE', self.root/'pool.json'))
        self.env = SimpleNamespace(identity='audit-demo', mode='demo', simulated=True, configured=True)
        self.policy = risk.Policy(**{key: value for key, value in profiles.SMALL_300.items()
                                    if key in risk.Policy.__dataclass_fields__})
        self.scope = self.env.identity + ':execution:small300'

    def seed(self):
        allocation = pool.Budget(False, 300, 300, self.policy, {}, 'offline')
        profiles.cap_allocation(allocation, profiles.SMALL_300, [], [], {}, 'BTC-USDT-SWAP',
                                lambda *_: 3, env=self.env, observation=observation(), balance=balance())
        return pool._state(self.scope)

    def observe(self, equity=300, at=1001, flow=0):
        return profiles.observe_existing(self.env, observation(equity, at, flow), risk.Policy(),
                                         balance=balance(equity, at))

    def test_missing_scope_never_creates_allocation_even_with_unknown_observation(self):
        with patch.object(pool, 'advance') as advance, patch.object(pool, '_save') as save:
            self.assertIsNone(profiles.observe_existing(self.env, None, self.policy, balance={}))
        advance.assert_not_called(); save.assert_not_called()
        self.assertFalse(evidence.DB_PATH.exists())

    def test_peak_day_boundary_and_deposit_are_continuously_observed_without_entry(self):
        self.seed()
        with patch.object(profiles, 'runtime', side_effect=ValueError('profile not needed')):
            peak = self.observe(330, 1001)
            tomorrow = self.observe(320, 90000)
            deposit = self.observe(420, 90001, 100)
        self.assertEqual(peak['drawdown']['peak'], 330)
        self.assertEqual(tomorrow['drawdown']['peak'], 330)
        self.assertNotEqual(peak['drawdown']['day'], tomorrow['drawdown']['day'])
        self.assertEqual(tomorrow['drawdown']['day_anchor'], 320)
        self.assertEqual(deposit['pool_nav'], 320)
        self.assertEqual(deposit['drawdown']['peak'], 330)
        self.assertEqual(deposit['initial_budget'], 300)
        self.assertEqual(pool._state(self.scope)['at'], 90001)

    def test_same_existing_small300_drawdown_thresholds_and_no_new_gate(self):
        self.seed()
        result = self.observe(270, 1001)
        self.assertTrue(result['drawdown']['blocked'])
        self.assertEqual(result['pool_nav'], 270)
        # Observation records the existing gate; it does not submit/close or raise it.
        self.assertEqual(pool._state(self.scope)['drawdown'], result['drawdown'])

    def test_repeated_balance_does_not_duplicate_observation_evidence(self):
        self.seed(); self.observe(330, 1001)
        count = len(evidence.export_events(self.scope, 'capital_pool_observation'))
        self.observe(330, 1001)
        self.assertEqual(len(evidence.export_events(self.scope, 'capital_pool_observation')), count)

    def test_account_and_environment_switch_cannot_copy_a_baseline(self):
        self.seed()
        other = SimpleNamespace(identity='audit-live', mode='live')
        self.assertIsNone(profiles.observe_existing(other, observation(), self.policy, balance=balance()))
        self.assertIsNone(pool._state(other.identity + ':execution:small300'))

    def test_failed_flow_reconciliation_cannot_advance_from_stale_account_state(self):
        self.seed()
        with evidence.connection() as db:
            db.execute('INSERT INTO equity_state VALUES (?,?)', (self.env.identity, evidence.canonical(observation())))
        before = pool._state(self.scope)
        with self.assertRaises(risk.RiskRejected):
            profiles.observe_existing(self.env, None, self.policy, balance=balance(310, 2000))
        self.assertEqual(pool._state(self.scope), before)

    def test_guard_recovers_exact_committed_observation_after_account_gate_raises(self):
        self.seed()
        observed = observation(270, 2000)
        def account_guard(*args):
            with evidence.connection() as db:
                db.execute('INSERT INTO equity_state VALUES (?,?)', (self.env.identity, evidence.canonical(observed)))
            raise risk.RiskRejected('existing account gate')
        with patch('okxquant_backend.okx_trade_service._request', return_value=[balance(270, 2000)]), patch.object(risk, 'load_policy', return_value=self.policy), patch.object(entry_gateway, 'equity_guard', side_effect=account_guard):
            with self.assertRaises(risk.RiskRejected):
                position_guard.observe_equity(self.env, [{'pos': '1'}])
        self.assertEqual(pool._state(self.scope)['pool_nav'], 270)
        self.assertEqual(pool._state(self.scope)['at'], 2000)

    def test_guard_observation_error_does_not_disable_position_protection(self):
        import ai_factor_trader as trader
        import okx_runtime
        from scripts import algo_reader
        held = {'instId': 'BTC-USDT-SWAP', 'posSide': 'long', 'pos': '1', 'avgPx': '100', 'markPx': '100'}
        with ExitStack() as stack:
            for target, value in ((okx_runtime, 'freeze_environment'),):
                stack.enter_context(patch.object(target, value, return_value=self.env))
            stack.enter_context(patch.object(okx_runtime, 'unfreeze_environment'))
            stack.enter_context(patch.object(position_guard, 'writer', return_value=nullcontext()))
            stack.enter_context(patch.object(position_guard, 'read_positions', return_value=[held]))
            stack.enter_context(patch('okxquant_backend.okx_trade_service._request', return_value=[balance()]))
            stack.enter_context(patch.object(risk, 'load_policy', return_value=self.policy))
            stack.enter_context(patch.object(entry_gateway, 'equity_guard', return_value=observation()))
            stack.enter_context(patch.object(profiles, 'observe_existing', side_effect=RuntimeError('offline failure')))
            stack.enter_context(patch.object(trader, 'load_trackers', return_value={}))
            stack.enter_context(patch.object(trader, 'save_trackers'))
            stack.enter_context(patch.object(trader, 'add_stop_cooldown'))
            stack.enter_context(patch.object(trader.market, 'get_json', side_effect=RuntimeError('no metadata')))
            stack.enter_context(patch.object(algo_reader, 'read_algo_orders', side_effect=RuntimeError('unknown protection')))
            close = stack.enter_context(patch.object(trader, 'close_position_confirmed', return_value=(True, 'confirmed')))
            submit = stack.enter_context(patch.object(trader, 'submit_protected_limit_order'))
            result = position_guard.run_guard()
        close.assert_called_once(); submit.assert_not_called()
        self.assertEqual(result['positions'], 1)
        self.assertTrue(evidence.export_events(self.env.identity, 'execution_allocation_observation_failed'))
