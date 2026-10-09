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


ERROR_LABELS = {
    'request_timeout': '模型请求超时', 'network_error': '模型网络请求失败',
    'connection_error': '模型连接失败', 'dns_error': '模型地址解析失败',
    'certificate_error': '模型连接证书校验失败', 'deadline_exceeded': '模型调用总时限已耗尽',
    'invalid_response': '模型网关响应格式无效', 'invalid_json_response': '模型网关返回的内容不是有效JSON',
    'empty_model_output': '模型返回空正文', 'truncated_model_output': '\u6a21\u578bJSON\u8f93\u51fa\u88ab\u622a\u65ad',
    'http_error': '\u6a21\u578b\u63a5\u53e3\u8bf7\u6c42\u5931\u8d25',
    'connect_timeout': '\u6a21\u578b\u8fde\u63a5\u8d85\u65f6',
    'first_byte_timeout': '\u6a21\u578b\u9996\u5305\u7b49\u5f85\u8d85\u65f6',
    'idle_timeout': '\u6a21\u578b\u54cd\u5e94\u6d41\u957f\u65f6\u95f4\u65e0\u6570\u636e',
    'total_timeout': '\u6a21\u578b\u8bf7\u6c42\u603b\u65f6\u9650\u5df2\u8017\u5c3d',
    'stream_error': '\u6a21\u578b\u6d41\u5f0f\u54cd\u5e94\u8fd4\u56de\u9519\u8bef',
    'stream_incomplete': '\u6a21\u578b\u54cd\u5e94\u6d41\u672a\u5b8c\u6574\u7ed3\u675f',
    'stream_truncated': '\u6a21\u578b\u54cd\u5e94\u6d41\u88ab\u622a\u65ad',
    'completion_invalid': '\u6a21\u578b\u672a\u660e\u786e\u6b63\u5e38\u5b8c\u6210\uff0c\u4e0d\u63a5\u53d7\u90e8\u5206\u7ed3\u679c',
    'invalid_stream': '\u6a21\u578b\u6d41\u5f0f\u534f\u8bae\u54cd\u5e94\u65e0\u6548',
    'response_too_large': '\u6a21\u578b\u54cd\u5e94\u8d85\u8fc7\u5b89\u5168\u5927\u5c0f\u4e0a\u9650',
    'stream_unsupported': '\u4f9b\u5e94\u5546\u660e\u786e\u4e0d\u652f\u6301\u6d41\u5f0f\u53c2\u6570',
    'unsupported_protocol': '\u6a21\u578b\u54cd\u5e94\u534f\u8bae\u6682\u4e0d\u652f\u6301',
    'configuration_error': '\u6a21\u578b\u4f20\u8f93\u914d\u7f6e\u65e0\u6548',
    'async_context_error': '\u6a21\u578b\u8bf7\u6c42\u8c03\u7528\u4e0a\u4e0b\u6587\u65e0\u6548',
}


def network_category(error):
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLError): return 'certificate_error'
    if isinstance(reason, TimeoutError) or getattr(reason, 'errno', None) == errno.ETIMEDOUT: return 'request_timeout'
    if isinstance(reason, socket.gaierror): return 'dns_error'
    if isinstance(reason, ConnectionError): return 'connection_error'
    return 'network_error'


class LLMRequestError(RuntimeError):
    def __init__(self, status_code: int, attempts: int, category: str,
                 provider_code: str = "", request_id: str = ""):
        self.status_code = status_code
        self.attempts = attempts
        self.category = category if category in ERROR_LABELS else 'network_error'
        self.provider_code = provider_code if provider_code in PROVIDER_ERROR_LABELS else ""
        self.provider_reason = PROVIDER_ERROR_LABELS.get(self.provider_code, "")
        self.request_id = request_id if re.fullmatch(r"[0-9]{20}[A-Za-z0-9]{8,64}", request_id) else ""
        label = ERROR_LABELS[self.category]
        if status_code: label += f'（HTTP {status_code}）'
        if self.provider_reason:
            label += f"（{self.provider_reason}）"
        super().__init__(f"模型请求失败：{label}，已尝试 {attempts} 次；未更换模型或降低思考强度")


def public_failure(error):
    """Allowlisted diagnostics only: never return exception cause, body, URL or credentials."""
    if not isinstance(error, LLMRequestError):
        return None
    return {'error_type': 'LLMRequestError', 'category': error.category,
            'http_status': error.status_code or None, 'attempts': error.attempts,
            'provider_error_code': error.provider_code, 'request_id': error.request_id,
            'message': str(error),
            'transport': safe_transport_diagnostics(getattr(error, 'transport_diagnostics', {}))}


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
                 max_attempts: int = 3, attempt_timeout: float | None = None,
                 protocol: str | None = None, transport_policy: dict | None = None,
                 diagnostics: dict | None = None) -> tuple[dict[str, Any], int, int, int]:
    """Return JSON, HTTP status, end-to-end latency and attempts used.

    All attempts share one timeout budget. Retry-After is respected: when it
    exceeds the remaining budget, fail instead of sending an early retry.
    attempt_timeout caps socket connect/read waits, not a hard wall-clock kill.
    Responses received after the shared deadline are never accepted.
    """
    if transport_policy is not None:
        from .llm_inference_transport import request_inference
        budget = min(timeout, attempt_timeout) if attempt_timeout is not None else timeout
        return request_inference(endpoint, headers, payload, protocol=protocol or 'openai_chat',
                                 policy=transport_policy, timeout=budget, diagnostics=diagnostics)
    if not math.isfinite(timeout) or timeout <= 0 or not 1 <= max_attempts <= 3:
        raise ValueError("Invalid inference timeout or retry limit")
    if attempt_timeout is not None and (isinstance(attempt_timeout, bool) or not math.isfinite(attempt_timeout) or attempt_timeout <= 0):
        raise ValueError('Invalid per-attempt timeout')
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
            request_timeout = min(remaining, attempt_timeout) if attempt_timeout is not None else remaining
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                status = response.getcode()
                decoded = json.loads(response.read().decode("utf-8"))
            if time.monotonic() > deadline:
                raise LLMRequestError(status, attempt, 'deadline_exceeded')
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
            category = network_category(exc)
            failure = exc
            LOG.warning('LLM transport failure category=%s attempt=%s/%s', category, attempt, max_attempts)
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


def safe_transport_diagnostics(value):
    """No provider bodies, endpoints, arbitrary headers or credentials may escape."""
    if not isinstance(value, dict): return {}
    out = {}
    for key in ('attempts','http_status','total_ms','first_byte_ms','first_content_ms','max_gap_ms','bytes_received','heartbeat_count'):
        v=value.get(key)
        if key=='http_status' and not (isinstance(v,int) and not isinstance(v,bool) and 100<=v<=599):continue
        if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0 <= v <= 1e12: out[key]=v
    for key,allowed in {'transport_mode':{'stream','json'},'failure_phase':{'connect','headers','first_byte','body','idle','total','protocol','http','configuration','complete','network','cancelled','async_context','worker'}}.items():
        if value.get(key) in allowed:out[key]=value[key]
    if type(value.get('completion_seen')) is bool:out['completion_seen']=value['completion_seen']
    patterns={'request_id':r'(?:[0-9]{20}[A-Za-z0-9]{8,64}|req_[A-Za-z0-9_-]{8,100}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})',
              'cf_ray':r'[0-9a-fA-F]{16,32}-[A-Z0-9]{2,6}'}
    for key,pattern in patterns.items():
        if isinstance(value.get(key),str) and re.fullmatch(pattern,value[key]):out[key]=value[key]
    return out
