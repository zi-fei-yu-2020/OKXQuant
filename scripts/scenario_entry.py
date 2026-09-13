"""Causal scenario candidates for SHADOW research, never executable trade decisions.

Frozen 4H environment / 1H structure / closed-5m triggers. No model, account,
exchange, filesystem, or trading imports. Definitions may not mutate while armed.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math

VERSION = 'scenario-shadow-v1'
WIDTH = 300_000
TERMINAL = {'triggered_research', 'invalidated', 'expired', 'rejected'}


@dataclass(frozen=True)
class Spec:
    ema_period: int = 16
    atr_period: int = 14
    lifetime_ms: int = 45 * 60_000
    max_chase_atr: float = .25
    minimum_net_rr: float = 2.0
    taker_fee: float = .0005
    slippage: float = .001

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid_shadow_spec_' + name)
        if any(type(value) is not int for value in (self.ema_period, self.atr_period, self.lifetime_ms)):
            raise ValueError('shadow_periods_must_be_integers')
        if self.lifetime_ms % WIDTH or self.taker_fee >= 1 or self.slippage >= 1:
            raise ValueError('invalid_shadow_spec_bounds')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def initial_state(scope, spec=None):
    return {'version': VERSION, 'scope': scope, 'spec_hash': digest(asdict(spec or Spec())),
            'last_at': {}, 'last_valid_at': {}, 'candidates': {}, 'consumed': {}, 'events': [], 'frames': {},
            'mode': 'shadow_only', 'order_authorized': False}


def validate_bars(rows, width, at, minimum=20):
    if not isinstance(rows, list) or len(rows) < minimum:
        raise ValueError('insufficient_closed_candles')
    previous = None
    for row in rows:
        ts = row.get('ts_ms')
        if not isinstance(ts, int) or isinstance(ts, bool) or ts % width or ts > at or row.get('confirm') is not True:
            raise ValueError('unconfirmed_or_future_candle')
        vals = [row.get(k) for k in ('open', 'high', 'low', 'close', 'volume')]
        if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in vals):
            raise ValueError('invalid_candle_values')
        o, h, l, c, v = vals
        if not (0 < l <= min(o, c) <= max(o, c) <= h and v >= 0):
            raise ValueError('invalid_candle_geometry')
        if previous is not None and ts != previous + width:
            raise ValueError('candle_gap_or_duplicate')
        previous = ts
    if rows[-1]['ts_ms'] != at // width * width:
        raise ValueError('stale_candles')


def stats(rows, spec):
    alpha = 2 / (spec.ema_period + 1)
    ema = rows[0]['close']
    prior_ema = ema
    trs = []
    prev = None
    for row in rows:
        prior_ema = ema
        ema = alpha * row['close'] + (1 - alpha) * ema
        trs.append(max(row['high'] - row['low'], abs(row['high'] - prev), abs(row['low'] - prev)) if prev is not None else row['high'] - row['low'])
        prev = row['close']
    return {'ema': ema, 'prior_ema': prior_ema, 'atr': sum(trs[-spec.atr_period:]) / spec.atr_period}


def net_rr(entry, stop, target, side, spec):
    if not (0 < stop < entry < target if side == 'long' else 0 < target < entry < stop):
        return None
    # Same conservative per-unit cost convention as the existing WAIT geometry audit.
    cost = (entry + max(stop, target)) * spec.taker_fee + entry * 2 * spec.slippage
    return (abs(target - entry) - cost) / (abs(entry - stop) + cost)


def proposals(frame, spec):
    """Structural research hypotheses, not a forecast of calibrated success odds."""
    h, m, f = frame['1H'], frame['4H'], frame['5m']
    window = max(20, spec.ema_period + 1, spec.atr_period)
    hs, ms = stats(h[-window:], spec), stats(m[-window:], spec)
    if hs['atr'] <= 0:
        return [], 'no_structural_range'
    long = m[-1]['close'] > ms['ema'] and ms['ema'] > ms['prior_ema']
    short = m[-1]['close'] < ms['ema'] and ms['ema'] < ms['prior_ema']
    if not long and not short:
        return [], 'no_directional_environment'
    side = 'long' if long else 'short'
    prior = f[-4:-1]  # Trigger level excludes the candidate-creation candle.
    trigger = max(r['high'] for r in prior) if long else min(r['low'] for r in prior)
    support = min(r['low'] for r in h[-6:]) if long else max(r['high'] for r in h[-6:])
    target = max(r['high'] for r in h[-12:]) if long else min(r['low'] for r in h[-12:])
    base = {'instrument': frame['instrument'], 'side': side, 'created_at_ms': frame['at_ms'],
            'expires_at_ms': frame['at_ms'] + spec.lifetime_ms, 'hour_anchor_ms': h[-1]['ts_ms'],
            'macro_anchor_ms': m[-1]['ts_ms'], 'atr_1h': hs['atr'], 'macro_ema': ms['ema'],
            'hour_ema': hs['ema'], 'hour_ema_slope': hs['ema'] - hs['prior_ema'],
            'authorization': 'none', 'version': VERSION}
    result = []
    bar = f[-1]
    in_pullback = (bar['low'] <= hs['ema'] + hs['atr'] * .25 and support < bar['low'] <= bar['close'] <= trigger) if long else (bar['high'] >= hs['ema'] - hs['atr'] * .25 and support > bar['high'] >= bar['close'] >= trigger)
    if in_pullback:
        result.append({**base, 'scenario': 'trend_pullback', 'trigger': trigger, 'stop': support, 'target': target,
                       'target_basis': 'previous_12_closed_hour_extreme', 'stop_basis': 'previous_6_closed_hour_extreme',
                       'trigger_basis': 'previous_3_closed_5m_extreme',
                       'counter_evidence': {'hour_momentum_opposes': hs['ema'] < hs['prior_ema'] if long else hs['ema'] > hs['prior_ema']},
                       'remaining_uncertainty': 'Local recovery can fail even while the larger structure remains intact.'})
    channel = f[-13:-1]
    upper, lower = max(r['high'] for r in channel), min(r['low'] for r in channel)
    if lower <= bar['close'] <= upper and upper > lower:
        level = upper if long else lower
        stop = min(r['low'] for r in prior) if long else max(r['high'] for r in prior)
        result.append({**base, 'scenario': 'range_breakout', 'trigger': level, 'stop': stop,
                       'target': level + (upper - lower) * (1 if long else -1),
                       'target_basis': 'one_frozen_range_projection_hypothesis', 'stop_basis': 'previous_3_closed_5m_extreme',
                       'trigger_basis': 'previous_12_closed_5m_range',
                       'counter_evidence': {'false_breakout_possible': True},
                       'remaining_uncertainty': 'A measured-range target is a hypothesis, not observed future liquidity.'})
    return result, '' if result else 'no_setup_in_current_price_zone'


def advance(state, frame, spec=None):
    spec = spec or Spec()
    if state.get('version') != VERSION or state.get('spec_hash') != digest(asdict(spec)):
        raise ValueError('shadow_version_or_spec_changed_use_new_run')
    if not state.get('scope') or frame.get('scope') != state['scope']:
        raise ValueError('shadow_scope_mismatch')
    if state.get('mode') != 'shadow_only' or state.get('order_authorized') is not False:
        raise ValueError('shadow_authority_invalid')
    at, inst = frame['at_ms'], frame['instrument']
    if not isinstance(at, int) or isinstance(at, bool) or at % WIDTH:
        raise ValueError('invalid_frame_time')
    state = {**deepcopy({k: v for k, v in state.items() if k != 'events'}), 'events': list(state['events'])}
    old_at = state['last_at'].get(inst)
    if old_at is not None and at < old_at:
        raise ValueError('out_of_order_frame')
    if old_at == at:
        return state  # Duplicate deliveries never create another trigger/review.
    state['last_at'][inst] = at
    events = []
    def event(kind, candidate=None, **details):
        identity = candidate.get('id') if candidate else None
        value = {'kind': kind, 'at_ms': at, 'instrument': inst, 'candidate_id': identity,
                 'scope': state['scope'], 'order_authorized': False, **details}
        value['event_id'] = digest(value)
        events.append(value)
    data_error = ''
    try:
        for name, width in (('5m', WIDTH), ('1H', 3_600_000), ('4H', 14_400_000)):
            validate_bars(frame.get(name), width, at, max(20, spec.ema_period + 1, spec.atr_period))
        by_time = {row['ts_ms']: row['close'] for row in frame['5m']}
        for name in ('1H', '4H'):
            row = frame[name][-1]
            if row['ts_ms'] in by_time and not math.isclose(row['close'], by_time[row['ts_ms']], rel_tol=1e-8):
                raise ValueError('cross_timeframe_close_mismatch')
    except (ValueError, TypeError, KeyError) as exc:
        data_error = str(exc)
        if frame.get('collection_error'):
            data_error = 'collection_unavailable:' + str(frame['collection_error'])[:100]
    previous_valid = state['last_valid_at'].get(inst)
    gap = previous_valid is not None and at - previous_valid != WIDTH
    if not data_error:
        state['last_valid_at'][inst] = at
    existing = [c for c in state['candidates'].values() if c['definition']['instrument'] == inst]
    for c in existing:
        if digest(c['definition']) != c['definition_hash']:
            raise ValueError('frozen_candidate_was_modified')
        if c['status'] != 'armed':
            continue
        d = c['definition']
        failure = None
        if at >= d['expires_at_ms']:
            failure = ('expired', 'fixed_expiry_reached')
        elif gap:
            failure = ('invalidated', 'monitoring_gap_no_retroactive_trigger')
        elif frame.get('entry_supported') is not True:
            failure = ('rejected', 'environment_not_supported_or_unknown')
        elif data_error:
            event('data_unavailable', c, reason=data_error)
            continue
        else:
            bar, previous = frame['5m'][-1], frame['5m'][-2]
            long = d['side'] == 'long'
            invalid = bar['low'] <= d['stop'] if long else bar['high'] >= d['stop']
            ms = stats(frame['4H'][-max(20, spec.ema_period + 1, spec.atr_period):], spec)
            macro_valid = (frame['4H'][-1]['close'] > ms['ema'] and ms['ema'] > ms['prior_ema']) if long else (frame['4H'][-1]['close'] < ms['ema'] and ms['ema'] < ms['prior_ema'])
            crossed = previous['close'] <= d['trigger'] < bar['close'] if long else previous['close'] >= d['trigger'] > bar['close']
            if invalid:
                failure = ('invalidated', 'frozen_structure_broken')
            elif not macro_valid:
                failure = ('invalidated', 'macro_environment_invalidated')
            elif crossed:
                rr = net_rr(bar['close'], d['stop'], d['target'], d['side'], spec)
                if abs(bar['close'] - d['trigger']) > d['atr_1h'] * spec.max_chase_atr:
                    failure = ('rejected', 'chase_limit_exceeded')
                elif rr is None or rr < spec.minimum_net_rr:
                    failure = ('rejected', 'current_price_net_rr_insufficient')
                else:
                    c['status'] = 'triggered_research'
                    c['finished_at_ms'] = at
                    c['reason'] = 'closed_5m_trigger_requires_fresh_review'
                    event('triggered_research', c, price=bar['close'], net_rr=rr,
                          definition_hash=c['definition_hash'],
                          review_request={'candidate_id': c['id'], 'observed_at_ms': at,
                                          'valid_until_ms': at + WIDTH, 'order_authorized': False,
                                          'required': ['fresh_market_snapshot', 'new_model_evidence_review', 'account_and_pending_reconciliation', 'final_risk_and_protection_checks']})
        if failure:
            c['status'], reason = failure
            c['reason'] = reason
            c['finished_at_ms'] = at
            event(c['status'], c, reason=reason)
    frame_status = 'observed'
    if frame.get('entry_supported') is not True:
        frame_status = 'observation_only'
        event('environment_restriction', reason='environment_not_supported_or_unknown')
    elif data_error:
        frame_status = 'data_unavailable'
        event('data_unavailable', reason=data_error)
    elif gap:
        frame_status = 'monitoring_gap'
        event('monitoring_gap', reason='skip_candidate_creation_on_gap')
    else:
        plans, reason = proposals(frame, spec)
        if not plans:
            event('no_setup', reason=reason)
        for definition in plans:
            key = inst + ':' + definition['scenario'] + ':' + definition['side']
            token = key + ':' + str(definition['hour_anchor_ms'])
            active = state['candidates'].get(key)
            if active and active['status'] == 'armed':
                continue  # Do not move trigger, stop, target or expiry after creation.
            if token in state['consumed']:
                continue  # At most one hypothesis per scenario/direction/hour anchor.
            state['consumed'][token] = at
            rr = net_rr(definition['trigger'], definition['stop'], definition['target'], definition['side'], spec)
            cid = digest([state['scope'], state['spec_hash'], definition])
            c = {'id': cid, 'definition': definition, 'definition_hash': digest(definition), 'status': 'armed', 'initial_net_rr': rr, 'reason': 'waiting_fixed_price_trigger'}
            state['candidates'][key] = c
            if rr is None or rr < spec.minimum_net_rr:
                c['status'] = 'rejected'
                c['reason'] = 'initial_geometry_or_net_rr_insufficient'
                c['finished_at_ms'] = at
                event('candidate_rejected', c, reason='initial_geometry_or_net_rr_insufficient', net_rr=rr, definition=definition)
            else:
                event('candidate_created', c, definition=definition, definition_hash=c['definition_hash'], net_rr=rr)
    state['frames'][inst] = {'at_ms': at, 'status': frame_status, 'reason': data_error,
                             'observation_hash': digest({'at_ms': at, 'instrument': inst, 'invalid': data_error}) if data_error else digest({k: frame.get(k) for k in ('at_ms', 'instrument', 'entry_supported', '5m', '1H', '4H')})}
    state['events'] = (state['events'] + events)[-1000:]
    state['consumed'] = {k: v for k, v in state['consumed'].items() if v >= at - 86_400_000}
    return state
