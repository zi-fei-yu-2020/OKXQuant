import copy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import scenario_entry as engine, scenario_shadow as monitor, wait_diagnostics

BASE = 80 * 3_600_000
SCOPE = 'offline:demo:test'
INST = 'TEST-USDT-SWAP'


def row(at, close=100, high=101, low=99.6, opening=100):
    return {'ts_ms': at, 'open': opening, 'high': high, 'low': low, 'close': close, 'volume': 10., 'confirm': True}


def frame(at=BASE + engine.WIDTH, price=100):
    f = [row(at - (19 - i) * engine.WIDTH) for i in range(20)]
    f[-1] = row(at, price, max(101, price + .2), min(99.6, price - .2))
    h = [row(BASE - (19 - i) * 3_600_000, 103 - 3 * i / 19, 115, 99, 103 - 3 * i / 19) for i in range(20)]
    m = [row(BASE - (19 - i) * 14_400_000, 80 + 20 * i / 19, 102, 75, 80 + 20 * i / 19) for i in range(20)]
    return {'scope': SCOPE, 'instrument': INST, 'at_ms': at, 'entry_supported': True, '5m': f, '1H': h, '4H': m}


def armed():
    return engine.advance(engine.initial_state(SCOPE), frame())


def pullback(state):
    return next(c for c in state['candidates'].values() if c['definition']['scenario'] == 'trend_pullback')


class CandidateTests(unittest.TestCase):
    def test_opposing_hour_momentum_does_not_automatically_veto_pullback(self):
        state = armed()
        candidate = pullback(state)
        self.assertEqual(candidate['status'], 'armed')
        self.assertTrue(candidate['definition']['counter_evidence']['hour_momentum_opposes'])
        self.assertFalse(state['order_authorized'])
        self.assertNotIn('action', candidate['definition'])

    def test_creation_bar_never_triggers_and_next_closed_bar_can(self):
        state = armed()
        self.assertFalse(any(e['kind'] == 'triggered_research' for e in state['events']))
        after = engine.advance(state, frame(BASE + 2 * engine.WIDTH, 101.2))
        self.assertEqual(pullback(after)['status'], 'triggered_research')
        self.assertEqual(pullback(state)['status'], 'armed')
        event = next(e for e in after['events'] if e['kind'] == 'triggered_research')
        self.assertFalse(event['review_request']['order_authorized'])
        self.assertEqual(event['review_request']['valid_until_ms'] - event['at_ms'], engine.WIDTH)

    def test_frozen_definition_is_not_moved_by_new_indicators(self):
        state = armed()
        new_frame = frame(BASE + 2 * engine.WIDTH)
        new_frame['1H'][5]['high'] = 150
        after = engine.advance(state, new_frame)
        self.assertEqual(pullback(after)['definition'], pullback(state)['definition'])
        self.assertEqual(pullback(after)['id'], pullback(state)['id'])

    def test_duplicate_frame_is_idempotent(self):
        state = armed()
        self.assertEqual(engine.advance(state, frame()), state)

    def test_modified_frozen_definition_is_detected(self):
        state = armed()
        pullback(state)['definition']['target'] += 1
        with self.assertRaisesRegex(ValueError, 'modified'):
            engine.advance(state, frame(BASE + 2 * engine.WIDTH))

    def test_changed_spec_requires_new_run(self):
        with self.assertRaisesRegex(ValueError, 'spec_changed'):
            engine.advance(armed(), frame(BASE + 2 * engine.WIDTH), replace(engine.Spec(), lifetime_ms=600_000))

    def test_scope_cannot_mix(self):
        value = frame()
        value['scope'] = 'other'
        with self.assertRaisesRegex(ValueError, 'scope'):
            engine.advance(engine.initial_state(SCOPE), value)

    def test_stop_breach_wins_over_trigger_in_same_candle(self):
        value = frame(BASE + 2 * engine.WIDTH, 101.2)
        value['5m'][-1]['low'] = 98
        after = engine.advance(armed(), value)
        self.assertEqual(pullback(after)['status'], 'invalidated')
        self.assertFalse(any(e['kind'] == 'triggered_research' for e in after['events']))

    def test_expiry_not_extended_or_triggered_at_expiry(self):
        state = armed()
        at = pullback(state)['definition']['expires_at_ms']
        after = engine.advance(state, frame(at, 101.2))
        self.assertEqual(pullback(after)['status'], 'expired')

    def test_monitoring_gap_never_backfills_a_live_trigger(self):
        after = engine.advance(armed(), frame(BASE + 3 * engine.WIDTH, 101.2))
        self.assertEqual(pullback(after)['status'], 'invalidated')
        self.assertEqual(after['frames'][INST]['status'], 'monitoring_gap')

    def test_missing_frame_then_recovery_is_still_a_monitoring_gap(self):
        value = frame(BASE + 2 * engine.WIDTH)
        value['5m'] = []
        state = engine.advance(armed(), value)
        self.assertEqual(pullback(state)['status'], 'armed')
        after = engine.advance(state, frame(BASE + 3 * engine.WIDTH, 101.2))
        self.assertEqual(pullback(after)['status'], 'invalidated')

    def test_unconfirmed_future_and_duplicate_bars_are_unavailable(self):
        for mutation in ('unconfirmed', 'future', 'duplicate', 'nan', 'mismatch'):
            with self.subTest(mutation=mutation):
                value = frame()
                if mutation == 'unconfirmed': value['5m'][-1]['confirm'] = False
                if mutation == 'future': value['5m'][-1]['ts_ms'] += engine.WIDTH
                if mutation == 'duplicate': value['5m'][-1]['ts_ms'] = value['5m'][-2]['ts_ms']
                if mutation == 'nan': value['5m'][-1]['close'] = float('nan')
                if mutation == 'mismatch': value['4H'][-1]['close'] = 101
                state = engine.advance(engine.initial_state(SCOPE), value)
                self.assertEqual(state['frames'][INST]['status'], 'data_unavailable')
                self.assertFalse(state['candidates'])

    def test_unsupported_instrument_never_arms_even_without_candles(self):
        value = frame()
        value['entry_supported'] = False
        value['5m'] = []
        after = engine.advance(engine.initial_state(SCOPE), value)
        self.assertEqual(after['frames'][INST]['status'], 'observation_only')
        self.assertFalse(after['candidates'])

    def test_net_rr_rejects_without_moving_target(self):
        state = armed()
        rejected = next(c for c in state['candidates'].values() if c['definition']['scenario'] == 'range_breakout')
        self.assertEqual(rejected['status'], 'rejected')
        self.assertLess(rejected['initial_net_rr'], engine.Spec().minimum_net_rr)
        self.assertAlmostEqual(rejected['definition']['target'], 102.4)

    def test_rejected_anchor_does_not_churn_new_candidates(self):
        state = armed()
        first = len([e for e in state['events'] if e['kind'] == 'candidate_rejected'])
        after = engine.advance(state, frame(BASE + 2 * engine.WIDTH))
        self.assertEqual(first, len([e for e in after['events'] if e['kind'] == 'candidate_rejected']))

    def test_chase_limit_is_a_rejection_not_a_changed_entry(self):
        after = engine.advance(armed(), frame(BASE + 2 * engine.WIDTH, 108))
        self.assertEqual(pullback(after)['status'], 'rejected')
        self.assertTrue(any(e.get('reason') == 'chase_limit_exceeded' for e in after['events']))

    def test_cost_defaults_match_existing_risk_policy(self):
        from scripts.risk_policy import Policy
        for field in ('minimum_net_rr', 'slippage', 'taker_fee'):
            self.assertEqual(getattr(engine.Spec(), field), getattr(Policy(), field))

    def test_invalid_spec_and_out_of_order_are_rejected(self):
        with self.assertRaises(ValueError): engine.Spec(slippage=-1)
        with self.assertRaisesRegex(ValueError, 'out_of_order'):
            engine.advance(armed(), frame(BASE))

    def test_short_side_is_symmetric(self):
        def mirror(value):
            for tf in ('5m', '1H', '4H'):
                for r in value[tf]:
                    r['open'], r['close'], r['high'], r['low'] = 200-r['open'], 200-r['close'], 200-r['low'], 200-r['high']
            return value
        state = engine.advance(engine.initial_state(SCOPE), mirror(frame()))
        self.assertEqual(pullback(state)['definition']['side'], 'short')
        after = engine.advance(state, mirror(frame(BASE + 2 * engine.WIDTH, 101.2)))
        self.assertEqual(pullback(after)['status'], 'triggered_research')


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'shadow.db'

    def test_disabled_and_status_reads_create_nothing(self):
        self.assertFalse(monitor.run_once({}, path=self.path)['enabled'])
        self.assertFalse(monitor.public_status(SCOPE, path=self.path)['enabled'])
        self.assertFalse(self.path.exists())

    def test_database_transaction_deduplicates_and_isolates_scopes(self):
        state = monitor.apply_frames(SCOPE, [frame()], path=self.path)
        after = monitor.apply_frames(SCOPE, [frame()], path=self.path)
        self.assertEqual(state, after)
        self.assertFalse(monitor.public_status('other', path=self.path)['enabled'])
        result = monitor.public_status(SCOPE, path=self.path, now=(BASE+engine.WIDTH)/1000)
        self.assertTrue(result['enabled'])
        self.assertFalse(result['stale'])
        self.assertFalse(result['order_authorized'])

    def test_corrupt_state_cannot_be_reset_into_a_fresh_signal(self):
        monitor.apply_frames(SCOPE, [frame()], path=self.path)
        with monitor.connect(self.path) as db:
            db.execute('UPDATE states SET payload=?', ('broken',))
        with self.assertRaises(ValueError):
            monitor.apply_frames(SCOPE, [frame(BASE+2*engine.WIDTH)], path=self.path)
        self.assertEqual(monitor.public_status(SCOPE, path=self.path)['status'], 'unavailable')

    def test_monitor_needs_explicit_network_opt_in(self):
        with self.assertRaisesRegex(ValueError, 'allow-public-network'):
            monitor.run_once({'enabled': True, 'mode': 'shadow_only'}, path=self.path)
        self.assertFalse(self.path.exists())

    def test_support_missing_or_stale_does_not_call_market(self):
        config = {'enabled': True, 'mode': 'shadow_only', 'scope': SCOPE, 'simulated': True,
                  'instruments': [{'instId': INST, 'can_open': True, 'checked_at': 0}]}
        with patch('scripts.public_market.get_json') as get:
            result = monitor.run_once(config, path=self.path, now=BASE/1000+310, allow_public_network=True)
        get.assert_not_called()
        self.assertFalse(result['order_authorized'])

    def test_public_candle_requests_shared_cached_and_bounded(self):
        now = (BASE + engine.WIDTH) / 1000 + 10
        config = {'enabled': True, 'mode': 'shadow_only', 'scope': SCOPE, 'simulated': True,
                  'instruments': [{'instId': INST, 'can_open': True, 'checked_at': now}]}
        values = frame()
        def reader(url, **kwargs):
            from urllib.parse import parse_qs, urlsplit
            self.assertLessEqual(kwargs['timeout'], 3)
            parts = urlsplit(url)
            self.assertEqual(parts.path, '/api/v5/market/candles')
            tf = parse_qs(parts.query)['bar'][0]
            width = {'5m': engine.WIDTH, '1H': 3_600_000, '4H': 14_400_000}[tf]
            return {'code': '0', 'data': [[r['ts_ms']-width,r['open'],r['high'],r['low'],r['close'],r['volume'],0,0,'1'] for r in reversed(values[tf])]}
        with patch('scripts.public_market.get_json', side_effect=reader) as get:
            monitor.run_once(config, path=self.path, now=now, allow_public_network=True)
            self.assertEqual(get.call_count, 3)
            result = monitor.run_once(config, path=self.path, now=now+10, allow_public_network=True)
            self.assertEqual(result['status'], 'already_observed_or_attempted')
            self.assertEqual(get.call_count, 3)
            values = frame(BASE+2*engine.WIDTH)
            monitor.run_once(config, path=self.path, now=now+300, allow_public_network=True)
            self.assertEqual(get.call_count, 4)  # 1H and 4H not re-fetched this round.

    def test_market_429_has_no_retry_and_no_trading_imports(self):
        now = (BASE+engine.WIDTH)/1000+10
        config = {'enabled': True, 'mode': 'shadow_only', 'scope': SCOPE, 'simulated': True,
                  'instruments': [{'instId': INST, 'can_open': True, 'checked_at': now}]}
        with patch('scripts.public_market.get_json', side_effect=RuntimeError('HTTP 429')) as get:
            monitor.run_once(config, path=self.path, now=now, allow_public_network=True)
            monitor.run_once(config, path=self.path, now=now+10, allow_public_network=True)
            self.assertEqual(get.call_count, 1)
        for name in ('scenario_entry.py', 'scenario_shadow.py'):
            source = (Path(engine.__file__).parent/name).read_text(encoding='utf8')
            for unsafe in ('import entry_gateway', 'import ai_factor_trader', 'import okx_trade_service', 'subprocess', 'llm_transport'):
                self.assertNotIn(unsafe, source)


class DiagnosticTests(unittest.TestCase):
    def test_incomplete_waits_are_not_reported_as_valid_no_opportunity(self):
        snapshot = {'scope': SCOPE, 'wait': {'no_entry_candidate_streak': 16}, 'events': [
            {'payload': {'generated_at_ms': BASE, 'instrument': INST, 'decision': {
                'action': 'WAIT', 'contract_valid': False, 'decision_status': 'audited_wait',
                'validation_reason': 'missing review', 'previous_wait_review': {'trigger_checks': {'long': 'met'}}}}}]}
        result = wait_diagnostics.diagnose(snapshot)
        self.assertEqual(result['decision_counts'], {'incomplete': 1})
        self.assertEqual(len(result['trigger_met_but_wait']), 1)
        self.assertEqual(result['reported_streak'], 16)


class ScenarioSafetyRegressionTests(unittest.TestCase):
    def test_concurrent_monitor_calls_claim_one_cycle_only(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'shadow.db'
            # Pre-create the schema, just as a configured runner would on its first cycle.
            monitor.connect(path).close()
            config = {'enabled':True,'mode':'shadow_only','scope':SCOPE,'simulated':True,
                      'instruments':[{'instId':INST,'can_open':False}]}
            barrier = threading.Barrier(2)
            def run(_):
                barrier.wait(timeout=5)
                return monitor.run_once(config,path=path,now=BASE/1000+310,allow_public_network=True)['status']
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(run, range(2)))
            self.assertEqual(sorted(results), ['already_observed_or_attempted', 'observed'])

    def test_shadow_candidate_cannot_pass_production_entry_authority(self):
        from scripts import entry_gateway, strategy_evidence
        from scripts.okx_runtime import OKXEnvironment
        from scripts.risk_policy import RiskRejected
        env = OKXEnvironment('demo', 'fake', 'fake', 'fake')
        with tempfile.TemporaryDirectory() as directory, patch.object(strategy_evidence, 'DB_PATH', Path(directory)/'production-evidence.db'):
            value = frame()
            value['scope'] = env.identity
            state = monitor.apply_frames(env.identity, [value], path=Path(directory)/'shadow.db')
            with patch.object(entry_gateway.time, 'time', return_value=1000), patch.object(entry_gateway, '_request') as request:
                with self.assertRaisesRegex(RiskRejected, 'evidence not found'):
                    entry_gateway.prepare(env, inst_id=INST, side='long', entry=101, stop=99, take_profit=115,
                                          requested_size=1, budget=1, decision_id=pullback(state)['id'], decision_at=1000)
                request.assert_not_called()

    def test_partial_batch_is_rolled_back_on_scope_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'shadow.db'
            other = frame(BASE+2*engine.WIDTH)
            other['scope'] = 'wrong'
            with self.assertRaises(ValueError): monitor.apply_frames(SCOPE, [frame(), other], path=path)
            self.assertFalse(monitor.public_status(SCOPE, path=path)['enabled'])

    def test_expired_review_is_displayed_without_updating_durable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'shadow.db'
            monitor.apply_frames(SCOPE, [frame(), frame(BASE+2*engine.WIDTH,101.2)], path=path)
            result = monitor.public_status(SCOPE,path=path,now=(BASE+4*engine.WIDTH)/1000)
            candidate = next(c for c in result['candidates'] if c['definition']['scenario']=='trend_pullback')
            self.assertEqual(candidate['status'],'triggered_research')
            self.assertEqual(candidate['display_status'],'review_expired')

    def test_market_environment_cannot_reuse_the_same_cache_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'shadow.db'
            config={'enabled':True,'mode':'shadow_only','scope':SCOPE,'simulated':True,'instruments':[{'instId':INST,'can_open':False}]}
            monitor.run_once(config,path=path,now=BASE/1000+310,allow_public_network=True)
            config['simulated']=False
            with self.assertRaisesRegex(ValueError,'environment/instruments changed'):
                monitor.run_once(config,path=path,now=BASE/1000+610,allow_public_network=True)

    def test_local_catalog_mode_does_not_launch_a_refresh_worker(self):
        from scripts import instrument_support
        with tempfile.TemporaryDirectory() as directory, patch.object(instrument_support,'_read',return_value=None),patch.object(instrument_support,'refresh_catalog') as refresh,patch('scripts.public_market.get_json') as request:
            config={'enabled':True,'mode':'shadow_only','scope':SCOPE,'simulated':True,'support_source':'local_catalog','instruments':[{'instId':INST}]}
            monitor.run_once(config,path=Path(directory)/'shadow.db',now=BASE/1000+310,allow_public_network=True)
            request.assert_not_called();refresh.assert_not_called()

    def test_resampled_frames_do_not_include_future_candles(self):
        from scripts.scenario_replay import frames_for
        rows=[row((i+1)*engine.WIDTH) for i in range(1200)]
        start,cutoff=86*3_600_000,88*3_600_000
        first=list(frames_for(rows,INST,SCOPE,start,cutoff))
        changed=copy.deepcopy(rows)
        for r in changed:
            if r['ts_ms']>cutoff:
                for key in ('open','high','low','close'):r[key]+=1000
        self.assertEqual(first,list(frames_for(changed,INST,SCOPE,start,cutoff)))
        self.assertTrue(all(all(r['ts_ms']<=f['at_ms'] for r in f[tf]) for f in first for tf in ('5m','1H','4H')))

    def test_replay_rejects_modified_capture_hash(self):
        from scripts.scenario_replay import validate_dataset
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            validate_dataset({'series':{},'metadata':{},'start_ms':0,'end_ms':300000,'capture_hash':'wrong'})
