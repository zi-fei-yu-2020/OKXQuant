"""Read-only macro feeds: bounded transport, explicit provenance and no trading side effects.

Daily Treasury par yields are NOT intraday rates or the Fed policy rate. Official
calendars contain schedules, not consensus/actual values. FMP requires an entitled
key; index identity is verified against its index catalogue (no ETF/DXY proxy).
"""
from __future__ import annotations
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE_FILE = ROOT / 'data' / 'macro_market.json'
VERSION = 'macro-feeds-v1'
UTC = timezone.utc
TREASURY_URL = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml'
CALENDAR_URLS = {
    'bea': 'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics',
    'bls': 'https://www.bls.gov/schedule/news_release/bls.ics',
}
FMP_BASE = 'https://financialmodelingprep.com/stable/'
SOURCES = ('treasury', 'bea', 'bls', 'fmp_indices', 'fmp_calendar')
REFRESH_SECONDS = {'treasury': 3600, 'bea': 3600, 'bls': 3600, 'fmp_indices': 900, 'fmp_calendar': 900}
TTL_SECONDS = {'treasury': 86400, 'bea': 86400, 'bls': 86400, 'fmp_indices': 1800, 'fmp_calendar': 3600}
IMPORTANT_EVENT = re.compile(r'consumer price|producer price|employment situation|non.?farm|payroll|unemployment|gross domestic|\bgdp\b|personal income|personal consumption|\bpce\b|\bcpi\b|\bppi\b|fomc|fed interest|interest rate decision|retail sales', re.I)
DXY_NAMES = {'us dollar index', 'u s dollar index', 'us dollar index dxy', 'u s dollar index dxy', 'ice us dollar index', 'ice u s dollar index', 'dollar index'}


class FeedError(Exception):
    """Only a safe, fixed error code may leave a provider adapter."""


def number(value):
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_cache(path=None):
    try:
        target = Path(path or CACHE_FILE)
        if target.stat().st_size > 4_000_000:
            return {}
        value = json.loads(target.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) and value.get('version') == VERSION else {}
    except (OSError, ValueError, TypeError):
        return {}


def fetch(url, *, params=None, deadline=None, session=None):
    """Fixed allowlist, no redirects or retries, byte and total-time ceilings.

    Exception text, request URLs (which can contain credentials), bodies and headers
    must never enter logs, receipts, cache errors or API responses.
    """
    if url not in {TREASURY_URL, *CALENDAR_URLS.values(), *(FMP_BASE + p for p in ('index-list', 'quote', 'economic-calendar'))}:
        raise FeedError('disallowed_endpoint')
    deadline = deadline or (time.monotonic() + 15)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FeedError('deadline')
    try:
        with (session or requests).get(url, params=params, stream=True, allow_redirects=False,
                headers={'User-Agent': 'OKXQuant-MacroContext/1.0', 'Accept': 'application/json, text/calendar, application/xml, text/xml'},
                timeout=(min(4, remaining), min(8, remaining))) as response:
            if response.status_code != 200:
                code = {401: 'authentication_failed', 402: 'subscription_required', 403: 'access_denied', 429: 'rate_limited'}.get(response.status_code, 'http_error')
                raise FeedError(code)
            chunks = []; size = 0
            for chunk in response.iter_content(16384):
                if time.monotonic() > deadline:
                    raise FeedError('deadline')
                size += len(chunk)
                if size > 2_000_000:
                    raise FeedError('response_too_large')
                chunks.append(chunk)
            return b''.join(chunks).decode('utf-8-sig')
    except FeedError:
        raise
    except requests.Timeout:
        raise FeedError('timeout') from None
    except (requests.RequestException, UnicodeError):
        raise FeedError('transport_error') from None


def fmp_json(endpoint, key, *, params=None, deadline=None, getter=fetch):
    text = getter(FMP_BASE + endpoint, params={**(params or {}), 'apikey': key}, deadline=deadline)
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise FeedError('invalid_json') from None
    # FMP may return an error document with HTTP 200. Never call it empty data.
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise FeedError('provider_error_payload')
    return payload


def parse_treasury(text, now):
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise FeedError('invalid_xml')
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        raise FeedError('invalid_xml') from None
    today = datetime.fromtimestamp(now, ZoneInfo('America/New_York')).date()
    rows = []
    for properties in root.iter():
        if properties.tag.split('}')[-1] != 'properties':
            continue
        fields = {node.tag.split('}')[-1]: node.text for node in properties}
        try:
            day = datetime.fromisoformat(fields.get('NEW_DATE') or '').date()
        except ValueError:
            continue
        if day > today:
            continue
        y2, y10 = number(fields.get('BC_2YEAR')), number(fields.get('BC_10YEAR'))
        if y2 is None or y10 is None or not (-5 <= y2 <= 50 and -5 <= y10 <= 50):
            continue
        rows.append({'observation_date': day.isoformat(), 'us2y_pct': y2, 'us10y_pct': y10,
                     'spread_10y_2y_bp': round((y10-y2)*100, 4)})
    rows.sort(key=lambda row: row['observation_date'])
    if not rows:
        raise FeedError('no_valid_observations')
    latest = rows[-1]
    previous = next((r for r in reversed(rows[:-1]) if r['observation_date'] < latest['observation_date']), None)
    if previous:
        latest.update({'previous_observation_date': previous['observation_date'],
                       'us2y_change_bp': round((latest['us2y_pct']-previous['us2y_pct'])*100, 4),
                       'us10y_change_bp': round((latest['us10y_pct']-previous['us10y_pct'])*100, 4)})
    return {**latest, 'source': 'US Treasury', 'frequency': 'daily', 'unit': 'percent; changes/spread in basis points',
            'semantics': 'daily_par_yield_curve_not_intraday_quote_or_fed_policy_rate', 'published_at': None}


def localize(naive, zone):
    """Reject ambiguous/nonexistent wall times rather than silently choosing a fold."""
    tz = ZoneInfo(zone)
    candidates = set()
    for fold in (0, 1):
        dt = naive.replace(tzinfo=tz, fold=fold)
        if dt.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == naive:
            candidates.add(dt.timestamp())
    if len(candidates) != 1:
        raise ValueError('ambiguous_or_nonexistent_time')
    return datetime.fromtimestamp(candidates.pop(), tz)


def parse_ics(text, source, now):
    """Bounded one-off agency calendar parser; never guess floating/all-day times."""
    if 'BEGIN:VCALENDAR' not in text or 'END:VCALENDAR' not in text:
        raise FeedError('invalid_calendar')
    # RFC5545 line unfolding. Some agency output includes blank separator lines.
    lines = []
    for line in text.replace('\r\n', '\n').split('\n'):
        if not line:
            continue
        if line.startswith((' ', '\t')) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    entries = []; row = None; skipped = 0; coverage_end = None
    for line in lines:
        if line == 'BEGIN:VEVENT':
            row = {}
        elif line == 'END:VEVENT' and row is not None:
            try:
                if 'RRULE' in row or row.get('STATUS', ('', ''))[1] == 'CANCELLED':
                    raise ValueError('unsupported recurrence or cancelled')
                header, value = row['DTSTART']
                if value.endswith('Z'):
                    dt = datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)
                else:
                    tzmatch = re.search(r'(?:^|;)TZID=([^;:]+)', header)
                    if not tzmatch or tzmatch[1] not in {'America/New_York', 'US/Eastern', 'UTC'}:
                        raise ValueError('unverified time zone')
                    dt = localize(datetime.strptime(value, '%Y%m%dT%H%M%S'), tzmatch[1])
                at = dt.timestamp()
                coverage_end = max(coverage_end or at, at)
                title = row.get('SUMMARY', ('', ''))[1].replace('\\,', ',').replace('\\;', ';').replace('\\n', ' ')[:200]
                if title and now-86400 <= at <= now+7*86400:
                    entries.append({'id': digest([source, row.get('UID', ('', title))[1], at])[:24],
                        'source': source.upper(), 'event': title, 'scheduled_at': at, 'country': 'US',
                        'importance': 'high' if IMPORTANT_EVENT.search(title) else 'other',
                        'actual': None, 'estimate': None, 'previous': None, 'unit': None,
                        'values_status': 'schedule_only', 'time_basis': 'explicit_ics_timezone'})
            except (ValueError, KeyError, OverflowError):
                skipped += 1
            row = None
        elif row is not None and ':' in line:
            header, value = line.split(':', 1)
            row[header.split(';')[0]] = (header, value)
    if coverage_end is None:
        raise FeedError('no_verified_event_times')
    entries.sort(key=lambda e: (e['importance'] != 'high', e['scheduled_at']))
    return {'events': entries[:24], 'coverage_end': coverage_end, 'skipped_events': skipped,
            'coverage': 'agency_only_not_complete_macro_calendar', 'values_status': 'schedule_only'}


def resolve_symbols(rows):
    known = {row.get('symbol'): row for row in rows if isinstance(row.get('symbol'), str)}
    symbols = {}
    for name, symbol in (('sp500', '^GSPC'), ('nasdaq_composite', '^IXIC')):
        if symbol in known:
            symbols[name] = symbol
    candidates = []
    for row in rows:
        name = re.sub(r'[^a-z0-9]+', ' ', str(row.get('name') or '').lower()).strip()
        symbol = row.get('symbol')
        if name in DXY_NAMES and isinstance(symbol, str) and re.fullmatch(r'[A-Za-z0-9^._-]{1,30}', symbol):
            candidates.append(symbol)
    if len(set(candidates)) == 1:
        symbols['dxy'] = candidates[0]
    return symbols


def parse_quote(rows, symbol, now):
    row = next((r for r in rows if r.get('symbol') == symbol), None)
    if row is None:
        raise FeedError('symbol_not_returned')
    price, at = number(row.get('price')), number(row.get('timestamp'))
    if price is None or price <= 0 or at is None or at <= 0 or at > now+60:
        raise FeedError('invalid_quote')
    previous = number(row.get('previousClose'))
    # Calculate only when the actual previous-close field exists, never fake zero.
    change = (price / previous - 1)*100 if previous is not None and previous > 0 else None
    return {'symbol': symbol, 'name': str(row.get('name') or '')[:100], 'price': price,
            'previous_close': previous, 'change_pct': change, 'as_of': at, 'source': 'FMP',
            'unit': 'index_points', 'delivery': 'provider_delay_unverified', 'market_session': 'unknown',
            'semantics': 'index_level_not_tradable_price; change_vs_provider_previous_close'}


def parse_fmp_calendar(rows, now, naive_timezone=''):
    events = []; skipped = 0
    for row in rows:
        if str(row.get('country') or '').upper() not in {'US', 'USA', 'UNITED STATES'}:
            continue
        title = str(row.get('event') or '')[:200]
        if not title:
            continue
        try:
            stamp = str(row.get('date') or '')
            if not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', stamp):
                raise ValueError('missing_event_time')
            dt = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            basis = 'provider_explicit_offset'
            if dt.tzinfo is None:
                if naive_timezone not in {'UTC', 'America/New_York'}:
                    raise ValueError('provider time zone not confirmed')
                dt = localize(dt, naive_timezone)
                basis = 'operator_confirmed_' + naive_timezone
            at = dt.timestamp()
        except (ValueError, OverflowError):
            skipped += 1
            continue
        if not (now-86400 <= at <= now+7*86400):
            continue
        events.append({'id': digest(['fmp', title, at])[:24], 'source': 'FMP', 'event': title,
            'scheduled_at': at, 'country': 'US', 'time_basis': basis,
            'importance': 'high' if str(row.get('impact') or '').lower() == 'high' or IMPORTANT_EVENT.search(title) else 'other',
            'actual': number(row.get('actual')) if at <= now else None,
            'estimate': number(row.get('estimate')), 'previous': number(row.get('previous')),
            'unit': str(row.get('unit') or '')[:24] or None, 'values_status': 'provider_reported_not_officially_reconciled'})
    if skipped and not events:
        raise FeedError('unverified_calendar_timezone_or_dates')
    events.sort(key=lambda e: (e['importance'] != 'high', e['scheduled_at']))
    return {'events': events[:32], 'skipped_events': skipped, 'coverage': 'provider_us_calendar_not_exhaustive',
            'values_status': 'provider_reported_not_officially_reconciled'}


def collect(config, previous=None, *, now=None, getter=fetch):
    """Separate scheduled collector: inference never performs provider network I/O."""
    now = time.time() if now is None else now
    previous = previous or {}
    state = {'version': VERSION, 'captured_at': now, 'binding': config['binding'], 'sources': {}}
    old_sources = (previous.get('sources') or {}) if previous.get('binding') == config['binding'] else {}
    deadline = time.monotonic() + 90
    key = config.get('api_key') or ''
    for source in SOURCES:
        old = old_sources.get(source) or {}
        if source.startswith('fmp_') and (not config.get('fmp_enabled') or not key):
            state['sources'][source] = {'status': 'not_configured', 'error': 'api_key_or_permission_missing'}
            continue
        if not source.startswith('fmp_') and not config.get('official_enabled'):
            state['sources'][source] = {'status': 'disabled'}
            continue
        if now < (number(old.get('next_attempt_at')) or 0):
            state['sources'][source] = copy.deepcopy(old)
            continue
        current = {**copy.deepcopy(old), 'last_attempt_at': now}
        try:
            if source == 'treasury':
                local = datetime.fromtimestamp(now, ZoneInfo('America/New_York'))
                text = getter(TREASURY_URL, params={'data': 'daily_treasury_yield_curve', 'field_tdr_date_value_month': local.strftime('%Y%m')}, deadline=deadline)
                try:
                    data = parse_treasury(text, now)
                except FeedError as exc:
                    if str(exc) != 'no_valid_observations' or local.day > 7:
                        raise
                    # Early-month/weekend cache may have no current-month observation.
                    prior = local.replace(day=1)-timedelta(days=1)
                    data = parse_treasury(getter(TREASURY_URL, params={'data': 'daily_treasury_yield_curve', 'field_tdr_date_value_month': prior.strftime('%Y%m')}, deadline=deadline), now)
            elif source in CALENDAR_URLS:
                data = parse_ics(getter(CALENDAR_URLS[source], deadline=deadline), source, now)
            elif source == 'fmp_indices':
                catalog = (previous.get('index_catalog') or {}) if previous.get('binding') == config['binding'] else {}
                catalog_at = number(catalog.get('captured_at')) or 0
                if not 0 <= now-catalog_at < 86400:
                    rows = fmp_json('index-list', key, deadline=deadline, getter=getter)
                    catalog = {'captured_at': now, 'symbols': resolve_symbols(rows)}
                state['index_catalog'] = catalog
                symbols = catalog.get('symbols') or {}
                quotes = {}; errors = {}
                for name in ('sp500', 'nasdaq_composite', 'dxy'):
                    symbol = symbols.get(name)
                    if not symbol:
                        errors[name] = 'index_identity_not_verified'
                        continue
                    try:
                        rows = fmp_json('quote', key, params={'symbol': symbol}, deadline=deadline, getter=getter)
                        quotes[name] = parse_quote(rows, symbol, now)
                    except FeedError as exc:
                        errors[name] = str(exc)
                if not quotes:
                    priority = ('rate_limited', 'authentication_failed', 'subscription_required', 'access_denied', 'provider_error_payload')
                    raise FeedError(next((code for code in priority if code in errors.values()), 'no_usable_index_quotes'))
                data = {'quotes': quotes, 'errors': errors}
            else:
                day = datetime.fromtimestamp(now, UTC).date()
                rows = fmp_json('economic-calendar', key, params={'from': (day-timedelta(days=1)).isoformat(), 'to': (day+timedelta(days=8)).isoformat()}, deadline=deadline, getter=getter)
                data = parse_fmp_calendar(rows, now, config.get('calendar_timezone', ''))
            current.update({'status': 'ok', 'data': data, 'last_success_at': now, 'error': None,
                            'next_attempt_at': now+REFRESH_SECONDS[source], 'failures': 0})
        except FeedError as exc:
            failures = min(int(old.get('failures') or 0)+1, 8)
            backoff = min(6*3600, REFRESH_SECONDS[source]*2**min(failures-1, 4))
            current.update({'status': 'error', 'error': str(exc), 'failures': failures, 'next_attempt_at': now+backoff})
        state['sources'][source] = current
    if 'index_catalog' not in state and previous.get('binding') == config['binding']:
        state['index_catalog'] = copy.deepcopy(previous.get('index_catalog') or {})
    return state


def snapshot(cache, config, *, now=None):
    """Recheck age at decision time, freeze once, exclude stale/unknown values from facts."""
    now = time.time() if now is None else now
    if not isinstance(cache, dict) or cache.get('version') != VERSION or cache.get('binding') != config['binding']:
        cache = {}
    result = {'version': VERSION, 'captured_at': cache.get('captured_at'),
              'evaluated_at': now if cache.get('sources') else None, 'sources': {}, 'quotes': {}, 'rates': {}, 'events': [],
              'missing_data_policy': 'unknown_not_zero; incomplete_calendar_does_not_mean_no_event_risk',
              'coverage': 'US_only; BLS_BEA_agency_schedules_plus_optional_FMP; not_exhaustive',
              'policy_rate': {'status': 'not_connected'}, 'quote_delivery': 'not_certified_realtime'}
    for source in SOURCES:
        raw = (cache.get('sources') or {}).get(source) or {}
        enabled = config.get('fmp_enabled') and config.get('api_key') if source.startswith('fmp_') else config.get('official_enabled')
        if not enabled:
            result['sources'][source] = {'status': 'not_configured' if source.startswith('fmp_') else 'disabled', 'usable': False}
            continue
        success = number(raw.get('last_success_at'))
        fresh = success is not None and 0 <= now-success <= TTL_SECONDS[source]
        status = 'cached_after_error' if raw.get('status') == 'error' and fresh else 'available' if fresh else 'stale' if success else raw.get('status', 'unavailable')
        result['sources'][source] = {'status': status, 'usable': fresh, 'last_success_at': success,
            'last_attempt_at': raw.get('last_attempt_at'), 'error': raw.get('error'), 'next_attempt_at': raw.get('next_attempt_at')}
        if not fresh:
            continue
        data = copy.deepcopy(raw.get('data') or {})
        if source == 'treasury':
            try:
                day = datetime.fromisoformat(data.get('observation_date') or '').date()
                age = (datetime.fromtimestamp(now, ZoneInfo('America/New_York')).date()-day).days
                valid = 0 <= age <= 7
            except ValueError:
                valid = False
            result['rates'] = {**data, 'usable': valid, 'status': 'daily_reference' if valid else 'stale_observation'}
            result['sources'][source]['usable'] = valid
            if not valid:
                result['sources'][source]['status'] = 'stale_observation'
        elif source == 'fmp_indices':
            for name, quote in (data.get('quotes') or {}).items():
                at = number(quote.get('as_of')); age = now-at if at is not None else None
                valid = age is not None and 0 <= age <= 1800
                result['quotes'][name] = {**quote, 'age_seconds': age, 'usable': valid,
                    'status': 'recent_provider_quote' if valid else 'stale_or_market_closed'}
            result['sources'][source]['instrument_errors'] = data.get('errors') or {}
            result['sources'][source]['usable'] = any(q['usable'] for q in result['quotes'].values())
            if data.get('errors'):
                result['sources'][source]['status'] = 'partial'
            elif not result['sources'][source]['usable']:
                result['sources'][source]['status'] = 'stale_or_market_closed'
        else:
            if source in CALENDAR_URLS and (number(data.get('coverage_end')) or 0) < now:
                result['sources'][source].update({'status': 'outdated_schedule', 'usable': False})
                continue
            result['sources'][source]['skipped_events'] = data.get('skipped_events', 0)
            for event in data.get('events') or []:
                at = number(event.get('scheduled_at'))
                if at is not None and now-86400 <= at <= now+7*86400:
                    result['events'].append({**event, 'minutes_to_event': round((at-now)/60, 1), 'usable': True})
    # Do not silently merge provider claims with agency schedules: revisions can differ.
    result['events'].sort(key=lambda e: (e.get('importance') != 'high', abs(e['scheduled_at']-now), e['id']))
    result['events'] = result['events'][:24]
    result['digest'] = digest(result)
    return result


def facts(snapshot_data):
    """Distinct macro group: context alone must not satisfy technical-entry evidence."""
    out = {}
    def add(ref, value):
        if value is None or isinstance(value, (dict, list, bool)):
            return
        if isinstance(value, str) and (not value or len(value) > 160):
            return
        if isinstance(value, (float, int)) and not math.isfinite(value):
            return
        out[ref] = {'value': value, 'group': 'macro'}
    for name, row in (snapshot_data.get('quotes') or {}).items():
        if row.get('usable') is not True:
            continue
        for field in ('price', 'previous_close', 'change_pct', 'as_of', 'symbol'):
            add('/macro/quotes/'+name+'/'+field, row.get(field))
    rates = snapshot_data.get('rates') or {}
    if rates.get('usable') is True:
        for field in ('observation_date', 'us2y_pct', 'us10y_pct', 'spread_10y_2y_bp', 'us2y_change_bp', 'us10y_change_bp'):
            add('/macro/rates/'+field, rates.get(field))
    for i, row in enumerate(snapshot_data.get('events') or []):
        if row.get('usable') is not True:
            continue
        for field in ('event', 'scheduled_at', 'minutes_to_event', 'actual', 'estimate', 'previous', 'unit'):
            add('/macro/events/'+str(i)+'/'+field, row.get(field))
    return out


def load_snapshot(*, now=None):
    from okxquant_backend.macro_store import load_settings
    config = load_settings()
    return snapshot(read_cache(), config, now=now)


def refresh(*, now=None):
    from scripts.config_lock import configuration_write
    from okxquant_backend.macro_store import CONFIG_FILE, load_settings
    from okxquant_gateway.secrets import _atomic_write
    # Serialize refresh and configuration changes; never hold the trading writer lock.
    with configuration_write(CONFIG_FILE, timeout=.1):
        config = load_settings()
        updated = collect(config, read_cache(), now=now)
        _atomic_write(CACHE_FILE, json.dumps(updated, ensure_ascii=False, allow_nan=False).encode())
        return snapshot(updated, config, now=now)


def main():
    try:
        result = refresh()
        print(json.dumps({'status': 'collected', 'sources': {k: v['status'] for k, v in result['sources'].items()}}, ensure_ascii=False))
        return 0
    except TimeoutError:
        print('{"status":"refresh_already_running"}')
        return 0
    except Exception as exc:
        # Type only: provider exceptions can carry URL credentials.
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
