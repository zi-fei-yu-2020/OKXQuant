"""Atomic, account-scoped performance baselines with non-destructive migration.

Version 2 keeps a map keyed by OKXEnvironment.identity. The top-level record is a
compatibility projection, NOT a fallback for another account. Pre-scope data is
retained in legacy_baseline on the first explicit save/migration.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import ROOT
from scripts.config_lock import configuration_write

BASELINE_FILE = ROOT / "data" / "account_initial_state.json"
BJ_TZ = timezone(timedelta(hours=8))
DEFAULT_CAPITAL = 10_000.0
MIN_CAPITAL = 1.0
MAX_CAPITAL = 1_000_000_000.0
_META = {"baselines", "schema_version", "legacy_baseline"}


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) and number > 0 else default


def _scope(scope=None):
    if scope is None:
        from scripts.okx_runtime import selected_environment
        scope = selected_environment().identity
    scope = getattr(scope, "identity", scope)
    if not isinstance(scope, str) or not scope.startswith(("okx:demo:", "okx:live:")):
        raise ValueError("Invalid baseline account scope")
    return scope


def _read(*, strict=False):
    try:
        loaded = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("Invalid baseline document")
        if "baselines" in loaded and not isinstance(loaded["baselines"], dict):
            raise ValueError("Invalid scoped baselines")
        return loaded
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError):
        if strict:
            raise ValueError("Baseline storage is unreadable; original data was preserved") from None
        return {}


def _project(data, scope):
    records = data.get("baselines", {})
    record = records.get(scope)
    source = "scoped"
    if not isinstance(record, dict) or record.get("account_scope", scope) != scope:
        record = None
    if record is None and data.get("account_scope") == scope:
        record = {k: v for k, v in data.items() if k not in _META}
    # Historical explicit demo baselines remain usable until the first migration.
    # An ownerless legacy amount is NEVER evidence of a live account's capital.
    if record is None and not records and not data.get("account_scope") and scope.startswith("okx:demo:"):
        record = {k: v for k, v in data.items() if k not in _META}
        source = "legacy_demo"
    record = record or {}
    env_scope = os.getenv("INITIAL_CAPITAL_ACCOUNT_SCOPE", "")
    env_allowed = env_scope == scope or (not env_scope and not records and not data.get("account_scope") and scope.startswith("okx:demo:"))
    env_capital = _number(os.getenv("INITIAL_CAPITAL"), 0) if env_allowed else 0
    explicit = _number(record.get("initial_capital"), 0) if record.get("baseline_configured") is not False else 0
    configured = bool(explicit or env_capital)
    return {**record, "account_scope": scope,
            "initial_capital": round(explicit or env_capital or DEFAULT_CAPITAL, 2),
            "baseline_configured": configured,
            "baseline_source": source if explicit else "environment" if env_capital else "unconfigured",
            "reset_time": str(record.get("reset_time") or "1970-01-01 00:00:00")}


def load_account_baseline(scope=None, *, path=None) -> dict[str, Any]:
    """Read one account only; reads never migrate or change the backing file."""
    if path is None:
        data = _read()
    else:
        try:
            data = json.loads(Path(path).read_text(encoding='utf8'))
            if not isinstance(data, dict): data = {}
        except (OSError, ValueError): data = {}
    return _project(data, _scope(scope))


def _store(data, updated):
    records = dict(data.get("baselines", {}))
    # Preserve an explicitly scoped pre-v2 record as well as the current record.
    previous_scope = data.get("account_scope")
    if previous_scope and previous_scope not in records:
        records[previous_scope] = {k: v for k, v in data.items() if k not in _META}
    records[updated["account_scope"]] = updated
    document = {**{k: v for k, v in data.items() if k in _META}, **updated, "schema_version": 2, "baselines": records}
    if data and "legacy_baseline" not in data and "baselines" not in data:
        document["legacy_baseline"] = dict(data)
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".account-baseline-", suffix=".json", dir=BASELINE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, BASELINE_FILE)
        os.chmod(BASELINE_FILE, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def preserve_account_baseline(scope=None):
    """Bind the outgoing legacy baseline BEFORE a persisted account switch.

    Existing unowned LIVE data is retained, but not claimed. Account-center calls
    this under its mutation lock, so the outgoing runtime identity is still valid.
    """
    scope = _scope(scope)
    with configuration_write(BASELINE_FILE):
        data = _read(strict=True)
        previous = _project(data, scope)
        if previous["baseline_configured"]:
            _store(data, previous)


def update_initial_capital(initial_capital: float, scope=None) -> dict[str, Any]:
    if isinstance(initial_capital, bool):
        raise ValueError("Invalid initial capital")
    capital = round(float(initial_capital), 2)
    if not math.isfinite(capital) or not MIN_CAPITAL <= capital <= MAX_CAPITAL:
        raise ValueError(f"Initial capital must be between {MIN_CAPITAL:.2f} and {MAX_CAPITAL:.2f} USDT")
    scope = _scope(scope)
    with configuration_write(BASELINE_FILE):
        data = _read(strict=True)
        previous = _project(data, scope)
        updated = {**previous, "initial_capital": capital, "baseline_configured": True,
                   "baseline_source": "scoped", "account_scope": scope,
                   "capital_updated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")}
        _store(data, updated)
    return {"previous_initial_capital": previous["initial_capital"], **updated}
