"""Minute-engine control plane: legacy DEMO switch, explicit bound LIVE consent.

Reading status has no exchange or persistence side effects. Controls require the
current managed LIVE binding; HTTP authentication/confirmation belongs to the
admin API. Consent does not switch accounts or submit/retry exchange operations.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / 'data' / 'strategy_engine_runtime.json'
VERSION = 'strategy-engine-runtime-v1'
ENGINE_VERSION = 'demo-scalp-v2'
ENGINE_ID = 'demo_scalp_v2'  # Stable strategy identity, not an environment selector.
BINDING_FIELDS = ('environment', 'engine_version', 'account_scope', 'connection_id',
                  'binding_version', 'profile_signature', 'execution_signature', 'policy_signature')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _identity(env):
    return {'environment': getattr(env, 'mode', ''), 'engine_version': ENGINE_VERSION,
            'account_scope': getattr(env, 'identity', ''),
            'connection_id': getattr(env, 'connection_id', ''),
            'binding_version': getattr(env, 'binding_version', 0)}


def _assert_live(env):
    from scripts.risk_policy import RiskRejected
    from okxquant_backend.account_connections import assert_current
    identity = _identity(env)
    if (identity['environment'] != 'live' or not identity['account_scope']
            or not identity['connection_id'] or type(identity['binding_version']) is not int
            or identity['binding_version'] < 1 or not getattr(env, 'configured', False)):
        raise RiskRejected('LIVE minute consent requires the current managed LIVE account binding')
    assert_current(env)


def _autotrade_enabled():
    from scripts.okx_runtime import _load_dotenv
    return _load_dotenv().get('OKXQUANT_AUTOTRADE_ENABLED', '1') == '1'


def _current_binding(env):
    from scripts import execution_profiles, prompt_library, trading_prompt, risk_policy
    from scripts import demo_scalp_policy, execution_costs, strategy_modes, capital_pool
    execution = execution_profiles.runtime()
    profile = prompt_library.active_profile()
    profile_hash = trading_prompt.profile_signature(profile)
    if execution_profiles.runtime(profile)['signature'] != execution['signature']:
        raise ValueError('Profile changed while reading execution binding')
    policy = risk_policy.load_policy()
    mode = strategy_modes.mode_for('scalp')
    signature = _hash({'risk_policy': asdict(policy), 'entry_policy': demo_scalp_policy.descriptor(),
                       'mode': mode, 'execution_costs_version': execution_costs.VERSION,
                       'capital_pool_signature': capital_pool.config_signature()})
    if (execution_profiles.runtime()['signature'] != execution['signature']
            or trading_prompt.profile_signature(prompt_library.active_profile()) != profile_hash):
        raise ValueError('Profile changed while reading execution binding')
    return {**_identity(env), 'profile_signature': profile_hash,
            'execution_signature': execution['signature'], 'policy_signature': signature,
            'limits': {'max_leverage': min(policy.scalp_max_leverage, mode['max_leverage']),
                       'per_trade_equity_pct': min(policy.per_trade_equity_pct, mode['risk_per_trade_equity_pct']),
                       'minimum_net_rr': demo_scalp_policy.MINIMUM_NET_RR}}


def _load():
    if not STATE_FILE.exists():
        return {'version': VERSION, 'live': {}}
    value = json.loads(STATE_FILE.read_text(encoding='utf8'))
    if (not isinstance(value, dict) or value.get('version') != VERSION
            or not isinstance(value.get('live'), dict)):
        raise ValueError('Invalid minute-engine consent state')
    return value


def _save(value):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.minute-consent-', suffix='.json', dir=STATE_FILE.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, STATE_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def status(env):
    """Read-only, fail-closed state; never changes DEMO's legacy opt-in contract."""
    result = {**_identity(env), 'enabled': False, 'authorized': False,
              'status': 'confirmation_required', 'version': VERSION}
    try:
        if env.mode == 'demo':
            # Retain the existing public CONFIG override used by operators/tests.
            from scripts.demo_scalp import CONFIG
            cfg = json.loads(CONFIG.read_text(encoding='utf8'))
            configured = isinstance(cfg, dict) and cfg.get('enabled') is True and cfg.get('version') == ENGINE_VERSION
            running = configured and _autotrade_enabled()
            return {**result, 'enabled': running, 'authorized': configured,
                    'status': 'ready' if running else 'disabled'}
        _assert_live(env)
        current = _current_binding(env)
        result.update(current)
        saved = _load()['live'].get(current['account_scope'])
        if not isinstance(saved, dict) or saved.get('enabled') is not True:
            return result
        if (saved.get('confirmed') is not True or not isinstance(saved.get('actor'), str)
                or not saved['actor'].strip() or not isinstance(saved.get('record_id'), str)
                or not saved['record_id'] or type(saved.get('binding_version')) is not int
                or any(saved.get(key) != current[key] for key in BINDING_FIELDS)):
            return {**result, 'status': 'binding_changed'}
        running = _autotrade_enabled()
        return {**result, 'authorized': True, 'enabled': running,
                'status': 'ready' if running else 'paused', 'record_id': saved['record_id'],
                'confirmed_at': saved.get('confirmed_at'), 'actor': saved['actor']}
    except Exception as exc:
        # Never expose configuration/credential-bearing exception text to UI.
        return {**result, 'enabled': False, 'authorized': False, 'status': 'unavailable',
                'error_type': type(exc).__name__}


def enabled(env):
    return status(env).get('enabled') is True


def authorize_live(env, actor, *, expected_binding=None):
    """Admin-confirmed current LIVE only; actor is the authenticated server principal."""
    from scripts.config_lock import configuration_write
    from scripts.risk_policy import RiskRejected
    _assert_live(env)
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 200:
        raise RiskRejected('A server-authenticated administrator identity is required')
    with configuration_write(STATE_FILE):
        _assert_live(env)
        binding = _current_binding(env)
        if expected_binding is not None and any(expected_binding.get(k)!=binding.get(k) for k in BINDING_FIELDS):
            raise RiskRejected('Displayed LIVE account or strategy changed; refresh and confirm again')
        state = _load()
        previous = state['live'].get(binding['account_scope']) or {}
        active_record = previous.get('record_id') if previous.get('enabled') and previous.get('confirmed') and all(previous.get(k)==binding.get(k) for k in BINDING_FIELDS) else None
        if expected_binding is not None and expected_binding.get('record_id') != active_record:
            raise RiskRejected('LIVE consent changed after the confirmation view; refresh and confirm again')
        # Check again before persistence; callers must not authorize stale UI bindings.
        _assert_live(env)
        checked = _current_binding(env)
        if any(binding[key] != checked[key] for key in BINDING_FIELDS):
            raise RiskRejected('Minute-engine binding changed during confirmation')
        state['live'][binding['account_scope']] = {
            **{key: binding[key] for key in BINDING_FIELDS},
            'enabled': True, 'confirmed': True, 'actor': actor.strip(),
            'confirmed_at': time.time(), 'record_id': uuid.uuid4().hex}
        _save(state)
    return status(env)


def disable_live(env, *, expected_binding=None):
    """Revoke this current LIVE scope only; other scopes and DEMO remain untouched."""
    from scripts.config_lock import configuration_write
    _assert_live(env)
    with configuration_write(STATE_FILE):
        _assert_live(env)
        state = _load()
        previous = state['live'].get(env.identity)
        if expected_binding is not None:
            from scripts.risk_policy import RiskRejected
            current = _current_binding(env)
            if any(expected_binding.get(k)!=current.get(k) for k in BINDING_FIELDS):
                raise RiskRejected('Displayed LIVE account or strategy changed; refresh and confirm again')
            if expected_binding.get('record_id') != (previous or {}).get('record_id'):
                raise RiskRejected('LIVE authorization record changed; refresh and confirm again')
        state['live'][env.identity] = {**(previous if isinstance(previous, dict) else {}),
                                      **_identity(env), 'enabled': False, 'confirmed': False,
                                      'disabled_at': time.time()}
        _save(state)
    return status(env)
