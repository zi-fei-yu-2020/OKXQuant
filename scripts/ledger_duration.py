"""Duration display from actual receipt timestamps, never a return inference."""
import math


def duration_seconds(start, end):
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start <= 0 or end < start:
        return None
    return round(end - start, 3)


def format_duration(value):
    if value is None or isinstance(value, bool):
        return '--'
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return '--'
    if not math.isfinite(seconds) or seconds < 0:
        return '--'
    if seconds < 1:
        return '不足1秒' if seconds > 0 else '0秒'
    # Truncate to 0.1s so a 59.99s receipt never displays as 60.0s.
    if seconds < 60:
        return f'{math.floor(seconds * 10) / 10:g}秒'
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f'{hours}时{minutes}分' + (f'{seconds}秒' if seconds else '')
    return f'{minutes}分' + (f'{seconds}秒' if seconds else '钟')


def duration_bucket(seconds, horizon='unknown'):
    """Describe actual holding time separately from the planned strategy horizon."""
    try:
        value = float(seconds)
    except (TypeError, ValueError, OverflowError):
        return 'unknown'
    if not math.isfinite(value) or value < 0:
        return 'unknown'
    planned = str(horizon or 'unknown').lower()
    if planned == 'scalp':
        if value <= 1800: return 'scalp_standard'
        if value <= 7200: return 'scalp_extended'
        return 'scalp_overdue'
    if planned == 'swing':
        if value <= 14400: return 'swing_fast'
        if value <= 86400: return 'swing_standard'
        return 'swing_extended'
    if value <= 7200: return 'short_unknown'
    if value <= 86400: return 'medium_unknown'
    return 'long_unknown'
