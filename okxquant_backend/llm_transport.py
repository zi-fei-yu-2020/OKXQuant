"""Bounded retries for inference only. Never use this transport for orders.

Retries preserve every payload field, including model, high reasoning effort and
JSON mode. Errors deliberately exclude prompts, credentials and upstream bodies.
"""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import errno
import json
import logging
import math
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

LOG = logging.getLogger(__name__)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
# Never surface arbitrary upstream error messages: they can echo credentials or
# strategy prompts. Only known machine codes have user-facing explanations.
PROVIDER_ERROR_LABELS = {
    "system_cpu_overloaded": "模型网关 CPU 过载保护",
    "system_memory_overloaded": "模型网关内存过载保护",
    "system_disk_overloaded": "模型网关磁盘过载保护",
    "model_not_found": "模型网关未找到可用模型渠道",
}


def error_diagnostics(error: urllib.error.HTTPError) -> tuple[str, str]:
    """Read a bounded error document and retain only allowlisted metadata."""
    provider_code = ""
    try:
        document = json.loads(error.read(8192).decode("utf-8"))
        detail = document.get("error") if isinstance(document, dict) else None
        code = detail.get("code") if isinstance(detail, dict) else None
        if isinstance(code, str) and code in PROVIDER_ERROR_LABELS:
            provider_code = code
    except Exception:
        # A truncated, unreadable or non-JSON error must not mask its HTTP status.
        pass
    request_id = str(error.headers.get("X-Oneapi-Request-Id", "")) if error.headers else ""
    # New API generates a 20-digit timestamp followed by an alphanumeric ID.
    # Reject arbitrary headers rather than logging untrusted text or keys.
    if not re.fullmatch(r"[0-9]{20}[A-Za-z0-9]{8,64}", request_id):
        request_id = ""
    return provider_code, request_id


class LLMRequestError(RuntimeError):
    def __init__(self, status_code: int, attempts: int, category: str,
                 provider_code: str = "", request_id: str = ""):
        self.status_code = status_code
        self.attempts = attempts
        self.category = category
        self.provider_code = provider_code if provider_code in PROVIDER_ERROR_LABELS else ""
        self.provider_reason = PROVIDER_ERROR_LABELS.get(self.provider_code, "")
        self.request_id = request_id if re.fullmatch(r"[0-9]{20}[A-Za-z0-9]{8,64}", request_id) else ""
        label = f"HTTP {status_code}" if status_code else category
        if self.provider_reason:
            label += f"（{self.provider_reason}）"
        super().__init__(f"模型请求失败：{label}，已尝试 {attempts} 次；未更换模型或降低思考强度")


def retry_delay(header: str | None, attempt: int) -> float:
    if header:
        try:
            seconds = float(header)
        except ValueError:
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = -1
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    return 0.5 * (2 ** (attempt - 1))


def _transient_network_error(error: BaseException) -> bool:
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLError):
        return False  # Never retry around certificate validation failures.
    return isinstance(reason, (TimeoutError, ConnectionError)) or (
        isinstance(reason, OSError)
        and reason.errno in {errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT, socket.EAI_AGAIN}
    )


def request_json(endpoint: str, headers: dict[str, str], payload: dict[str, Any], timeout: float,
                 max_attempts: int = 3) -> tuple[dict[str, Any], int, int, int]:
    """Return JSON, HTTP status, end-to-end latency and attempts used.

    All attempts share one timeout budget. Retry-After is respected: when it
    exceeds the remaining budget, fail instead of sending an early retry.
    """
    if not math.isfinite(timeout) or timeout <= 0 or not 1 <= max_attempts <= 3:
        raise ValueError("Invalid inference timeout or retry limit")
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    start = time.monotonic()
    deadline = start + timeout
    for attempt in range(1, max_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMRequestError(0, attempt - 1, "deadline_exceeded")
        status = 0
        provider_code = request_id = ""
        delay_header = None
        category = "network_error"
        retryable = False
        failure: BaseException | None = None
        try:
            request = urllib.request.Request(endpoint, data=encoded, headers=headers)
            with urllib.request.urlopen(request, timeout=remaining) as response:
                status = response.getcode()
                decoded = json.loads(response.read().decode("utf-8"))
            if not isinstance(decoded, dict) or decoded.get("error"):
                raise LLMRequestError(status, attempt, "invalid_response")
            return decoded, status, round((time.monotonic() - start) * 1000), attempt
        except urllib.error.HTTPError as exc:
            status = exc.code
            category = "http_error"
            delay_header = exc.headers.get("Retry-After") if exc.headers else None
            retryable = status in RETRYABLE_STATUS
            failure = exc
            provider_code, request_id = error_diagnostics(exc)
            exc.close()
            LOG.warning("LLM HTTP failure status=%s attempt=%s/%s provider_code=%s request_id=%s",
                        status, attempt, max_attempts, provider_code or "unknown", request_id or "unavailable")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            retryable = _transient_network_error(exc)
            failure = exc
        except (ValueError, UnicodeError) as exc:
            raise LLMRequestError(status, attempt, "invalid_json_response") from exc
        delay = retry_delay(delay_header, attempt)
        if status == 503 and provider_code.startswith("system_"):
            # New API caches its host-load sample for five seconds. Retrying
            # within 0.5/1s hits the same admission rejection before inference.
            # Keep the existing total deadline and attempt cap, and never
            # shorten a longer Retry-After supplied by the server.
            delay = max(delay, 6.0 * (2 ** (attempt - 1)))
        if not retryable or attempt == max_attempts or delay >= deadline - time.monotonic():
            raise LLMRequestError(status, attempt, category, provider_code, request_id) from failure
        LOG.warning("LLM transient failure status=%s; retry=%s/%s delay=%.2fs",
                    status or category, attempt + 1, max_attempts, delay)
        time.sleep(delay)
    raise AssertionError("Unreachable")
