"""Bound LIVE minute consent: isolated storage, no real exchange operations."""
from contextlib import ExitStack
from dataclasses import replace
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from scripts import strategy_engine_runtime as engine, demo_scalp, demo_scalp_policy
from scripts import execution_leverage as leverage, risk_policy as risk
from scripts import prompt_library, capital_pool, strategy_evidence as evidence, trade_lock
from scripts.okx_runtime import OKXEnvironment

INST = 'BTC-USDT-SWAP'


class LiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for module, key, name in (
            (engine, 'STATE_FILE', 'live.json'), (demo_scalp, 'CONFIG', 'demo.json'),
            (prompt_library, 'LIBRARY_FILE', 'profiles.json'),
            (capital_pool, 'CONFIG_FILE', 'pool.json'),
            (evidence, 'DB_PATH', 'e.db'), (trade_lock, 'PATH', 'writer.lock'),
        ):
            self.stack.enter_context(patch.object(module, key, self.root / name))
        self.current = self.stack.enter_context(patch('okxquant_backend.account_connections.assert_current'))
        self.autotrade = self.stack.enter_context(patch.object(engine, '_autotrade_enabled', return_value=True))
        self.base = risk.Policy(max_leverage=6, scalp_max_leverage=20, per_trade_equity_pct=.02)
        self.policy = self.stack.enter_context(patch.object(risk, 'load_policy', return_value=self.base))
        self.live = OKXEnvironment('live', 'dummy-api', 'dummy-secret', 'dummy-pass',
            connection_id='conn-1', binding_version=7, account_scope='okx:live:account:test')
        self.demo = replace(self.live, mode='demo', account_scope='okx:demo:account:test')

    def authorize(self, env=None):
        return engine.authorize_live(env or self.live, 'admin:test')

    def durable_decision(self, binding=None):
        binding = binding or engine.status(self.live)
        return evidence.append(self.live.identity, 'decision', {
            'instrument': INST, 'features': {'strategy_engine': engine.ENGINE_ID},
            'execution_profile_signature': binding['execution_signature'],
            'decision': {'horizon': 'scalp', 'strategy_engine': engine.ENGINE_ID,
                         'engine_binding': {k: binding[k] for k in (*engine.BINDING_FIELDS, 'record_id')}}})

    def test_default_live_is_off_even_if_legacy_demo_flag_is_enabled(self):
        demo_scalp.CONFIG.write_text(json.dumps({'enabled': True, 'version': engine.ENGINE_VERSION}))
        self.assertTrue(demo_scalp.enabled(self.demo))
        self.assertFalse(engine.enabled(self.live))
        self.assertFalse(demo_scalp.enabled(self.live))
        self.assertFalse(engine.STATE_FILE.exists())

    def test_legacy_demo_flag_keeps_exact_enabled_and_version_contract(self):
        for cfg, expected in (({'enabled': True, 'version': engine.ENGINE_VERSION}, True),
                              ({'enabled': 'true', 'version': engine.ENGINE_VERSION}, False),
                              ({'enabled': True, 'version': 'other'}, False), ([], False)):
            with self.subTest(cfg=cfg):
                demo_scalp.CONFIG.write_text(json.dumps(cfg))
                self.assertEqual(engine.enabled(self.demo), expected)
        self.current.assert_not_called()

    def test_demo_status_never_depends_on_live_consent_storage(self):
        demo_scalp.CONFIG.write_text(json.dumps({'enabled': True, 'version': engine.ENGINE_VERSION}))
        engine.STATE_FILE.write_text('corrupt')
        with patch.object(engine, '_load', side_effect=AssertionError('demo must not load live state')):
            self.assertTrue(engine.enabled(self.demo))

    def test_status_is_read_only_without_state_or_after_authorizing(self):
        with patch.object(engine, '_save', side_effect=AssertionError('read-only')):
            self.assertFalse(engine.status(self.live)['enabled'])
        self.authorize()
        before = engine.STATE_FILE.read_bytes()
        modified = engine.STATE_FILE.stat().st_mtime_ns
        for _ in range(3):
            self.assertTrue(engine.status(self.live)['enabled'])
        self.assertEqual(engine.STATE_FILE.read_bytes(), before)
        self.assertEqual(engine.STATE_FILE.stat().st_mtime_ns, modified)

    def test_confirmation_persists_binding_only_not_credentials(self):
        result = self.authorize()
        self.assertTrue(result['authorized']); self.assertTrue(result['enabled'])
        raw = engine.STATE_FILE.read_text()
        for private in ('dummy-api', 'dummy-secret', 'dummy-pass'):
            self.assertNotIn(private, raw)
        record = json.loads(raw)['live'][self.live.identity]
        self.assertTrue(record['confirmed'])
        self.assertEqual(record['actor'], 'admin:test')
        for key in engine.BINDING_FIELDS:
            self.assertEqual(record[key], result[key])
        self.assertEqual(engine.STATE_FILE.stat().st_mode & 0o777, 0o600)

    def test_authorize_requires_current_managed_live_and_server_actor(self):
        for env in (self.demo, replace(self.live, connection_id=''),
                    replace(self.live, binding_version=0), replace(self.live, binding_version=True)):
            with self.subTest(env=env.mode), self.assertRaises(risk.RiskRejected):
                self.authorize(env)
        for actor in ('', ' ', None, True, {'admin': True}):
            with self.subTest(actor=actor), self.assertRaises(risk.RiskRejected):
                engine.authorize_live(self.live, actor)
        self.assertFalse(engine.STATE_FILE.exists())

    def test_noncurrent_live_slot_cannot_be_pre_authorized(self):
        self.current.side_effect = ValueError('binding is not current')
        with self.assertRaises(ValueError):
            self.authorize()
        self.assertFalse(engine.enabled(self.live))
        self.assertFalse(engine.STATE_FILE.exists())

    def test_scope_connection_and_generation_changes_invalidate_consent(self):
        self.authorize()
        for env in (replace(self.live, account_scope='other'), replace(self.live, connection_id='c2'),
                    replace(self.live, binding_version=8)):
            with self.subTest(env=env.identity):
                self.assertFalse(engine.enabled(env))
        self.assertTrue(engine.enabled(self.live))

    def test_profile_content_edit_invalidates_consent_without_execution_change(self):
        before = self.authorize()
        changed = {**prompt_library.active_profile(), 'name': 'Changed preference profile'}
        with patch.object(prompt_library, 'active_profile', return_value=changed):
            after = engine.status(self.live)
        self.assertEqual(after['execution_signature'], before['execution_signature'])
        self.assertNotEqual(after['profile_signature'], before['profile_signature'])
        self.assertFalse(after['enabled'])

    def test_execution_binding_edit_invalidates_consent(self):
        before = self.authorize()
        changed = {**prompt_library.active_profile(), 'execution_profile': 'small300'}
        with patch.object(prompt_library, 'active_profile', return_value=changed):
            after = engine.status(self.live)
        self.assertNotEqual(after['execution_signature'], before['execution_signature'])
        self.assertFalse(after['enabled'])

    def test_policy_or_optional_pool_edit_invalidates_consent(self):
        before = self.authorize()
        self.policy.return_value = replace(self.base, scalp_max_leverage=12)
        changed = engine.status(self.live)
        self.assertNotEqual(changed['policy_signature'], before['policy_signature'])
        self.assertFalse(changed['enabled'])
        self.policy.return_value = self.base
        capital_pool.CONFIG_FILE.write_text(json.dumps({'budget_usdt': 250}))
        self.assertFalse(engine.enabled(self.live))

    def test_global_pause_does_not_erase_confirmation_or_enable_other_accounts(self):
        self.authorize()
        self.autotrade.return_value = False
        paused = engine.status(self.live)
        self.assertEqual(paused['status'], 'paused')
        self.assertTrue(paused['authorized']); self.assertFalse(paused['enabled'])
        self.autotrade.return_value = True
        self.assertTrue(engine.enabled(self.live))

    def test_disable_only_current_live_scope_preserves_demo_and_other_scope(self):
        demo_scalp.CONFIG.write_text(json.dumps({'enabled': True, 'version': engine.ENGINE_VERSION}))
        self.authorize()
        other = replace(self.live, connection_id='conn-2', account_scope='okx:live:account:other')
        self.authorize(other)
        before_demo = demo_scalp.CONFIG.read_bytes()
        result = engine.disable_live(self.live)
        self.assertFalse(result['enabled'])
        self.assertFalse(engine.enabled(self.live))
        self.assertTrue(engine.enabled(other))
        self.assertTrue(engine.enabled(self.demo))
        self.assertEqual(demo_scalp.CONFIG.read_bytes(), before_demo)

    def test_corrupt_store_and_non_boolean_confirmation_fail_closed(self):
        self.authorize()
        valid = engine.STATE_FILE.read_text()
        for key, value in (('confirmed', 'true'), ('enabled', 1), ('actor', ''), ('record_id', '')):
            broken = json.loads(valid)
            broken['live'][self.live.identity][key] = value
            engine.STATE_FILE.write_text(json.dumps(broken))
            self.assertFalse(engine.enabled(self.live))
        engine.STATE_FILE.write_text('broken')
        self.assertFalse(engine.enabled(self.live))
        with self.assertRaises(ValueError):
            self.authorize()
        self.assertEqual(engine.STATE_FILE.read_text(), 'broken')

    def test_no_exchange_or_environment_switch_during_controls(self):
        with patch('okxquant_backend.okx_trade_service._request', side_effect=AssertionError('no exchange')), \
             patch('scripts.okx_runtime.freeze_environment', side_effect=AssertionError('no switch')):
            self.authorize()
            engine.status(self.live)
            engine.disable_live(self.live)

    def test_sampling_and_leverage_limits_match_demo_after_consent_only(self):
        with self.assertRaises(risk.RiskRejected):
            demo_scalp_policy.execution_policy(self.base, self.live)
        self.authorize()
        self.assertEqual(demo_scalp_policy.execution_policy(self.base, self.live),
                         demo_scalp_policy.execution_policy(self.base, self.demo))
        self.assertEqual(leverage.mode_policy(self.base, 'scalp', self.live),
                         leverage.mode_policy(self.base, 'scalp', self.demo))
        self.assertEqual(leverage.mode_policy(self.base, 'scalp', self.live).per_trade_equity_pct, .004)
        self.assertEqual(leverage.mode_policy(self.base, 'swing', self.live).per_trade_equity_pct, .005)
        engine.disable_live(self.live)
        self.assertEqual(leverage.mode_policy(self.base, 'scalp', self.live).max_leverage, 6)

    def request(self, before_write=None):
        def exchange(method, path, params, env):
            self.assertIs(env, self.live)
            if path.endswith('/positions'):
                return []
            if path.endswith('/orders-pending'):
                if before_write: before_write()
                return []
            if path.endswith('/set-leverage'):
                return [{'lever': params['lever']}]
            if path.endswith('/leverage-info'):
                return [{'posSide': 'long', 'lever': '10'}]
            raise AssertionError(path)
        return Mock(side_effect=exchange)

    def test_real_confirmed_binding_and_durable_minute_decision_allow_one_write(self):
        self.authorize(); decision = self.durable_decision(); request = self.request()
        self.assertEqual(leverage.apply(self.live, INST, 'long', 10, 3, decision, request, horizon='scalp'), 10)
        self.assertEqual(sum(c.args[0] == 'POST' for c in request.call_args_list), 1)
        event = evidence.export_events(self.live.identity, 'leverage_change_intent')[-1]['payload']
        self.assertEqual(event['live_binding']['policy_signature'], engine.status(self.live)['policy_signature'])

    def test_reconfirmation_invalidates_previous_durable_decision(self):
        self.authorize(); decision = self.durable_decision()
        self.authorize(); request = self.request()
        with self.assertRaises(risk.RiskRejected):
            leverage.apply(self.live, INST, 'long', 10, 3, decision, request, horizon='scalp')
        request.assert_not_called()

    def test_revocation_during_preflight_never_posts(self):
        self.authorize(); decision = self.durable_decision()
        request = self.request(before_write=lambda: engine.disable_live(self.live))
        with self.assertRaises(risk.RiskRejected):
            leverage.apply(self.live, INST, 'long', 10, 3, decision, request, horizon='scalp')
        self.assertFalse(any(c.args[0] == 'POST' for c in request.call_args_list))

    def test_live_apply_requires_explicit_horizon_and_effective_target_ceiling(self):
        self.authorize(); decision = self.durable_decision(); request = self.request()
        with self.assertRaises(risk.RiskRejected):
            leverage.apply(self.live, INST, 'long', 10, 3, decision, request)
        with self.assertRaises(risk.RiskRejected):
            leverage.apply(self.live, INST, 'long', 21, 3, decision, request, horizon='scalp')
        request.assert_not_called()


class LiveRunnerTests(unittest.TestCase):
    setUp = LiveRuntimeTests.setUp
    authorize = LiveRuntimeTests.authorize

    def runner_fixture(self, *, reconfirm=False, alternate_env=None):
        import ai_factor_trader as trader
        import public_market as market
        from scripts import okx_runtime, ai_brain_trader, instrument_pool, instrument_support
        from scripts import news_connection, ledger_monitor
        from test_demo_scalp import minute_package
        self.authorize()
        package = minute_package(); when = package['data_as_of'] + 8
        item = {'instId': package['instId'], 'name': 'BTC', 'ctVal': 1, 'risk_per_trade_usd': 15}
        parent = threading.get_ident(); observed = []

        def selected(*args, **kwargs):
            self.assertEqual(threading.get_ident(), parent, 'worker must inherit captured account')
            return self.live

        def fetch(item):
            self.assertNotEqual(threading.get_ident(), parent)
            observed.append((market.signal_as_of(), okx_runtime.current_environment().identity))
            return copy.deepcopy(package)

        self.stack.enter_context(patch.object(ledger_monitor, 'DATA', self.root))
        self.stack.enter_context(patch.object(okx_runtime, 'selected_environment', side_effect=selected))
        self.stack.enter_context(patch.object(trader, 'freeze_okx_environment', return_value=alternate_env or self.live))
        self.stack.enter_context(patch.object(trader, 'unfreeze_okx_environment'))
        self.stack.enter_context(patch.object(demo_scalp.time, 'time', return_value=when))
        self.stack.enter_context(patch.object(instrument_pool, 'load_instruments', return_value=[item]))
        self.stack.enter_context(patch.object(instrument_support, 'pool_support', return_value={'items': {package['instId']: {'can_open': True}}}))
        self.stack.enter_context(patch.object(ai_brain_trader, 'fetch_single_instrument_package', side_effect=fetch))
        self.stack.enter_context(patch.object(news_connection, 'load_strategy_snapshot', return_value={}))
        self.stack.enter_context(patch.object(demo_scalp, 'load_policy', return_value=self.base))
        self.stack.enter_context(patch.object(trader, 'is_circuit_breaker_active', return_value=(False, '')))
        self.stack.enter_context(patch.object(trader, 'query_positions', return_value=(True, [], '')))
        self.stack.enter_context(patch.object(trader, 'okx_private_command', side_effect=lambda x: x))
        self.stack.enter_context(patch.object(trader, 'run_cmd_result', return_value={'ok': True, 'data': []}))
        def limits():
            if reconfirm: self.authorize()
            return {'max_positions': 6}
        self.stack.enter_context(patch.object(trader, 'execution_limits', side_effect=limits))
        submit = self.stack.enter_context(patch.object(trader, 'submit_protected_limit_order', return_value=(True, 'mock-only')))
        self.stack.enter_context(patch('scripts.scalp_research.observe', return_value={'status': 'mocked'}))
        return submit, observed, when

    def test_live_runner_freezes_consent_preserves_dedup_and_inherits_worker_frame(self):
        submit, observed, when = self.runner_fixture()
        result = demo_scalp.run()
        self.assertEqual(result['status'], 'submitted', result)
        self.assertEqual(result['mode'], 'live')
        submit.assert_called_once()
        self.assertEqual(observed, [(when, self.live.identity)])
        self.assertEqual(submit.call_args.kwargs['horizon'], 'scalp')
        self.assertFalse(submit.call_args.kwargs['allow_demo_translation'])
        event = evidence.export_events(self.live.identity, 'decision')[-1]['payload']
        frozen = event['decision']['engine_binding']
        current = engine.status(self.live)
        self.assertEqual(frozen, {key: current[key] for key in (*engine.BINDING_FIELDS, 'record_id')})
        self.assertEqual(demo_scalp.run()['status'], 'already_evaluated')
        submit.assert_called_once()

    def test_live_runner_does_not_adopt_new_consent_mid_cycle(self):
        submit, _, _ = self.runner_fixture(reconfirm=True)
        self.assertEqual(demo_scalp.run()['status'], 'disabled')
        submit.assert_not_called()
        self.assertEqual(evidence.export_events(self.live.identity, 'decision'), [])

    def test_live_runner_rejects_changed_binding_even_with_same_scope(self):
        submit, _, _ = self.runner_fixture(alternate_env=replace(self.live, binding_version=8))
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            demo_scalp.run()
        submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
