"""Account-wide, READ-ONLY algo snapshots with cross-process admission control.

Pending snapshots and optional history enrichment are read-only fixed endpoints. Write barriers only
invalidate snapshots; they never retry, delay on the read gate, or send orders.
"""
from __future__ import annotations
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import base64
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / 'data' / '.private-algos'
ENDPOINT = '/api/v5/trade/orders-algo-pending'
GAP_SECONDS = .30  # Conservative workspace-wide cap, shared across keys/modes.
MAX_ATTEMPTS = 3
MAX_PAGES = 10
MONITOR_TTL = 2.0
RISK_TTL = .75
LOG = logging.getLogger(__name__)


class AlgoReadError(RuntimeError):
    def __init__(self, category, attempts=0, code=0):
        self.category, self.attempts, self.code = category, attempts, code
        label = {'rate_limited':'OKX 限流','rate_limit_cooldown':'等待限流冷却','deferred_for_risk_check':'已让出读取机会给风控核验','retry_after_exceeds_budget':'限流等待超过核验预算','deadline_exceeded':'核验预算已耗尽','network_error':'网络暂不可用','coordination_unavailable':'共享读取协调暂不可用'}.get(category, category)
        super().__init__(f'保护单核验未知：{label} [{category}]，尝试 {attempts} 次' + (f'，代码 {code}' if code else ''))


class _WireError(Exception):
    def __init__(self, category, code=0, retryable=False, retry_after=None):
        self.category, self.code, self.retryable, self.retry_after = category, code, retryable, retry_after


def _dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return STATE_DIR


def _boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def _scope(env):
    seed = [env.mode, env.base_url, env.api_key, env.secret_key, env.passphrase]
    return hashlib.sha256(json.dumps(seed).encode()).hexdigest()


def _read(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _atomic(path, value):
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=_dir())
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def _epoch(scope):
    return str(_read(_dir() / (scope + '.epoch.json'), 'initial'))


def _invalidate(scope):
    # UUID replacement is atomic and needs no network/read admission lock.
    _atomic(_dir() / (scope + '.epoch.json'), uuid.uuid4().hex)


@contextmanager
def _marker(prefix, deadline=None):
    """Publish only after acquiring the OS lock; a crashed owner's lock is freed."""
    import fcntl
    fd, tmp = tempfile.mkstemp(prefix='.publishing-', dir=_dir())
    target = _dir() / (prefix + uuid.uuid4().hex + '.lock')
    handle = os.fdopen(fd, 'w+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        json.dump({'boot': _boot(), 'deadline': deadline}, handle)
        handle.flush()
        os.replace(tmp, target)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
        for path in (target, Path(tmp)):
            try: path.unlink()
            except FileNotFoundError: pass


def _active(prefix):
    import fcntl
    for path in _dir().glob(prefix + '*.lock'):
        try:
            with path.open('r+') as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    data = _read(path, {})
                    if not isinstance(data, dict): return True
                    if data.get('boot') == _boot() and (data.get('deadline') is None or time.monotonic() < data['deadline']):
                        return True
                else:
                    # Stale marker, including one left by SIGKILL.
                    try: path.unlink()
                    except FileNotFoundError: pass
        except FileNotFoundError:
            continue
    return False


def _best_effort_invalidate(scope):
    try:
        _invalidate(scope)
    except OSError:
        # Cache bookkeeping must never prevent an emergency close being sent.
        try: (_dir() / (scope + '.snapshot.json')).unlink(missing_ok=True)
        except OSError: pass
        LOG.error('Algo cache invalidation unavailable; no write retry was added')


@contextmanager
def algo_mutation(env):
    """Barrier around ONE existing write attempt; never serialize/retry writes."""
    scope = _scope(env)
    marker = _marker('write-' + scope + '-')
    active = False
    try:
        marker.__enter__()
        active = True
    except (OSError, ImportError):
        LOG.error('Algo write marker unavailable; business write is not suppressed')
    _best_effort_invalidate(scope)
    try:
        yield
    finally:
        _best_effort_invalidate(scope)
        if active:
            try: marker.__exit__(None, None, None)
            except (OSError, ImportError): LOG.error('Algo write marker cleanup unavailable')


def command_barrier(command, env):
    args = shlex.split(command) if isinstance(command, str) else list(command)
    # Fixed business command verbs, not substring matching of user/order IDs.
    for i, arg in enumerate(args):
        if arg != 'swap': continue
        verb = args[i + 1:i + 3]
        if verb and (verb[0] in {'place', 'close', 'cancel', 'amend'} or
                     len(verb) == 2 and verb[0] == 'algo' and verb[1] in {'place', 'amend', 'cancel'}):
            return algo_mutation(env)
        break
    return nullcontext()


def _check(deadline, priority):
    if time.monotonic() >= deadline:
        raise AlgoReadError('deadline_exceeded')
    if priority == 'monitor' and _active('risk-'):
        raise AlgoReadError('deferred_for_risk_check')


def _sleep(delay, deadline, priority):
    end = time.monotonic() + max(0, delay)
    if end >= deadline:
        raise AlgoReadError('retry_after_exceeds_budget')
    while time.monotonic() < end:
        _check(deadline, priority)
        remaining=end-time.monotonic()
        if remaining<=0:break
        time.sleep(min(.03, remaining))


@contextmanager
def _turn(deadline, priority):
    import fcntl
    with (_dir() / 'read-admission.lock').open('a+') as f:
        while True:
            _check(deadline, priority)
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                _sleep(.03, deadline, priority)
        try:
            _check(deadline, priority)
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _state():
    state = _read(_dir() / 'admission.json', {})
    return state if isinstance(state, dict) and state.get('boot') == _boot() else {'boot': _boot()}


def _reserve(deadline, priority):
    state = _state()
    until = max(state.get('next', 0), state.get('blocked_until', 0))
    if until >= deadline:
        raise AlgoReadError('rate_limit_cooldown' if state.get('blocked_until', 0) >= deadline else 'deadline_exceeded', code=429 if state.get('blocked_until', 0) >= deadline else 0)
    if until > time.monotonic(): _sleep(until - time.monotonic(), deadline, priority)
    _check(deadline, priority)
    state['next'] = time.monotonic() + GAP_SECONDS
    state['last_priority'] = priority
    _atomic(_dir() / 'admission.json', state)


def _read_finished():
    """Called under _turn after each wire read, including errors.

    Reserving before an atomic file write cannot bound real request spacing:
    a descheduled first writer may send much later than its reservation. Keep
    the OS gate until this post-read anchor is durable, instead of adding jitter guesses.
    """
    state=_state()
    state['next']=max(state.get('next',0),time.monotonic()+GAP_SECONDS)
    _atomic(_dir()/'admission.json',state)


def _retry_delay(value, attempt):
    if value:
        try: seconds = float(value)
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None: date = date.replace(tzinfo=timezone.utc)
                seconds = date.timestamp() - time.time()
            except (ValueError, TypeError, OverflowError): seconds = -1
        if math.isfinite(seconds) and seconds >= 0: return max(1., seconds)
    return float(2 ** (attempt - 1))


def _cooldown(delay):
    state = _state()
    state['blocked_until'] = max(state.get('blocked_until', 0), time.monotonic() + delay)
    _atomic(_dir() / 'admission.json', state)


def _signed_page(env, params, deadline):
    if env.base_url.rstrip('/') != 'https://www.okx.com':
        raise _WireError('invalid_host')
    request_path = ENDPOINT + '?' + urllib.parse.urlencode(params)
    timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    signature = base64.b64encode(hmac.new(env.secret_key.encode(), (timestamp + 'GET' + request_path).encode(), hashlib.sha256).digest()).decode()
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0 OKXQuant-Private-Read/1.0',
               'OK-ACCESS-KEY': env.api_key, 'OK-ACCESS-SIGN': signature,
               'OK-ACCESS-TIMESTAMP': timestamp, 'OK-ACCESS-PASSPHRASE': env.passphrase}
    if env.simulated: headers['x-simulated-trading'] = '1'
    try:
        req = urllib.request.Request(env.base_url + request_path, headers=headers, method='GET')
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise _WireError('deadline_exceeded')
        with urllib.request.urlopen(req, timeout=min(2., remaining)) as response:
            data = json.loads(response.read(2_000_000).decode('utf-8'))
    except urllib.error.HTTPError as exc:
        status = exc.code
        retry_after = exc.headers.get('Retry-After') if exc.headers else None
        exc.close()  # Never log raw private response bodies/headers.
        raise _WireError('rate_limited' if status == 429 else 'http_error', status,
                         status in {408, 429, 500, 502, 503, 504}, retry_after) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        reason = getattr(exc, 'reason', exc)
        transient = not isinstance(reason, ssl.SSLError) and isinstance(reason, (TimeoutError, ConnectionError, socket.gaierror))
        raise _WireError('network_error', retryable=transient) from None
    except (ValueError, UnicodeError):
        raise _WireError('invalid_json') from None
    if not isinstance(data, dict): raise _WireError('invalid_response')
    code = str(data.get('code', ''))
    if code != '0':
        raise _WireError('rate_limited' if code in {'429','50011','50061'} else 'api_error',
                         code, code in {'429','50011','50061'})
    rows = data.get('data')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise _WireError('invalid_response')
    return rows


def _oauth_page(env, params, deadline):
    command = ['okx', '--' + env.mode, 'swap', 'algo', 'orders', '--ordType', params['ordType'], '--limit', '100', '--json']
    if params.get('after'): command += ['--after', params['after']]
    cli_env = env.cli_env()
    for key in ('OKX_API_KEY','OKX_SECRET_KEY','OKX_PASSPHRASE'): cli_env.pop(key, None)
    try:
        result = subprocess.run(command, env=cli_env, shell=False, capture_output=True, text=True,
                                timeout=max(.01, min(2., deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        raise _WireError('network_error', retryable=True) from None
    if result.returncode != 0:
        limited = bool(re.search(r'\b(?:429|50011|50061)\b', result.stderr + result.stdout))
        raise _WireError('rate_limited' if limited else 'cli_error', 429 if limited else 0, limited)
    try: rows = json.loads(result.stdout)
    except ValueError: raise _WireError('invalid_json') from None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise _WireError('invalid_response')
    return rows


def _fetch_all(env, deadline, priority):
    result, seen = [], {}
    first_started = None
    # SDK CLI accepts single enum types: no implicit 3-type fan-out for OAuth.
    for kind in (('conditional,oco',) if env.configured else ('conditional','oco')):
        after = None
        for page in range(MAX_PAGES):
            _reserve(deadline, priority)
            if first_started is None: first_started = time.monotonic()
            params = {'instType': 'SWAP', 'ordType': kind, 'limit': '100'}
            if after: params['after'] = after
            try:
                rows = _signed_page(env, params, deadline) if env.configured else _oauth_page(env, params, deadline)
            finally:
                _read_finished()
            for row in rows:
                algo_id = str(row.get('algoId') or '')
                if not algo_id or not row.get('instId'): raise _WireError('invalid_order_identity')
                if algo_id in seen:
                    if seen[algo_id] != row:
                        # Exchange-side triggers/amendments need not change our
                        # local epoch. Do not keep an earlier green duplicate.
                        raise _WireError('snapshot_changed_during_read', retryable=True)
                else:
                    result.append(row)
                    seen[algo_id] = row
            if len(rows) < 100: break
            cursor = str(rows[-1].get('algoId') or '')
            if not cursor or cursor == after: raise _WireError('pagination_incomplete')
            after = cursor
        else: raise _WireError('pagination_incomplete')
    return result, first_started


def _cached(env, scope, ttl, priority='risk'):
    if not env.configured or _active('write-' + scope + '-'): return None
    data = _read(_dir() / (scope + '.snapshot.json'))
    try:
        if (data['boot'] == _boot() and data['epoch'] == _epoch(scope)
                and (priority != 'risk' or data.get('priority') == 'risk')
                and data['complete'] is True and isinstance(data['orders'], list)
                and all(isinstance(r, dict) and r.get('instId') and r.get('algoId') for r in data['orders'])
                and 0 <= time.monotonic() - data['started_mono'] <= ttl):
            if data['epoch'] == _epoch(scope) and not _active('write-' + scope + '-'):
                return data['orders']
    except (TypeError, KeyError): pass
    return None


def _read_algo_orders(env, *, priority='risk', force=False, timeout=None):
    """Return a complete current-account snapshot, or raise UNKNOWN.

    At most three complete attempts, including pagination, share one deadline.
    Confirmed empty is []; unavailable/incomplete is never converted to [].
    """
    if priority not in {'risk','monitor'}: raise ValueError('Invalid private read priority')
    budget = float(timeout if timeout is not None else (10 if priority == 'risk' else 3))
    if not math.isfinite(budget) or budget <= 0: raise ValueError('Invalid private read timeout')
    deadline = time.monotonic() + min(budget, 10.)
    scope = _scope(env)
    ttl = RISK_TTL if priority == 'risk' else MONITOR_TTL
    # A read-only/unwritable coordinator must not serve a previously green cache.
    with (_dir() / 'read-admission.lock').open('a+'):
        pass
    cached = None if force else _cached(env, scope, ttl, priority)
    if cached is not None: return cached
    attempts, last_code = 0, 0
    marker = _marker('risk-', deadline) if priority == 'risk' else nullcontext()
    try:
        with marker, _turn(deadline, priority):
            cached = None if force else _cached(env, scope, ttl, priority)
            if cached is not None: return cached
            for attempts in range(1, MAX_ATTEMPTS + 1):
                while _active('write-' + scope + '-'):
                    _sleep(.03, deadline, priority)
                epoch = _epoch(scope)
                started = time.monotonic()
                try:
                    orders, started = _fetch_all(env, deadline, priority)
                except _WireError as exc:
                    last_code = exc.code
                    LOG.warning('Private algo read priority=%s attempt=%s category=%s code=%s', priority, attempts, exc.category, exc.code)
                    delay = _retry_delay(exc.retry_after, attempts)
                    if exc.category == 'rate_limited': _cooldown(delay)
                    if not exc.retryable or attempts == MAX_ATTEMPTS:
                        raise AlgoReadError(exc.category, attempts, exc.code) from None
                    _sleep(delay, deadline, priority)
                    continue
                _check(deadline, 'risk')  # Accept completed response, but risk never reuses monitor-origin cache.
                if epoch != _epoch(scope) or _active('write-' + scope + '-'):
                    if attempts == MAX_ATTEMPTS: raise AlgoReadError('snapshot_changed_during_read', attempts)
                    continue
                if env.configured:
                    _atomic(_dir() / (scope + '.snapshot.json'), {'complete': True, 'orders': orders,
                            'boot': _boot(), 'epoch': epoch, 'priority': priority, 'started_mono': started, 'checked_at': time.time()})
                return orders
    except AlgoReadError as exc:
        if not exc.attempts: exc = AlgoReadError(exc.category, attempts, exc.code or last_code)
        raise exc from None
    raise AlgoReadError('unavailable', attempts, last_code)


def read_algo_orders(env, *, priority='risk', force=False, timeout=None):
    try:
        return _read_algo_orders(env, priority=priority, force=force, timeout=timeout)
    except (OSError, TypeError, KeyError, ImportError):
        raise AlgoReadError('coordination_unavailable') from None


def orders_for_instrument(orders, inst_id):
    return [row for row in orders if row.get('instId') == inst_id]


def read_algo_history(env, *, ord_type, timeout=2.0):
    """Optional one-page history enrichment; same monitor admission, no write/retry.

    History-specific failures do not poison the risk endpoint's shared cooldown.
    """
    if ord_type not in ('oco','conditional'):raise ValueError('Unsupported history type')
    if not env.configured:raise AlgoReadError('history_requires_static_key')
    if env.base_url.rstrip('/')!='https://www.okx.com':raise AlgoReadError('invalid_host')
    deadline=time.monotonic()+min(2.,max(.1,float(timeout)))
    try:
        with _turn(deadline,'monitor'):
            _reserve(deadline,'monitor');_check(deadline,'monitor')
            from okxquant_backend.okx_trade_service import _request
            try:
                rows=_request('GET','/api/v5/trade/orders-algo-history',
                    {'ordType':ord_type,'state':'effective','instType':'SWAP','limit':'100'},env,
                    timeout=max(.1,deadline-time.monotonic()))
            finally:
                _read_finished()
            if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise AlgoReadError('invalid_history_response')
            return rows
    except AlgoReadError:raise
    except Exception as exc:raise AlgoReadError('history_unavailable',code=getattr(exc,'code',0) or 0) from None
