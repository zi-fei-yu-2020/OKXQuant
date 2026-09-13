"""Opt-in shadow monitor: local evidence writes + bounded shared public GET only.

No LLM calls, credentials, account access, scheduler changes or trade operations.
Run --once from an existing minute scheduler only after explicit local opt-in.
"""
from dataclasses import asdict
from contextlib import closing
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import scenario_entry as engine

DB_PATH = ROOT / 'data' / 'scenario_shadow.db'
SCHEMA = '''CREATE TABLE IF NOT EXISTS states(scope TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,scope TEXT NOT NULL,at INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS shadow_event_scope ON events(scope,at);
CREATE TABLE IF NOT EXISTS attempts(scope TEXT NOT NULL,at INTEGER NOT NULL,PRIMARY KEY(scope,at));
CREATE TABLE IF NOT EXISTS bindings(scope TEXT PRIMARY KEY,signature TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candles(scope TEXT NOT NULL,inst TEXT NOT NULL,tf TEXT NOT NULL,anchor INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scope,inst,tf));'''


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=2)
    db.executescript(SCHEMA)
    return db


def apply_frames(scope, frames, *, path=None, spec=None):
    """Atomic state + evidence: a restart cannot duplicate a trigger."""
    path = path or DB_PATH
    spec = spec or engine.Spec()
    with closing(connect(path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM states WHERE scope=?', (scope,)).fetchone()
        state = json.loads(row[0]) if row else engine.initial_state(scope, spec)
        for frame in frames:
            before = {e['event_id'] for e in state['events']}
            state = engine.advance(state, frame, spec)
            for event in state['events']:
                if event['event_id'] not in before:
                    db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?)',
                               (event['event_id'], scope, event['at_ms'], json.dumps(event, ensure_ascii=False, allow_nan=False)))
        db.execute('INSERT OR REPLACE INTO states VALUES (?,?)', (scope, json.dumps(state, ensure_ascii=False, allow_nan=False)))
    db.close()
    return state


def public_status(scope, *, path=None, now=None):
    path = Path(path or DB_PATH)
    if not path.exists():
        return {'enabled': False, 'mode': 'shadow_only', 'order_authorized': False}
    now = time.time() if now is None else now
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            row = db.execute('SELECT payload FROM states WHERE scope=?', (scope,)).fetchone()
            if row is None:
                return {'enabled': False, 'mode': 'shadow_only', 'order_authorized': False}
            state = json.loads(row[0])
            events = [json.loads(r[0]) for r in db.execute('SELECT payload FROM events WHERE scope=? ORDER BY at DESC,id DESC LIMIT 15', (scope,))]
        last = max(state['last_at'].values(), default=0)
        valid = max(state['last_valid_at'].values(), default=0)
        candidates = list(state['candidates'].values())
        for candidate in candidates:
            candidate['display_status'] = candidate['status']
            if candidate['status'] == 'armed' and now * 1000 >= candidate['definition']['expires_at_ms']:
                candidate['display_status'] = 'expired'
            elif candidate['status'] == 'triggered_research' and now * 1000 >= candidate['finished_at_ms'] + engine.WIDTH:
                candidate['display_status'] = 'review_expired'
        return {'enabled': True, 'mode': 'shadow_only', 'order_authorized': False, 'version': engine.VERSION,
                'updated_at_ms': last, 'stale': now * 1000 - last > 2 * engine.WIDTH,
                'last_valid_at_ms': valid, 'frames': state['frames'],
                'candidates': candidates, 'recent_events': events,
                'message': '影子研究，不下单；触发仅申请新一轮证据、账户与风控复核。'}
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return {'enabled': True, 'mode': 'shadow_only', 'status': 'unavailable', 'order_authorized': False,
                'message': '影子研究记录不可用；不影响独立持仓保护，也不会授权交易。'}


def closed_rows(raw, width, at):
    result = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 9 or str(row[8]) != '1':
            continue
        ts = int(row[0]) + width
        if ts > at:
            continue
        values = [float(row[i]) for i in range(1, 6)]
        result.append(dict(zip(('open', 'high', 'low', 'close', 'volume'), values), ts_ms=ts, confirm=True))
    # Do not deduplicate inconsistent receipts or interpolate missing bars.
    result.sort(key=lambda row: row['ts_ms'])
    engine.validate_bars(result, width, at)
    return result[-20:]


def collect_frame(scope, inst, at, *, simulated, path, reader, deadline):
    frame = {'scope': scope, 'instrument': inst, 'at_ms': at, 'entry_supported': True}
    for tf, width in (('5m', engine.WIDTH), ('1H', 3_600_000), ('4H', 14_400_000)):
        anchor = at // width * width
        with closing(connect(path)) as db, db:
            cached = db.execute('SELECT anchor,payload FROM candles WHERE scope=? AND inst=? AND tf=?', (scope, inst, tf)).fetchone()
        db.close()
        if cached and cached[0] == anchor:
            rows = json.loads(cached[1])
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('shadow_collection_budget_exhausted')
            payload = reader(f'https://www.okx.com/api/v5/market/candles?instId={inst}&bar={tf}&limit=24',
                             simulated=simulated, timeout=min(3, remaining))
            if str(payload.get('code')) != '0':
                raise ValueError('shadow_public_candles_unavailable')
            rows = closed_rows(payload.get('data', []), width, at)
            with closing(connect(path)) as db, db:
                db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?)', (scope, inst, tf, anchor, json.dumps(rows)))
            db.close()
        frame[tf] = rows
    return frame


def run_once(config, *, path=None, now=None, allow_public_network=False, reader=None):
    # An absent/disabled config performs no filesystem, HTTP or credential access.
    if config.get('enabled') is not True:
        return {'enabled': False, 'mode': 'shadow_only', 'order_authorized': False}
    if config.get('mode') != 'shadow_only' or config.get('order_authorized', False) is not False:
        raise ValueError('Only non-trading shadow mode is supported')
    if not allow_public_network:
        raise ValueError('Explicit --allow-public-network is required')
    scope = config.get('scope')
    symbols = config.get('instruments')
    if not isinstance(scope, str) or not 1 <= len(scope) <= 128 or not isinstance(symbols, list) or not 1 <= len(symbols) <= 6:
        raise ValueError('Invalid scope or instrument list')
    if not isinstance(config.get('simulated'), bool):
        raise ValueError('Explicit market environment is required')
    if len({item['instId'] for item in symbols}) != len(symbols):
        raise ValueError('Duplicate shadow instrument')
    for item in symbols:
        if not re.fullmatch(r'[A-Z0-9]{2,16}-USDT-SWAP', item['instId']):
            raise ValueError('Invalid shadow instrument')
    spec = engine.Spec()  # Frozen research spec; no implicit production parameter edits.
    path = Path(path or DB_PATH)
    now = time.time() if now is None else now
    # Five-second settlement grace. A minute job at hh:mm:01 will inspect it next minute.
    at = int((now * 1000 - 5000) // engine.WIDTH) * engine.WIDTH
    binding = engine.digest({'simulated': config['simulated'], 'instruments': sorted(item['instId'] for item in symbols), 'spec': asdict(spec)})
    with closing(connect(path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT signature FROM bindings WHERE scope=?', (scope,)).fetchone()
        if previous and previous[0] != binding:
            raise ValueError('Shadow environment/instruments changed; use a new research scope')
        db.execute('INSERT OR IGNORE INTO bindings VALUES (?,?)', (scope, binding))
        claimed = db.execute('INSERT OR IGNORE INTO attempts VALUES (?,?)', (scope, at)).rowcount
    db.close()
    if not claimed:
        return {'status': 'already_observed_or_attempted', 'at_ms': at, 'order_authorized': False}
    if reader is None:
        from scripts.public_market import get_json
        reader = get_json
    frames = []
    deadline = time.monotonic() + 20
    catalog = None
    if config.get('support_source') == 'local_catalog':
        # Read only: never launches the support module's refresh/background worker.
        from scripts.instrument_support import _read
        catalog = _read('demo' if config['simulated'] else 'live')
    for configured_item in symbols:
        item = configured_item
        if config.get('support_source') == 'local_catalog':
            row = (catalog or {}).get('instruments', {}).get(item['instId'], {})
            item = {'instId': item['instId'], 'can_open': row.get('state') == 'live' and row.get('settleCcy') == 'USDT',
                    'checked_at': (catalog or {}).get('checked_at')}

        # Explicit frozen support evidence, not inference from a public ticker.
        checked = item.get('checked_at')
        supported = (item.get('can_open') is True and isinstance(checked, (int, float)) and not isinstance(checked, bool)
                     and 0 <= now - checked <= 1800)
        frame = {'scope': scope, 'instrument': item['instId'], 'at_ms': at, 'entry_supported': supported}
        if supported:
            try:
                frame = collect_frame(scope, item['instId'], at, simulated=config['simulated'], path=path, reader=reader, deadline=deadline)
            except Exception as exc:
                frame['collection_error'] = type(exc).__name__  # No full transport response or credentials.
        frames.append(frame)
    state = apply_frames(scope, frames, path=path, spec=spec)
    return {'status': 'observed', 'at_ms': at, 'frames': state['frames'], 'order_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--database', type=Path, default=DB_PATH)
    parser.add_argument('--once', action='store_true', required=True)
    parser.add_argument('--allow-public-network', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run_once(json.loads(args.config.read_text(encoding='utf8')), path=args.database,
                              allow_public_network=args.allow_public_network), ensure_ascii=False))


if __name__ == '__main__':
    main()
