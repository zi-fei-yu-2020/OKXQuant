"""Explicit connection transports. No automatic auth/environment fallback or write retry.

OAuth matches official CLI 1.4.5: native okx-auth token on fd3, then Bearer HTTPS.
Tokens stay in process memory; identity is checked with that same token before use.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OAUTH_ROOT = ROOT / 'data' / 'oauth-connections'
AUTH_BINARY = Path.home() / '.okx' / 'bin' / ('okx-auth.exe' if os.name == 'nt' else 'okx-auth')
NEWS_PATHS = {'/api/v5/orbit/news-search', '/api/v5/orbit/currency-sentiment-query',
              '/api/v5/journal/smartmoney/overview'}
READ_PATHS = {'/api/v5/account/config', '/api/v5/account/balance', '/api/v5/account/positions',
 '/api/v5/account/positions-history', '/api/v5/account/bills', '/api/v5/account/bills-archive',
 '/api/v5/account/leverage-info', '/api/v5/trade/orders-pending', '/api/v5/trade/orders-history',
 '/api/v5/trade/orders-history-archive', '/api/v5/trade/order', '/api/v5/trade/fills',
 '/api/v5/trade/fills-history', '/api/v5/trade/orders-algo-pending', '/api/v5/trade/orders-algo-history'}
WRITE_PATHS = {'/api/v5/trade/order', '/api/v5/trade/cancel-order', '/api/v5/trade/cancel-batch-orders',
 '/api/v5/trade/close-position', '/api/v5/trade/order-algo', '/api/v5/trade/cancel-algos',
 '/api/v5/trade/amend-algos', '/api/v5/trade/amend-order', '/api/v5/account/set-leverage'}
_CACHE = {}
_LOCK = threading.RLock()


class ConnectionError(RuntimeError):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


def oauth_home(connection_id):
    import re
    if not re.fullmatch(r'[a-f0-9]{32}', connection_id):
        raise ConnectionError('invalid_connection_id')
    return OAUTH_ROOT / connection_id


def isolated_env(connection_id):
    home = oauth_home(connection_id)
    if home.is_symlink(): raise ConnectionError('oauth_directory_is_symlink')
    # Do not inherit credentials, CLI profiles, proxies with embedded secrets, or session keyrings.
    keep = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'SYSTEMROOT', 'WINDIR', 'TMPDIR')
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home/'.config'),
               XDG_DATA_HOME=str(home/'.local'/'share'), XDG_CACHE_HOME=str(home/'.cache'),
               OKX_AUTH_BIN=str(AUTH_BINARY), OKX_DEMO='0')
    return env


def auth_command(connection_id, action, site='global'):
    if action not in {'login', 'status', 'logout'} or site not in {'global', 'eea', 'us', 'tr'}:
        raise ConnectionError('invalid_auth_action')
    if not AUTH_BINARY.is_file():
        raise ConnectionError('oauth_binary_missing')
    home = oauth_home(connection_id)
    if home.is_symlink(): raise ConnectionError('oauth_directory_is_symlink')
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    args = [str(AUTH_BINARY), action]
    if action == 'login': args += ['--manual', '--site', site]
    if action == 'status': args += ['--json']
    try:
        result = subprocess.run(args, env=isolated_env(connection_id), shell=False, capture_output=True,
                                text=True, timeout=30 if action == 'login' else 8)
    except (OSError, subprocess.SubprocessError):
        raise ConnectionError('oauth_auth_process_unavailable') from None
    if result.returncode: raise ConnectionError('oauth_auth_process_failed')
    try: return json.loads(result.stdout) if result.stdout.strip() else {'status': 'local_logout_completed'}
    except ValueError: raise ConnectionError('oauth_invalid_auth_response') from None


def native_token(connection_id):
    if os.name != 'posix': raise ConnectionError('oauth_transport_requires_linux_or_wsl')
    if not AUTH_BINARY.is_file(): raise ConnectionError('oauth_binary_missing')
    read_fd, write_fd = os.pipe()
    child = None
    try:
        # Trusted exec helper maps inherited fd to official native helper's fd3 without
        # using preexec_fn in the multithreaded backend. Nothing is sent to stdout/logs.
        code = 'import os,sys;os.dup2(int(sys.argv[2]),3);os.set_inheritable(3,True);os.execve(sys.argv[1],[sys.argv[1],"token"],dict(os.environ))'
        child = subprocess.Popen([sys.executable, '-c', code, str(AUTH_BINARY), str(write_fd)],
                                 env=isolated_env(connection_id), pass_fds=(write_fd,),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write_fd); write_fd = -1
        data = bytearray(); deadline = time.monotonic() + 8
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining): raise ConnectionError('oauth_token_timeout')
                chunk = os.read(read_fd, 4096)
                if not chunk: break
                data.extend(chunk)
                if len(data) > 16384: raise ConnectionError('oauth_token_response_too_large')
        child.wait(timeout=max(.1, deadline-time.monotonic()))
        token = data.decode('utf8').strip()
        if child.returncode or not token or '\n' in token or '\r' in token:
            raise ConnectionError('oauth_token_unavailable')
        return token
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise ConnectionError('oauth_token_unavailable') from None
    finally:
        if child is not None and child.poll() is None: child.kill(); child.wait()
        os.close(read_fd)
        if write_fd >= 0: os.close(write_fd)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectionError('redirect_forbidden')


def _http(method, path, params, headers, timeout):
    query = urllib.parse.urlencode(params or {}) if method == 'GET' else ''
    body = json.dumps(params or {}, separators=(',', ':'), ensure_ascii=False).encode() if method == 'POST' else None
    req = urllib.request.Request('https://www.okx.com'+path+('?' + query if query else ''), data=body,
                                 headers={'Content-Type':'application/json', 'Accept':'application/json', 'User-Agent':'OKXQuant-Quantum-Trader/0.1.0', **headers}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=min(10, max(.1, timeout))) as response:
            payload = json.loads(response.read(4*1024*1024).decode('utf8'))
    except urllib.error.HTTPError as exc: raise ConnectionError('http_'+str(exc.code)) from None
    except (OSError, ValueError): raise ConnectionError('upstream_unavailable') from None
    if not isinstance(payload, dict) or str(payload.get('code')) != '0':
        raise ConnectionError('okx_'+str(payload.get('code', 'invalid'))[:20] if isinstance(payload, dict) else 'invalid_response')
    rows = payload.get('data')
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ConnectionError('invalid_response')
    if any(str(r.get('sCode', '0')) != '0' for r in rows):
        raise ConnectionError('business_request_rejected')
    return rows


def credential_headers(connection, mode, method, path, params):
    if connection['auth_type'] == 'oauth':
        key = (connection['id'], connection.get('generation', 0), mode)
        with _LOCK:
            cached = _CACHE.get(key)
            if cached and time.monotonic()-cached[0] < 20:
                token = cached[1]
            else:
                token = native_token(connection['id'])
                _CACHE[key] = (time.monotonic(), token)
        return {'Authorization': 'Bearer '+token, **({'x-simulated-trading':'1'} if mode == 'demo' else {})}
    if connection['auth_type'] != 'api_key' or connection.get('mode') != mode:
        raise ConnectionError('credential_environment_mismatch')
    credentials = connection.get('credentials', {})
    if not all(credentials.get(k) for k in ('api_key', 'secret_key', 'passphrase')):
        raise ConnectionError('credentials_incomplete')
    timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    query = urllib.parse.urlencode(params or {}) if method == 'GET' else ''
    body = json.dumps(params or {}, separators=(',', ':'), ensure_ascii=False) if method == 'POST' else ''
    signed = timestamp+method+path+('?' + query if query else '')+body
    signature = base64.b64encode(hmac.new(credentials['secret_key'].encode(), signed.encode(), hashlib.sha256).digest()).decode()
    return {'OK-ACCESS-KEY':credentials['api_key'], 'OK-ACCESS-SIGN':signature,
            'OK-ACCESS-PASSPHRASE':credentials['passphrase'], 'OK-ACCESS-TIMESTAMP':timestamp,
            **({'x-simulated-trading':'1'} if mode == 'demo' else {})}


def request(connection, purpose, mode, method, path, params=None, *, timeout=8, verify_identity=True):
    if mode not in {'demo','live'} or purpose not in {'trade','news','probe'}:
        raise ConnectionError('invalid_connection_purpose')
    if connection.get('site') != 'global': raise ConnectionError('regional_transport_not_verified')
    if method not in {'GET','POST'}: raise ConnectionError('method_not_allowed')
    if purpose == 'news':
        if mode != 'live' or method != 'GET' or path not in NEWS_PATHS:
            raise ConnectionError('news_is_read_only_regular_market')
    elif path not in (READ_PATHS if method == 'GET' else WRITE_PATHS):
        raise ConnectionError('endpoint_not_allowed')
    if method == 'POST':
        if purpose != 'trade': raise ConnectionError('probe_is_read_only')
        if connection['auth_type'] == 'oauth':
            # Transport exists, but no live write certification is inferred from read probes.
            raise ConnectionError('oauth_write_capability_not_validated')
    headers = credential_headers(connection, mode, method, path, params)
    expected = connection.get('capabilities', {}).get(mode, {}).get('account_uid')
    if verify_identity and connection['auth_type'] == 'oauth':
        if not expected: raise ConnectionError('oauth_identity_not_verified')
        # Use exactly the same token for identity proof and request: no global auth fallback.
        rows = _http('GET', '/api/v5/account/config', {}, headers, timeout)
        if len(rows) != 1 or str(rows[0].get('uid') or '') != expected:
            invalidate(connection['id']); raise ConnectionError('oauth_account_changed')
    if method=='GET' and path in {'/api/v5/trade/orders-algo-pending','/api/v5/trade/orders-algo-history'}:
        from scripts import algo_reader
        deadline=time.monotonic()+timeout
        try:
            with algo_reader._turn(deadline,'monitor'):
                algo_reader._reserve(deadline,'monitor');algo_reader._check(deadline,'monitor')
                return _http(method,path,params,headers,max(.1,deadline-time.monotonic()))
        except (algo_reader.AlgoReadError,OSError):
            raise ConnectionError('protected_read_admission_unavailable') from None
    return _http(method, path, params, headers, timeout)


def invalidate(connection_id):
    with _LOCK:
        for key in list(_CACHE):
            if key[0] == connection_id: _CACHE.pop(key, None)
