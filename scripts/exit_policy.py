"""Versioned position-exit settings, independent of entry authorization.

These are engineering thresholds, not calibrated probabilities or return claims.
A failed activation read must neither disable hard stops nor silently select a
more permissive preset. Verified selections survive through position trackers.
"""
from copy import deepcopy
import math

VERSION = 'position-exits-v1'
HORIZON_PRESETS = {
    'scalp': {'time_stop_seconds': 3600, 'time_stop_profit_atr': .05, 'tier1_breakeven_atr': 1.2, 'tier2_lock_atr': 2.0, 'tier1_floor_atr': .2, 'tier2_floor_atr': .7, 'kinetic_peak_atr': 1.2, 'kinetic_pullback_atr': .55},
    'swing': {'time_stop_seconds': 14400, 'time_stop_profit_atr': .10, 'tier1_breakeven_atr': 1.8, 'tier2_lock_atr': 3.0, 'tier1_floor_atr': .3, 'tier2_floor_atr': 1.0, 'kinetic_peak_atr': 1.8, 'kinetic_pullback_atr': .8},
}

PRESETS = {
    'standard': {
        'time_stop_seconds': 21600,
        'time_stop_profit_atr': .15,
        'tier1_breakeven_atr': 2.5,
        'tier2_lock_atr': 4.0,
        'tier1_floor_atr': .5,
        'tier2_floor_atr': 1.5,
        'kinetic_peak_atr': 2.5,
        'kinetic_pullback_atr': 1.2,
    },
    'small300': {
        'time_stop_seconds': 14400,
        'time_stop_profit_atr': .10,
        'tier1_breakeven_atr': 1.8,
        'tier2_lock_atr': 3.0,
        'tier1_floor_atr': .3,
        'tier2_floor_atr': 1.0,
        'kinetic_peak_atr': 1.8,
        'kinetic_pullback_atr': .8,
    },
}


def thresholds(preset_id):
    if preset_id == 'fallback':
        # Earlier activation/exit, greater retained floor; never widen a stop.
        larger_is_protective = {'time_stop_profit_atr', 'tier1_floor_atr', 'tier2_floor_atr'}
        return {key: (max if key in larger_is_protective else min)(p[key] for p in PRESETS.values())
                for key in PRESETS['standard']}
    return deepcopy(PRESETS[preset_id])


def _valid_snapshot(snapshot):
    return (isinstance(snapshot, dict) and snapshot.get('version') == VERSION
            and isinstance(snapshot.get('preset_id'), str) and snapshot['preset_id'] in PRESETS
            and isinstance(snapshot.get('execution_signature'), str)
            and bool(snapshot['execution_signature']))


def resolve(tracker, runtime_reader):
    """Persist only verified selections. Degraded reads never overwrite them."""
    try:
        current = runtime_reader()
        snapshot = {'version': VERSION, 'preset_id': current['execution']['id'],
                    'execution_signature': current['signature']}
        if not _valid_snapshot(snapshot):
            raise ValueError('Unknown or unverifiable execution preset')
        tracker['exitPolicy'] = snapshot
        status = {'version': VERSION, 'preset_id': snapshot['preset_id'], 'source': 'active_profile'}
    except Exception as exc:
        previous = tracker.get('exitPolicy')
        known = _valid_snapshot(previous)
        status = {'version': VERSION, 'preset_id': previous['preset_id'] if known else 'fallback',
                  'source': 'last_verified' if known else 'conservative_fallback',
                  'error_type': type(exc).__name__}
    tracker['exitPolicyStatus'] = status
    selected=thresholds(status['preset_id'])
    if status['preset_id']=='small300':
        horizon=str(tracker.get('horizon','swing')).lower()
        selected=deepcopy(HORIZON_PRESETS.get(horizon,HORIZON_PRESETS['swing']))
    return selected, status


def volatility(factor):
    """One observed ATR basis for all dynamic exits; no invented percentage floor."""
    for field in ('atr_15m', 'atr'):
        value = factor.get(field)
        if isinstance(value, bool):
            continue
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value) and value > 0:
            return {'value': value, 'source': field}
    return {'value': 0., 'source': 'unavailable'}
