"""Shanghai calendar-day filtering for AI history, without deleting audit records."""
from datetime import datetime, timedelta
from scripts.dashboard_stats import SHANGHAI, normalize_timestamp


def current_day(now=None):
    instant = datetime.now(SHANGHAI) if now is None else normalize_timestamp(now)
    if instant is None:
        raise ValueError('Invalid history time')
    return instant.date().isoformat()


def today_records(rows, *, now=None):
    instant = datetime.now(SHANGHAI) if now is None else normalize_timestamp(now)
    if instant is None:
        raise ValueError('Invalid history time')
    start = instant.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    dated = []
    for row in rows if isinstance(rows, (list, tuple)) else []:
        if not isinstance(row, dict):
            continue
        at = normalize_timestamp(row.get('time') or row.get('timestamp'))
        if at is not None and start <= at < end:
            dated.append((at, row))
    return [row for _, row in sorted(dated, key=lambda pair: pair[0], reverse=True)]
