"""Bounded, credential-free public OKX reads shared by all OKXQuant processes.

The sole POST allowed here computes indicators; orders/account endpoints cannot
enter this transport. The separately named Smart Money reader keeps its existing
CLI/auth semantics and a separate one-process gate. No CLI fallback for public
HTTP failures, no stale-on-error and no cached trading decisions.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / ".public-market"
BASE_URL = "https://www.okx.com"
HTTP_SLOTS = 3
INDICATOR_DEFAULTS = {"ADX": [14], "KDJ": [9, 3, 3], "BBWIDTH": [20, 2], "CMF": [20]}
# path -> (permitted query parameters, freshness seconds, minimum request gap)
POLICY = {
    "/api/v5/market/ticker": ({"instId"}, 2.0, .12),
    "/api/v5/market/books": ({"instId", "sz"}, 2.0, .12),
    "/api/v5/market/candles": ({"instId", "bar", "limit"}, 5.0, .08),
    "/api/v5/public/instruments": ({"instId", "instType"}, 30.0, .12),
    "/api/v5/public/funding-rate": ({"instId"}, 10.0, .12),
    "/api/v5/public/open-interest": ({"instId", "instType"}, 10.0, .12),
    "/api/v5/rubik/stat/contracts/long-short-account-ratio": ({"ccy", "period"}, 10.0, .45),
    "/api/v5/rubik/stat/taker-volume": ({"ccy", "instType", "period"}, 10.0, .45),
}
INDICATOR_PATH = "/api/v5/aigc/mcp/indicators"
_DEADLINE = ContextVar("public_market_deadline", default=None)
_OBSERVATIONS = ContextVar("public_market_observations", default=None)


class MarketDataError(RuntimeError):
    pass


class IncompleteIndicators(MarketDataError):
    def __init__(self, available):
        self.available = available
        super().__init__("Incomplete OKX indicator response")


def run_with_deadline(deadline, function, *args, **kwargs):
    token = _DEADLINE.set(deadline)
    try:
        return function(*args, **kwargs)
    finally:
        _DEADLINE.reset(token)


def _deadline(seconds):
    deadline = time.monotonic() + seconds
    outer = _DEADLINE.get()
    return min(deadline, outer) if outer is not None else deadline


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _directory():
    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return CACHE_DIR


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".market-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def file_lock(name, deadline):
    # Real OS locks: shared by threads/processes, released even on process death.
    import fcntl
    with (_directory() / ("lock-" + _digest(name))).open("a+") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MarketDataError("Public collection lock deadline exceeded")
                time.sleep(min(.02, max(0, deadline - time.monotonic())))
        try:
            if time.monotonic() >= deadline:
                raise MarketDataError("Public collection deadline exceeded")
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def http_slot(deadline):
    import fcntl
    handles = [(_directory() / f"http-slot-{n}").open("a+") for n in range(HTTP_SLOTS)]
    acquired = None
    try:
        while acquired is None:
            if time.monotonic() >= deadline:
                raise MarketDataError("Public HTTP concurrency deadline exceeded")
            for handle in handles:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = handle
                    break
                except BlockingIOError:
                    continue
            if acquired is None:
                time.sleep(.02)
        yield
    finally:
        if acquired is not None:
            fcntl.flock(acquired, fcntl.LOCK_UN)
        for handle in handles:
            handle.close()


def _rate_limit(path, gap, deadline):
    with file_lock(("rate", path), deadline) as handle:
        handle.seek(0)
        try:
            previous = float(handle.read())
        except ValueError:
            previous = 0
        now = time.monotonic()
        # Ignore a value from a previous boot whose monotonic clock is ahead.
        delay = max(0, previous + gap - now) if previous <= now else 0
        if time.monotonic() + delay >= deadline:
            raise MarketDataError("Public request rate deadline exceeded")
        if delay:
            time.sleep(delay)
        handle.seek(0)
        handle.truncate()
        handle.write(str(time.monotonic()))
        handle.flush()


def cached_value(key, ttl, loader, deadline, *, boundary_seconds=None):
    path = _directory() / (_digest(("v1", key)) + ".json")
    def read():
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            now = time.time()
            if (isinstance(entry, dict) and "value" in entry and entry.get("version") == 1 and 0 <= now - entry["fetched_at"] < ttl
                    and now < entry["expires_at"]):
                return entry
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None
    entry = read()
    if entry is None:
        with file_lock(("request", key), deadline):
            entry = read()
            if entry is None:
                started = time.time()
                value = loader()
                if time.monotonic() >= deadline:
                    raise MarketDataError("Public collection exceeded its total deadline")
                expiry = started + ttl
                if boundary_seconds:
                    expiry = min(expiry, (math.floor(started / boundary_seconds) + 1) * boundary_seconds)
                entry = {"version": 1, "fetched_at": started, "expires_at": expiry, "value": value}
                if ttl > 0:
                    atomic_json(path, entry)
    observations = _OBSERVATIONS.get()
    if observations is not None:
        observations["oldest_source_at"] = min(observations["oldest_source_at"], entry["fetched_at"])
    # No in-process mutable cache: consumers cannot poison another consumer.
    return entry["value"]


def _failure():
    observations = _OBSERVATIONS.get()
    if observations is not None:
        observations["failed_reads"] += 1


def observe_collection(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        observations = {"failed_reads": 0, "oldest_source_at": time.time()}
        token = _OBSERVATIONS.set(observations)
        try:
            result = function(*args, **kwargs)
            result["collection_quality"] = {**observations, "status": "fresh" if observations["failed_reads"] == 0 else "partial"}
            return result
        finally:
            _OBSERVATIONS.reset(token)
    return wrapped


def _wire(path, params, body, deadline, *, simulated=False):
    # Defense in depth: even accidental direct callers cannot issue a trade.
    if body is None:
        if path not in POLICY or set(params) - POLICY[path][0]:
            raise ValueError("Only allowlisted public GET requests are permitted")
    elif path != INDICATOR_PATH or params or set(body) - {"instId", "timeframes", "indicators", "backtestTime"}:
        raise ValueError("Only the read-only indicator POST is permitted")
    method = "POST" if body is not None else "GET"
    url = BASE_URL + path + (("?" + urlencode(sorted(params.items()))) if params else "")
    headers = {"User-Agent": "OKXQuant-Public-Market/1.0", "Content-Type": "application/json"}
    if simulated:
        headers["x-simulated-trading"] = "1"  # Public data source, not an auth header.
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method=method)
    with http_slot(deadline):
        remaining = min(4.0, deadline - time.monotonic())
        if remaining <= 0:
            raise MarketDataError("Public HTTP deadline exceeded")
        with urllib.request.urlopen(request, timeout=remaining) as response:
            value = json.loads(response.read(2_000_000).decode("utf-8"))
    if not isinstance(value, dict) or str(value.get("code")) != "0" or not isinstance(value.get("data"), list) or not value["data"]:
        raise MarketDataError("Public data unavailable or rejected; no stale fallback")
    return value


def _bar_seconds(bar):
    match = re.fullmatch(r"([1-9][0-9]*)(m|H|D)(?:utc)?", bar)
    if not match:
        raise ValueError("Unsupported public candle interval")
    return int(match[1]) * {"m": 60, "H": 3600, "D": 86400}[match[2]]


def get_json(url, timeout=10.0, *, simulated=False):
    """Compatibility reader for existing fixed OKX URLs; never accepts secrets."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "www.okx.com" or parsed.fragment or parsed.path not in POLICY:
            raise ValueError("Only allowlisted public OKX endpoints are permitted")
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        params = dict(pairs)
        allowed, ttl, gap = POLICY[parsed.path]
        if len(params) != len(pairs) or set(params) - allowed:
            raise ValueError("Unexpected public query parameter")
        deadline = _deadline(min(max(float(timeout), .01), 15.0))
        original_limit = None
        boundary = None
        if parsed.path.endswith("/candles"):
            original_limit = int(params.get("limit", "100"))
            if not 1 <= original_limit <= 300:
                raise ValueError("Invalid candle limit")
            params["limit"] = str(max(100, original_limit))
            params.setdefault("bar", "1m")
            boundary = _bar_seconds(params["bar"])
        def load():
            _rate_limit(parsed.path, gap, deadline)
            return _wire(parsed.path, params, None, deadline, simulated=simulated)
        value = cached_value(("public-market", bool(simulated), parsed.path, params), ttl, load, deadline, boundary_seconds=boundary)
        if original_limit is not None:
            value = {**value, "data": value["data"][:original_limit]}
        return value
    except Exception:
        _failure()
        raise


def candles(inst_id, bar, limit):
    return get_json(BASE_URL + "/api/v5/market/candles?" + urlencode({"instId": inst_id, "bar": bar, "limit": limit}))["data"]


def indicator_values(inst_id, bar="1H", *, backtest_time=None):
    """Same OKX 1.4.5 indicator service/defaults; batch four, no local approximation."""
    try:
        if not re.fullmatch(r"[A-Z0-9-]{3,64}", inst_id):
            raise ValueError("Invalid instrument")
        boundary = _bar_seconds(bar)
        simulated = _selected().simulated  # CLI publicPost inherits demo/live for indicators.
        body = {"instId": inst_id, "timeframes": [bar], "indicators": {
            key: {"paramList": params, "returnList": False} for key, params in INDICATOR_DEFAULTS.items()}}
        if backtest_time is not None:
            body["backtestTime"] = int(backtest_time)
        deadline = _deadline(12)
        def load():
            _rate_limit(INDICATOR_PATH, .45, deadline)
            value = _wire(INDICATOR_PATH, {}, body, deadline, simulated=simulated)
            result = value["data"][0]["data"][0]["timeframes"][bar]["indicators"]
            fields = {"ADX": "adx", "KDJ": "j", "BBWIDTH": "bbWidth", "CMF": "cmf"}
            available = {}
            for code, field in fields.items():
                try:
                    if result.get(code) and math.isfinite(float(result[code][0]["values"][field])):
                        available[code] = result[code]
                except (KeyError, TypeError, ValueError, IndexError):
                    pass
            if len(available) != len(fields):
                raise IncompleteIndicators(available)
            return result
        return cached_value(("public-indicators", simulated, body), 10.0, load, deadline, boundary_seconds=boundary)
    except Exception:
        _failure()
        raise


def available_indicators(inst_id, bar="1H", *, backtest_time=None):
    # Match the old independent CLI reads: one missing indicator must not discard
    # the other valid indicators. Partial batches remain uncached and observable.
    try:
        return indicator_values(inst_id, bar) if backtest_time is None else indicator_values(inst_id, bar, backtest_time=backtest_time)
    except IncompleteIndicators as exc:
        return exc.available


def _selected():
    try:
        from . import okx_runtime
    except ImportError:
        import okx_runtime
    return okx_runtime._FROZEN_ENVIRONMENT or okx_runtime.selected_environment()


def account_scope(selected=None):
    selected = selected or _selected()
    # Full credential rotation changes scope; no secret is persisted in the key.
    if not selected.configured:
        return None  # OAuth identity is not safely known: do not reuse its cache.
    return _digest((selected.mode, selected.api_key, selected.secret_key, selected.passphrase))


def _smart_money_news(ccys, connections, transport):
    """Use the explicitly bound information connection, never the trading key.

    Cache identity includes credential rotation. A failed/unbound news source
    does not fall back to DEMO, a different account or a global OAuth profile.
    """
    def signature(connection):
        return _digest((connection['id'], connection.get('generation', 0),
                        connection.get('site'), connection.get('auth_type'),
                        connection.get('credentials'), connection.get('capabilities', {}).get('live', {}).get('account_uid')))
    connection = connections.news_connection()
    identity = signature(connection)
    deadline = _deadline(12)
    def load():
        with file_lock('authenticated-smart-money-cli', deadline):
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise MarketDataError('Smart Money read deadline exceeded')
            rows = transport.request(connection, 'news', 'live', 'GET',
                '/api/v5/journal/smartmoney/overview', {'instCcyList': ','.join(ccys)},
                timeout=min(8.0, remaining))
            if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('ccy'), str) for row in rows):
                raise MarketDataError('Invalid Smart Money response')
            rows = [row for row in rows if row['ccy'] in ccys]
            if not rows: raise MarketDataError('Smart Money information source returned no observations')
            if len({row['ccy'] for row in rows}) != len(rows): raise MarketDataError('Duplicate Smart Money observations')
            return rows
    rows = cached_value(('smart-money-news-v1', identity, ccys), 30.0, load, deadline)
    if signature(connections.news_connection()) != identity:
        raise MarketDataError('Information connection changed during Smart Money read')
    return rows


def smart_money_overview(ccys):
    """Authenticated information READ; managed accounts use the news binding."""
    try:
        ccys = sorted(set(ccys))
        if not ccys or any(not re.fullmatch(r"[A-Z0-9]{1,20}", c) for c in ccys):
            raise ValueError("Invalid Smart Money universe")
        from okxquant_backend import account_connections, connection_transport
        if account_connections.load().get("managed"):
            return _smart_money_news(ccys, account_connections, connection_transport)
        # Legacy unmanaged installations retain their explicit CLI environment.
        selected = _selected()
        scope = account_scope(selected)
        deadline = _deadline(12)
        def load():
            with file_lock("authenticated-smart-money-cli", deadline):
                command = ["okx", "--" + selected.mode, "smartmoney", "signal-overview-by-filter", "--instCcyList", ",".join(ccys), "--json"]
                completed = subprocess.run(command, env=selected.cli_env(), capture_output=True, text=True,
                                           timeout=min(8.0, max(.01, deadline - time.monotonic())), shell=False)
                if completed.returncode != 0:
                    raise MarketDataError("Smart Money read failed")
                value = json.loads(completed.stdout)
                if not isinstance(value, dict) or not isinstance(value.get("data"), list):
                    raise MarketDataError("Invalid Smart Money response")
                return value["data"]
        return cached_value(("smart-money", scope, ccys), 30.0 if scope else 0.0, load, deadline)
    except Exception:
        _failure()
        raise


_SIGNAL_AS_OF = None

def begin_signal_frame(as_of=None):
    global _SIGNAL_AS_OF
    _SIGNAL_AS_OF = float(time.time() if as_of is None else as_of)
    return _SIGNAL_AS_OF

def signal_as_of():
    return _SIGNAL_AS_OF if _SIGNAL_AS_OF is not None else time.time()

def signal_json(url, timeout=10.0):
    from scripts.signal_data import closed_candles
    value = get_json(url, timeout)
    parsed=urlsplit(url)
    if not parsed.path.endswith('/candles'): return value
    params=dict(parse_qsl(parsed.query))
    return {**value, 'data': closed_candles(value['data'],params.get('bar','1m'),
        as_of_ms=int(signal_as_of()*1000),limit=int(params.get('limit','100'))),
        'as_of_ms':int(signal_as_of()*1000),'candle_contract':'closed-v1'}

def signal_candles(inst_id, bar, limit):
    return signal_json(BASE_URL + '/api/v5/market/candles?' + urlencode({'instId':inst_id,'bar':bar,'limit':limit}))['data']


def signal_indicators(inst_id, bar='1H'):
    # Anchor at a known CLOSED bar open. Even inclusive endpoint semantics cannot
    # admit the current unfinished bar. This can be conservatively one bar lagged.
    rows=signal_candles(inst_id,bar,2)
    return available_indicators(inst_id,bar,backtest_time=int(rows[0][0]))
