"""A01: currency/allocation semantics, environment isolation and risk invariants.

Run only via scripts/run_tests.py: source snapshots contain no runtime data.
"""
from contextlib import ExitStack
from dataclasses import asdict, replace
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from scripts import capital_pool as pool, execution_profiles as profiles
from scripts import execution_leverage as leverage, execution_costs, demo_scalp_policy
from scripts import risk_policy as risk, strategy_evidence as evidence, trade_lock, entry_gateway
from scripts.okx_runtime import OKXEnvironment

META = {'instId': 'TEST-USDT-SWAP', 'ctType': 'linear', 'settleCcy': 'USDT',
        'state': 'live', 'ctVal': '1', 'ctMult': '1', 'lotSz': '.001',
        'minSz': '.001', 'tickSz': '.01'}


def observation(equity=300, at=1000, flow=0):
    return {'equity': equity, 'equity_currency': 'USDT', 'at': at,
            'external_flow_total': flow, 'external_flow_origin': 1000}


def balance(usdt=300, usd=300):
    return {'totalEq': str(usd), 'details': [
        {'ccy': 'USDT', 'eq': str(usdt), 'availEq': str(usdt), 'liab': '0'}]}


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for module, name, value in (
            (evidence, 'DB_PATH', self.root / 'evidence.db'),
            (pool, 'CONFIG_FILE', self.root / 'pool.json'),
            (trade_lock, 'PATH', self.root / 'writer.lock'),
        ):
            self.stack.enter_context(patch.object(module, name, value))
        self.env = OKXEnvironment('demo', 'a01-fake', 'fake', 'fake')
        self.policy = risk.Policy(**{k: v for k, v in profiles.SMALL_300.items()
                                   if k in risk.Policy.__dataclass_fields__})

    def small(self, usdt=300, usd=300, at=1000, flow=0, held=(), pending=(),
              allocation=None, env=None):
        allocation = allocation or pool.Budget(False, usd, usdt, self.policy, {}, 'sig')
        return profiles.cap_allocation(
            allocation, profiles.SMALL_300, list(held), list(pending),
            {META['instId']: META}, META['instId'], lambda *_: 3,
            env=env or self.env, observation=observation(usd, at, flow),
            balance=balance(usdt, usd))

    def test_small300_risk_equity_is_usdt_not_converted_usd_minimum(self):
        for usd in (270, 330):
            with self.subTest(usd=usd):
                result = self.small(usd=usd)
                self.assertEqual(result.equity, 300)
                self.assertEqual(result.detail['execution_allocation']['risk_equity'], 300)

    def test_small300_never_synthesizes_300_from_a_smaller_usdt_account(self):
        result = self.small(usdt=250, usd=400)
        self.assertEqual(result.equity, 250)
        self.assertEqual(result.available, 250)

    def test_small300_drawdown_and_deposits_do_not_reset_loss(self):
        self.small()
        with self.assertRaisesRegex(risk.RiskRejected, 'drawdown'):
            self.small(usdt=275, usd=275, at=1001)
        with self.assertRaisesRegex(risk.RiskRejected, 'drawdown'):
            self.small(usdt=375, usd=375, at=1002, flow=100)
        state = pool._state(self.env.identity + ':execution:small300')
        self.assertEqual(state['pool_nav'], 275)
        self.assertTrue(state['drawdown']['blocked'])

    def test_small300_is_an_enabled_virtual_allocation(self):
        result = self.small()
        self.assertTrue(result.enabled)
        self.assertEqual(result.available, 270)
        self.assertTrue(result.detail['execution_allocation']['virtual_cap'])
        standard = profiles.cap_allocation(result, {'id': 'standard'}, [], [], {},
                                            META['instId'], lambda *_: 3)
        self.assertIs(standard, result)

    def test_nested_pool_caps_intersect_without_second_loss_deduction(self):
        nested = pool.Budget(True, 200, 80, self.policy, {'pool_nav': 200}, 'sig')
        result = self.small(allocation=nested)
        self.assertEqual((result.equity, result.available), (200, 80))
        self.assertEqual(result.detail['pool_nav'], 200)

    def test_available_balance_is_not_reduced_by_exchange_reservations_twice(self):
        self.small()
        held = [{'instId': META['instId'], 'pos': '1', 'imr': '60', 'margin': '0'}]
        pending = [{'instId': META['instId'], 'sz': '2', 'accFillSz': '1',
                    'px': '100', 'lever': '5'}]
        source = copy.deepcopy((held, pending))
        allocation = pool.Budget(False, 300, 100, self.policy, {}, 'sig')
        result = self.small(held=held, pending=pending, allocation=allocation)
        self.assertEqual(pool.reserved_margin(held, pending, {META['instId']: META}, Mock()), 80)
        self.assertEqual(result.available, min(100, 270 - 80))
        self.assertEqual((held, pending), source)

    def test_loss_and_deposit_survive_profile_round_trip(self):
        self.small(usdt=5000, usd=5000)
        down = self.small(usdt=4997, usd=4997, at=1001)
        self.assertEqual(down.equity, 297)
        # Standard bypass does not erase or initialize the small-account state.
        standard = pool.Budget(False, 4997, 4997, self.policy, {}, 'sig')
        profiles.cap_allocation(standard, {'id': 'standard'}, [], [], {}, '', Mock())
        back = self.small(usdt=5097, usd=5097, at=1002, flow=100)
        self.assertEqual(back.equity, 297)

    def test_first_activation_with_existing_position_does_not_adopt_or_resize_it(self):
        held = [{'instId': META['instId'], 'pos': '1', 'imr': '20'}]
        original = copy.deepcopy(held)
        with self.assertRaisesRegex(risk.RiskRejected, 'initialization'):
            self.small(held=held)
        self.assertEqual(held, original)

    def test_demo_to_live_initializes_separate_small_allocation(self):
        self.small(usdt=5000, usd=5000)
        self.small(usdt=4997, usd=4997, at=1001)
        live = OKXEnvironment('live', 'a01-fake', 'fake', 'fake')
        self.assertEqual(self.small(env=live).equity, 300)
        saved = pool._state(self.env.identity + ':execution:small300')
        self.assertEqual(saved['risk_equity'], 297)

    def test_explicit_live_pool_does_not_migrate_demo_losses(self):
        cfg = pool.Config(enabled=True, dedicated_account_confirmed=True,
                          virtual_cap_acknowledged=True)
        old = pool.advance(None, cfg, self.env.identity, observation(),
                           flat=True, policy=self.policy)
        pool._save(old)
        live_cfg = replace(cfg, environment='live', allow_live=True)
        pool.CONFIG_FILE.write_text(json.dumps(asdict(live_cfg)), encoding='utf8')
        live = OKXEnvironment('live', 'a01-fake', 'fake', 'fake')
        result = pool.initialize_flat(live, observation(), balance(), [], [], self.policy)
        self.assertEqual(result['risk_equity'], 300)
        self.assertEqual(pool._state(self.env.identity)['risk_equity'], 300)

    def test_legacy_canonical_demo_scope_is_not_a_live_migration(self):
        cfg = pool.Config()
        old = pool.advance(None, cfg, self.env.identity, observation(),
                           flat=True, policy=self.policy)
        old.pop('environment', None)
        pool._save(old)
        live = OKXEnvironment('live', 'a01-fake', 'fake', 'fake')
        pool.assert_new_scope(live.identity, replace(cfg, environment='live'))

    def test_legacy_unknown_environment_still_requires_review(self):
        cfg = pool.Config()
        old = pool.advance(None, cfg, 'legacy-custom-scope', observation(),
                           flat=True, policy=self.policy)
        old.pop('environment', None)
        pool._save(old)
        live = OKXEnvironment('live', 'a01-fake', 'fake', 'fake')
        with self.assertRaisesRegex(risk.RiskRejected, 'migration'):
            pool.assert_new_scope(live.identity, replace(cfg, environment='live'))

    def test_new_explicit_environment_supports_noncanonical_scopes(self):
        demo = replace(self.env, account_scope='custom-demo-scope')
        live = OKXEnvironment('live', 'a01-fake', 'fake', 'fake', account_scope='custom-live-scope')
        self.small(env=demo)
        self.assertEqual(self.small(env=live).equity, 300)

    def test_same_environment_key_rotation_still_requires_review(self):
        self.small()
        changed = OKXEnvironment('demo', 'a01-another-fake', 'fake', 'fake')
        with self.assertRaisesRegex(risk.RiskRejected, 'migration'):
            self.small(env=changed)

    def test_live_still_needs_explicit_optional_pool_opt_in(self):
        with self.assertRaises(risk.RiskRejected):
            pool.Config(enabled=True, environment='live', dedicated_account_confirmed=True,
                        virtual_cap_acknowledged=True)

    def gateway(self, change_pending=False):
        now = time.time()
        binding = profiles.runtime({'id': 'small300'})
        decision = {'action': 'BUY_LONG', 'contract_version': 'trading-evidence-v1',
                    'contract_valid': True, 'valid_until': now + 120}
        did = evidence.append(self.env.identity, 'decision', {
            'instrument': META['instId'], 'features': {'structure_1h': '1H_SWING_BULL'},
            'decision': decision, 'execution_profile_signature': binding['signature']})
        reads = []

        def private(method, path, params, env):
            self.assertEqual(method, 'GET')
            if path.endswith('/positions'):
                return []
            if path.endswith('/orders-pending'):
                reads.append(path)
                if change_pending and len(reads) > 1:
                    return [{'ordId': 'new-entry', 'instId': 'OTHER-USDT-SWAP',
                             'posSide': 'long', 'sz': '1', 'accFillSz': '0', 'px': '100'}]
                return []
            if path.endswith('/balance'):
                return [{**balance(), 'uTime': str(int(now * 1000))}]
            if path.endswith('/leverage-info'):
                return [{'posSide': 'long', 'lever': '3'}]
            raise AssertionError(path)

        def public(url, **kwargs):
            return {'data': [META] if '/instruments?' in url else [
                {'last': '100', 'ts': str(int(now * 1000))}]}

        with patch.object(profiles, 'runtime', return_value=binding), \
             patch.object(risk, 'load_policy', return_value=self.policy), \
             patch.object(risk, 'ledger_daily_drawdown', return_value={'blocked': False}), \
             patch.object(entry_gateway, '_request', side_effect=private), \
             patch.object(entry_gateway.public_market, 'get_json', side_effect=public):
            result = entry_gateway.prepare(self.env, inst_id=META['instId'], side='long',
                entry=100, stop=98, take_profit=110, requested_size=1000, budget=15,
                decision_id=did, decision_at=now)
        return result, reads

    def test_small300_gateway_records_virtual_budget_and_rechecks_pending(self):
        (plan, client), reads = self.gateway()
        self.assertEqual(len(reads), 2)
        self.assertEqual(plan['capital_pool']['execution_allocation']['risk_equity'], 300)
        self.assertEqual(evidence.unresolved(self.env.identity)[0][0], client)

    def test_small300_gateway_uses_existing_reservation_race_protection(self):
        with self.assertRaisesRegex(risk.RiskRejected, 'Pending reservations changed'):
            self.gateway(change_pending=True)
        self.assertEqual(evidence.unresolved(self.env.identity), [])


class ProfileBindingTests(unittest.TestCase):
    def test_explicit_custom_binding_wins_over_legacy_environment(self):
        for value in ('300', 'small300', 'small'):
            with self.subTest(value=value), patch.dict(os.environ, {'OKXQUANT_CAPITAL_MODE': value}):
                self.assertEqual(profiles.settings_for({'id': 'custom', 'execution_profile': 'standard'})['id'], 'standard')
                self.assertEqual(profiles.settings_for({'id': 'custom', 'execution_profile': 'small300'})['id'], 'small300')
                self.assertEqual(profiles.settings_for({'id': 'legacy'})['id'], 'small300')

    def test_signature_tracks_binding_and_ignores_shadowed_environment(self):
        standard = {'id': 'custom', 'execution_profile': 'standard'}
        with patch.dict(os.environ, {'OKXQUANT_CAPITAL_MODE': ''}):
            before = profiles.runtime(standard)
        with patch.dict(os.environ, {'OKXQUANT_CAPITAL_MODE': 'small300'}):
            self.assertEqual(profiles.runtime(standard), before)
            changed = profiles.runtime({**standard, 'execution_profile': 'small300'})
        self.assertNotEqual(changed['signature'], before['signature'])
        self.assertEqual(changed['profile_id'], before['profile_id'])

    def test_small300_uses_the_same_daily_three_percent_circuit(self):
        self.assertEqual(profiles.SMALL_300['daily_drawdown_pct'], .03)

    def test_builtin_small300_and_unknown_binding_contract_are_preserved(self):
        self.assertEqual(profiles.settings_for({'id': 'small300', 'execution_profile': 'standard'})['id'], 'small300')
        with patch.dict(os.environ, {'OKXQUANT_CAPITAL_MODE': 'small300'}):
            with self.assertRaisesRegex(ValueError, 'Unknown execution preset'):
                profiles.settings_for({'id': 'custom', 'execution_profile': 'typo'})


class RiskInvariantTests(unittest.TestCase):
    def test_zero_remaining_entry_has_no_stop_or_metadata_requirement(self):
        completed = {'instId': 'OLD-USDT-SWAP', 'sz': '2', 'accFillSz': '2'}
        result = risk.exposure([], [completed], [], {})
        self.assertEqual(result['total'], 0)
        self.assertEqual(pool.entry_orders([completed]), [])

    def test_partially_filled_entry_reserves_only_remainder_and_still_needs_stop(self):
        pending = {'instId': META['instId'], 'posSide': 'long', 'sz': '3',
                   'accFillSz': '2', 'px': '100', 'slTriggerPx': '98'}
        result = risk.exposure([], [pending], [], {META['instId']: META})
        self.assertAlmostEqual(result['total'], 2 + execution_costs.reference_at_entry(100, risk.Policy()))
        pending.pop('slTriggerPx')
        with self.assertRaisesRegex(risk.RiskRejected, 'stop unavailable'):
            risk.exposure([], [pending], [], {META['instId']: META})

    def test_leverage_changes_margin_not_stop_risk_budget_or_costs(self):
        args = dict(metadata=META, side='long', entry=100, stop=98, take_profit=110,
                    requested_size=1000, budget_usdt=1.2, equity=300, available=270)
        low = risk.order_plan(leverage=3, **args)
        high = risk.order_plan(leverage=5, **args)
        self.assertEqual(low['size'], high['size'])
        self.assertEqual(low['risk_usdt'], high['risk_usdt'])
        self.assertEqual(low['cost_model'], high['cost_model'])
        self.assertAlmostEqual(low['margin_usdt'] * 3, high['margin_usdt'] * 5)

    def test_portfolio_constraints_are_intersection_not_added_deductions(self):
        policy = risk.Policy(per_trade_equity_pct=.02)
        plan = risk.order_plan(metadata=META, side='long', entry=100, stop=98,
            take_profit=110, requested_size=1000, budget_usdt=6, equity=300,
            available=270, leverage=3, policy=policy,
            portfolio={'total': 5, 'long': 3, 'group': 4})
        self.assertEqual(plan['risk_budget_usdt'], min(6, 9 - 5, 6 - 3, 6 - 4))
        self.assertLessEqual(plan['risk_usdt'], 2)

    def test_demo_and_live_leverage_semantics_and_sampling_remain_distinct(self):
        demo = OKXEnvironment('demo', 'fake', 'fake', 'fake')
        live = OKXEnvironment('live', 'fake', 'fake', 'fake')
        base = risk.Policy()
        demo_policy = leverage.mode_policy(base, 'scalp', demo)
        live_policy = leverage.mode_policy(base, 'scalp', live)
        self.assertEqual(demo_policy.max_leverage, 20)
        self.assertEqual(live_policy.max_leverage, 5)
        self.assertEqual(leverage.choose(demo_policy, 'scalp', demo, 3, [], {}), 10)
        self.assertEqual(leverage.choose(live_policy, 'scalp', live, 3, [], {'leverage': 20}), 3)
        self.assertEqual(leverage.choose(demo_policy, 'scalp', demo, 3, [{'pos': '1'}], {}), 3)
        request = Mock()
        self.assertEqual(leverage.apply(live, META['instId'], 'long', 3, 3, 'id', request), 3)
        with self.assertRaises(risk.RiskRejected):
            leverage.apply(live, META['instId'], 'long', 10, 3, 'id', request)
        request.assert_not_called()
        with self.assertRaises(risk.RiskRejected):
            demo_scalp_policy.execution_policy(base, live)
        sample = demo_scalp_policy.execution_policy(base, demo)
        self.assertEqual(sample.minimum_net_rr, 1.2)
        self.assertEqual(replace(sample, minimum_net_rr=base.minimum_net_rr), base)

    def test_small300_mode_intersection_matches_existing_scoped_limits(self):
        base = risk.Policy(**{k: v for k, v in profiles.SMALL_300.items()
                             if k in risk.Policy.__dataclass_fields__})
        demo = OKXEnvironment('demo', 'fake', 'fake', 'fake')
        for horizon, expected_risk, expected_leverage in (
            ('scalp', .004, 20), ('swing', .005, 5),
        ):
            with self.subTest(horizon=horizon):
                effective = leverage.mode_policy(base, horizon, demo)
                self.assertEqual(effective.per_trade_equity_pct, expected_risk)
                self.assertEqual(effective.max_leverage, expected_leverage)
                self.assertEqual(effective.single_asset_margin_usdt, 150)
                self.assertEqual(effective.daily_drawdown_pct, .03)
        # These are ceilings, not forced leverage/order-size targets.
        live = replace(demo, mode='live')
        self.assertEqual(leverage.mode_policy(base, 'scalp', live).max_leverage, 6)

    def test_cost_reference_explicitly_is_not_a_fill_promise(self):
        cost = execution_costs.from_policy(100, 110, risk.Policy())
        self.assertIn('not_fill_promise', cost['admission_basis'])
        self.assertGreater(cost['taker_taker_total'], cost['maker_taker_total'])
        self.assertFalse(cost['funding_included'])


if __name__ == '__main__':
    unittest.main()
