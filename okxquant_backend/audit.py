"""Private, serialized append-only audit records; diagnostics never repeat writes."""
from __future__ import annotations
from collections import deque
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from scripts.config_lock import configuration_write

ROOT = Path(__file__).resolve().parents[1]
AUDIT_FILE = ROOT / "logs" / "okxquant_admin_audit.jsonl"
LOG = logging.getLogger(__name__)
_SENSITIVE = re.compile(r"password|passphrase|secret|token|api.?key|authorization|cookie", re.I)


def _redact(value):
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if _SENSITIVE.search(str(k)) else _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def record(action: str, status: str, detail: dict[str, Any] | None = None) -> bool:
    now = datetime.now(timezone(timedelta(hours=8)))
    payload = {"timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
               "action": action, "status": status, "detail": _redact(detail or {})}
    try:
        with configuration_write(AUDIT_FILE):
            AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                os.chmod(AUDIT_FILE, 0o600)
                handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
        return True
    except (OSError, TimeoutError):
        # An action may already have completed. A logging failure must never turn
        # that success into a retryable API failure. Do not log untrusted detail.
        LOG.warning("Admin audit persistence unavailable; completed action was not retried")
        return False


def recent(limit: int = 50) -> list[dict[str, Any]]:
    try:
        with AUDIT_FILE.open(encoding="utf-8") as handle:
            lines = deque(handle, maxlen=max(1, min(limit, 200)))
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except json.JSONDecodeError:
            continue
    return records
