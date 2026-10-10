"""Bounded, account/day/generation-scoped statistics cache, never a last-good fallback."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import threading
import time
from scripts.json_projection_cache import VersionedJsonProjection, _signature

def _checked_rows(rows):
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise ValueError("Invalid canonical ledger shape")
    return rows


_ROWS=VersionedJsonProjection(_checked_rows)


def read_ledger(path):
    """Shared exact-generation source cache for lists, details and statistics."""
    from scripts.statistics_epoch import filter_rows
    return filter_rows(_ROWS.read(path), data_dir=Path(path).parent)
_LOCK=threading.Lock()
_KEY=None
_VALUE=None


def read_periods(path, scope, *, now=None):
    from scripts.horizon_stats import strategy_periods
    global _KEY,_VALUE
    path=Path(path).resolve();now=time.time() if now is None else float(now)
    day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).date().isoformat()
    with _LOCK:
        try:
            from scripts.statistics_epoch import epoch, filter_rows
            window=epoch(scope,path.parent)
            before=_signature(path.stat());key=(str(path),before,scope,day,int(now)//60,(window or {}).get('id'))
            if _KEY==key:return _VALUE
            rows=filter_rows(_ROWS.read(path),scope=scope,data_dir=path.parent,keep_active=False)
            if _signature(path.stat())!=before:raise OSError("Ledger changed during statistics read")
            value=strategy_periods(rows,scope=scope,now=now)
            _KEY=key;_VALUE=value
            return value
        except Exception:
            _KEY=None;_VALUE=None
            raise
