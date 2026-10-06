"""Durable, fail-closed recovery scheduling for pre-inference CPU overload rejects.

Only the exact, fast HTTP 503 admission rejection is eligible.  Ambiguous
network failures, timeouts, malformed model output and completed inference are
never replayed here because the upstream billing/execution outcome may be
unknown.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import sys
from typing import Any

from okxquant_gateway.store import GatewayStore

BJ_TZ = timezone(timedelta(hours=8))
SLOT_SECONDS = 15 * 60
MIN_NEXT_SLOT_MARGIN_SECONDS = 4 * 60
MIN_DELAY_SECONDS = 45
MAX_DELAY_SECONDS = 90
MAX_ADMISSION_REJECT_DURATION_MS = 10_000


def slot_start(now: datetime | None = None) -> datetime:
    current = now or datetime.now(BJ_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BJ_TZ)
    epoch = int(current.timestamp())
    return datetime.fromtimestamp(epoch - epoch % SLOT_SECONDS, BJ_TZ)


def context(account_scope: str, now: datetime | None = None) -> dict[str, str]:
    start = slot_start(now)
    return {
        "account_scope": str(account_scope or "")[:160],
        "slot_start": start.strftime("%Y-%m-%d %H:%M:%S"),
    }


def eligible_failure(failure: dict[str, Any] | None, duration_ms: int) -> bool:
    return bool(
        isinstance(failure, dict)
        and failure.get("category") == "http_error"
        and failure.get("http_status") == 503
        and failure.get("provider_error_code") == "system_cpu_overloaded"
        and failure.get("attempts") == 1
        and 0 <= int(duration_ms) < MAX_ADMISSION_REJECT_DURATION_MS
    )


def _test_write_allowed() -> bool:
    if "unittest" not in sys.modules and "pytest" not in sys.modules:
        return True
    return bool(os.environ.get("OKXQUANT_GATEWAY_DB") or os.environ.get("OKXQUANT_ALLOW_TEST_RECOVERY"))


def schedule(
    *,
    failure: dict[str, Any] | None,
    duration_ms: int,
    recovery_context: dict[str, Any] | None,
    original_model_call_id: int | None,
    delay_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Persist at most one delayed recovery for an account/15-minute slot."""
    if not eligible_failure(failure, duration_ms) or not isinstance(recovery_context, dict):
        return None
    if not _test_write_allowed():
        return None
    scope = str(recovery_context.get("account_scope") or "")[:160]
    raw_slot = str(recovery_context.get("slot_start") or "")
    if not scope or not raw_slot:
        return None
    try:
        start = datetime.strptime(raw_slot, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ_TZ)
    except ValueError:
        return None
    now = datetime.now(BJ_TZ)
    # A late rejection has no safe recovery window before the next natural slot.
    expires = start + timedelta(seconds=SLOT_SECONDS - MIN_NEXT_SLOT_MARGIN_SECONDS)
    if now >= expires or slot_start(now) != start:
        return None
    delay = delay_seconds if delay_seconds is not None else random.SystemRandom().randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    delay = max(MIN_DELAY_SECONDS, min(MAX_DELAY_SECONDS, int(delay)))
    scheduled = now + timedelta(seconds=delay)
    if scheduled >= expires:
        return None
    fingerprint_payload = {
        "category": failure.get("category"),
        "http_status": failure.get("http_status"),
        "provider_error_code": failure.get("provider_error_code"),
        "attempts": failure.get("attempts"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    from okxquant_gateway.publisher import DB_PATH
    db_path = Path(os.environ.get("OKXQUANT_GATEWAY_DB", DB_PATH))
    return GatewayStore(db_path).enqueue_ai_recovery(
        account_scope=scope,
        slot_start=start.strftime("%Y-%m-%d %H:%M:%S"),
        scheduled_at=scheduled.strftime("%Y-%m-%d %H:%M:%S"),
        expires_at=expires.strftime("%Y-%m-%d %H:%M:%S"),
        failure_fingerprint=fingerprint,
        original_model_call_id=original_model_call_id,
    )


def authorized_environment() -> dict[str, Any] | None:
    """Validate scheduler-issued recovery environment before bypassing slot dedupe."""
    raw_id = os.environ.get("OKXQUANT_RECOVERY_ID", "")
    expected_scope = os.environ.get("OKXQUANT_RECOVERY_ACCOUNT_SCOPE", "")
    expected_slot = os.environ.get("OKXQUANT_RECOVERY_SLOT_START", "")
    if os.environ.get("OKXQUANT_AI_RECOVERY") != "1" or not raw_id.isdigit() or not expected_scope or not expected_slot:
        return None
    try:
        from okxquant_gateway.publisher import DB_PATH
        db_path = Path(os.environ.get("OKXQUANT_GATEWAY_DB", DB_PATH))
        row = GatewayStore(db_path).ai_recovery(int(raw_id))
    except Exception:
        return None
    if not row or row.get("status") != "running":
        return None
    if row.get("account_scope") != expected_scope or row.get("slot_start") != expected_slot:
        return None
    return row
