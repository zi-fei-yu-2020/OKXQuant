"""Single-attempt, bounded HTTP inference transport with a synchronous facade.

Inference only: no retries, redirects, parameter substitutions, or partial streamed
results. The sync facade runs a single isolated Python worker and rejects active
event-loop contexts. Its deadline includes process startup, I/O and cleanup. Async
callers can await request_inference_async directly, but own their loop/executor
lifecycle; use the sync subprocess facade for isolation from stuck DNS threads.
"""
from __future__ import annotations
import asyncio, copy, json, math, re, ssl, time
import threading
import os
from pathlib import Path
import subprocess
import sys
import types
from typing import Any
import httpx
# Running this file directly intentionally bypasses package __init__: that file
# imports application configuration, which must never run in the inference worker.
if __name__ == "__main__" and not __package__:
    package = types.ModuleType("okxquant_backend")
    package.__path__ = [str(Path(__file__).resolve().parent)]
    sys.modules["okxquant_backend"] = package
    __package__ = "okxquant_backend"
from .llm_transport import LLMRequestError, PROVIDER_ERROR_LABELS, ERROR_LABELS

DEFAULTS = {"connect_timeout_seconds": 10.0, "first_byte_timeout_seconds": 150.0,
            "idle_timeout_seconds": 90.0, "total_timeout_seconds": 180.0,
            "max_response_bytes": 8_388_608}
MAX_ERROR_BYTES = 8192
ERROR_BODY_TIMEOUT_SECONDS = 1.0
REQUEST_ID_PATTERNS = (re.compile(r"^[0-9]{20}[A-Za-z0-9]{8,64}$"), re.compile(r"^req_[A-Za-z0-9_-]{8,100}$"), re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"))
CF_RAY_RE = re.compile(r"^[0-9a-fA-F]{16,32}-[A-Z0-9]{2,6}$")


def _credentials(headers):
    secrets = []
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() in {"authorization", "api-key", "x-api-key", "x-goog-api-key"} and isinstance(value, str) and value:
            secrets.append(value.strip())
            if key.lower() == "authorization":
                parts = value.split(None, 1)
                if len(parts) == 2: secrets.append(parts[1].strip())
    return tuple(secret for secret in secrets if secret)


def _safe_id(value, secrets=(), ray=False):
    pattern_ok = CF_RAY_RE.fullmatch(value or "") if ray else any(p.fullmatch(value or "") for p in REQUEST_ID_PATTERNS)
    if not pattern_ok or any(secret.casefold() in value.casefold() for secret in secrets): return ""
    return value


def _safe_header(headers: httpx.Headers, secrets, *names: str, ray=False) -> str:
    for name in names:
        safe = _safe_id(headers.get(name, ""), secrets, ray=ray)
        if safe: return safe
    return ""


def _policy(policy: dict[str, Any], timeout: float):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    result = {}
    for key, default in DEFAULTS.items():
        value = policy.get(key, default)
        if key == "max_response_bytes":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0: raise ValueError("max_response_bytes must be a positive integer")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a finite positive number")
        result[key] = value
    if policy.get("mode") not in ("stream", "json"): raise ValueError("policy mode must be 'stream' or 'json'")
    result["mode"] = policy["mode"]
    return result, min(float(timeout), float(result["total_timeout_seconds"]))


def _new_error(status, category, request_id="", provider_code="", *, attempts=1):
    error = LLMRequestError(status, attempts, category, provider_code, request_id)
    error.category = category
    error.request_id = _safe_id(request_id)
    error.__suppress_context__ = True
    return error


def _raise(status, category, diag, target, request_id="", provider_code=""):
    diag["failure_phase"] = diag.get("failure_phase") or "protocol"
    started = diag.pop("_started_at", None)
    if started is not None: diag["total_ms"] = round((time.monotonic() - started) * 1000)
    secrets = diag.pop("_secrets", ())
    diag["request_id"] = _safe_id(diag.get("request_id", ""), secrets)
    diag["cf_ray"] = _safe_id(diag.get("cf_ray", ""), secrets, ray=True)
    request_id = _safe_id(request_id, secrets)
    if target is not None: target.update(diag)
    error = _new_error(status, category, request_id, provider_code, attempts=diag.get("attempts", 1))
    error.transport_diagnostics = dict(diag)
    raise error


def _decode_error(body: bytes):
    provider = request_id = ""
    unsupported = False
    try:
        doc = json.loads(body.decode("utf-8")); detail = doc.get("error") if isinstance(doc, dict) else None
        if isinstance(detail, dict):
            code = detail.get("code")
            if isinstance(code, str) and code in PROVIDER_ERROR_LABELS: provider = code
            rid = detail.get("request_id")
            if not isinstance(rid, str) and isinstance(doc, dict): rid = doc.get("request_id")
            if isinstance(rid, str): request_id = rid
            unsupported = code in {"unsupported_parameter", "unsupported_value"} and detail.get("param") == "stream"
    except (UnicodeError, ValueError, TypeError): pass
    return provider, request_id, unsupported


def _stream_category(source):
    if source == "truncated_stream": return "stream_truncated"
    if source in {"event_too_large", "response_too_large"}: return "response_too_large"
    if source in {"invalid_terminal", "unsupported_finish", "incomplete_response"}: return "stream_incomplete"
    if source == "provider_error": return "stream_error"
    return "invalid_stream"


def _set_mode(payload, mode):
    result = copy.deepcopy(payload); result["stream"] = mode == "stream"; return result


async def _close_bounded(resource, deadline, maximum=0.1):
    if resource is None: return
    async def close_resource():
        await resource.aclose()
    task = asyncio.create_task(close_resource())
    remaining = min(maximum, max(0.0, deadline - time.monotonic()))
    try:
        if remaining > 0:
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if task in done:
                try: task.result()
                except BaseException: pass
            else:
                task.cancel()
                task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
        else:
            await asyncio.sleep(0)
            if not task.done(): task.cancel()
            task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
    except asyncio.CancelledError:
        if not task.done(): task.cancel()
        raise


async def request_inference_async(endpoint: str, headers: dict[str, str], payload: dict[str, Any], *,
        protocol: str, policy: dict[str, Any], timeout: float = 180.0,
        diagnostics: dict[str, Any] | None = None, transport: httpx.AsyncBaseTransport | None = None):
    """Async single-attempt API. Optional transport exists for deterministic tests."""
    try:
        if not isinstance(payload, dict) or not isinstance(headers, dict) or not isinstance(policy, dict):
            raise ValueError("invalid request configuration")
        limits, budget = _policy(policy, timeout)
    except (TypeError, ValueError):
        safe_diag = {"transport_mode": policy.get("mode") if isinstance(policy, dict) and policy.get("mode") in ("stream", "json") else None,
                     "attempts": 0, "failure_phase": "configuration", "http_status": None}
        if diagnostics is not None: diagnostics.update(safe_diag)
        error = _new_error(0, "configuration_error", attempts=0)
        error.transport_diagnostics = dict(safe_diag)
        raise error from None
    mode = limits["mode"]
    if not isinstance(protocol, str) or protocol not in {"openai_chat", "openai_responses", "claude_messages"}:
        error = _new_error(0, "unsupported_protocol", attempts=0)
        error.transport_diagnostics = {"transport_mode": mode, "attempts": 0, "failure_phase": "configuration", "http_status": None}
        if diagnostics is not None: diagnostics.update(error.transport_diagnostics)
        raise error
    diag = {"transport_mode": mode, "attempts": 1, "http_status": None, "total_ms": 0,
            "first_byte_ms": None, "first_content_ms": None, "max_gap_ms": 0, "bytes_received": 0,
            "heartbeat_count": 0, "completion_seen": False, "failure_phase": None, "request_id": "", "cf_ray": "", "_secrets": _credentials(headers)}
    start = time.monotonic(); diag["_started_at"] = start; deadline = start + budget; status = 0; response = None; client = None; request_started = False; iterator = None; error_iter = None

    async def wait_phase(awaitable, phase_deadline, phase):
        remaining = min(deadline, phase_deadline) - time.monotonic()
        if remaining <= 0:
            if hasattr(awaitable, "close"): awaitable.close()
            raise asyncio.TimeoutError(phase)
        try: return await asyncio.wait_for(awaitable, remaining)
        except asyncio.TimeoutError as exc: raise asyncio.TimeoutError(phase) from exc

    try:
        client = httpx.AsyncClient(transport=transport, follow_redirects=False,
            timeout=httpx.Timeout(connect=float(limits["connect_timeout_seconds"]), read=None, write=None, pool=None))
        request_headers = dict(headers)
        request_headers.setdefault("Accept-Encoding", "identity")
        req = client.build_request("POST", endpoint, headers=request_headers, json=_set_mode(payload, mode))
        first_deadline = min(deadline, start + float(limits["first_byte_timeout_seconds"]))
        try:
            request_started = True
            response = await wait_phase(client.send(req, stream=True), first_deadline, "first_byte")
        except asyncio.TimeoutError:
            diag["failure_phase"] = "headers"
            _raise(status, "total_timeout" if time.monotonic() >= deadline else "first_byte_timeout", diag, diagnostics)
        status = response.status_code; diag["http_status"] = status
        diag["request_id"] = _safe_header(response.headers, diag["_secrets"], "x-request-id", "x-oneapi-request-id")
        diag["cf_ray"] = _safe_header(response.headers, diag["_secrets"], "cf-ray", ray=True)
        if not 200 <= status < 300:
            body = bytearray()
            error_iter = response.aiter_bytes().__aiter__()
            error_deadline = min(deadline, time.monotonic() + ERROR_BODY_TIMEOUT_SECONDS)
            try:
                while len(body) < MAX_ERROR_BYTES:
                    remaining_error_budget = error_deadline - time.monotonic()
                    if remaining_error_budget <= 0:
                        break
                    chunk = await asyncio.wait_for(error_iter.__anext__(), remaining_error_budget)
                    body.extend(chunk[:MAX_ERROR_BYTES-len(body)])
            except (StopAsyncIteration, asyncio.TimeoutError): pass
            except Exception: pass
            diag["bytes_received"] = len(body)
            provider, body_id, unsupported = _decode_error(bytes(body))
            body_id = _safe_id(body_id, diag["_secrets"])
            if not diag["request_id"]: diag["request_id"] = body_id
            diag["failure_phase"] = "http"
            cat = "stream_unsupported" if mode == "stream" and status in (400, 422) and unsupported else "http_error"
            _raise(status, cat, diag, diagnostics, diag["request_id"], provider)

        decoder = assembler = None
        if mode == "stream":
            from .llm_stream import SSEDecoder, StreamAssembler, StreamProtocolError
            decoder = SSEDecoder(max_event_bytes=1_048_576, max_response_bytes=int(limits["max_response_bytes"]))
            assembler = StreamAssembler(protocol)
        raw = bytearray(); last = time.monotonic(); content_marked = False

        async def consume(chunk):
            nonlocal last, content_marked
            now = time.monotonic(); diag["max_gap_ms"] = max(diag["max_gap_ms"], round((now-last)*1000)); last = now
            if diag["first_byte_ms"] is None: diag["first_byte_ms"] = round((now-start)*1000)
            diag["bytes_received"] += len(chunk)
            if diag["bytes_received"] > limits["max_response_bytes"]: _raise(status, "response_too_large", diag, diagnostics)
            if mode == "json": raw.extend(chunk); return
            try:
                events = decoder.feed(chunk); diag["heartbeat_count"] = decoder.heartbeat_count
                for event, data in events:
                    assembler.feed(event, data)
                    if assembler.content_started and not content_marked:
                        diag["first_content_ms"] = round((now-start)*1000)
                        content_marked = True
                # All events already decoded from this chunk must be validated,
                # even after a terminal. Do not hide buffered errors/contradictions.
                if assembler.complete: decoder.validate_terminal_tail()
                diag["completion_seen"] = assembler.complete
            except StreamProtocolError as exc:
                _raise(status, _stream_category(getattr(exc, "category", "malformed_stream")), diag, diagnostics)

        iterator = response.aiter_bytes().__aiter__()
        while not (assembler is not None and assembler.complete):
            phase = "first_byte" if diag["first_byte_ms"] is None else "idle"
            phase_deadline = first_deadline if phase == "first_byte" else min(deadline, last + float(limits["idle_timeout_seconds"]))
            try: chunk = await wait_phase(iterator.__anext__(), phase_deadline, phase)
            except StopAsyncIteration: break
            except asyncio.TimeoutError:
                diag["failure_phase"] = "total" if time.monotonic() >= deadline else phase
                category = "total_timeout" if time.monotonic() >= deadline else ("idle_timeout" if phase == "idle" else "first_byte_timeout")
                _raise(status, category, diag, diagnostics)
            await consume(chunk)
            if assembler is not None and assembler.complete:
                break
        if mode == "stream":
            if not assembler.complete:
                try:
                    for event, data in decoder.finish(): assembler.feed(event, data)
                except StreamProtocolError as exc:
                    _raise(status, _stream_category(getattr(exc, "category", "truncated_stream")), diag, diagnostics)
                if not assembler.complete: _raise(status, "stream_truncated", diag, diagnostics)
                diag["completion_seen"] = True
            result = assembler.result()
        else:
            try: result = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError): _raise(status, "invalid_json_response", diag, diagnostics)
            if not isinstance(result, dict) or result.get("error"): _raise(status, "invalid_response", diag, diagnostics)
        diag["total_ms"] = round((time.monotonic()-start)*1000)
        if time.monotonic() > deadline: _raise(status, "total_timeout", diag, diagnostics)
        diag.pop("_started_at", None)
        secrets = diag.pop("_secrets", ())
        diag["request_id"] = _safe_id(diag.get("request_id", ""), secrets)
        diag["cf_ray"] = _safe_id(diag.get("cf_ray", ""), secrets, ray=True)
        if diagnostics is not None: diagnostics.update(diag)
        return result, status, diag["total_ms"], 1
    except LLMRequestError: raise
    except httpx.InvalidURL:
        diag["attempts"] = int(request_started)
        diag["failure_phase"] = "configuration"
        _raise(status, "configuration_error", diag, diagnostics)
    except httpx.TimeoutException as exc:
        is_connect = isinstance(exc, httpx.ConnectTimeout)
        diag["failure_phase"] = "connect" if is_connect else "headers"
        category = "total_timeout" if time.monotonic() >= deadline else ("connect_timeout" if is_connect else "first_byte_timeout")
        _raise(status, category, diag, diagnostics)
    except httpx.ConnectError as exc:
        diag["failure_phase"] = "connect"
        _raise(status, "certificate_error" if isinstance(exc.__cause__, ssl.SSLError) else "connection_error", diag, diagnostics)
    except httpx.HTTPError:
        diag["failure_phase"] = "network"; _raise(status, "network_error", diag, diagnostics)
    except asyncio.CancelledError:
        diag["failure_phase"] = "cancelled"
        diag["total_ms"] = round((time.monotonic()-start)*1000)
        diag.pop("_started_at", None)
        secrets = diag.pop("_secrets", ())
        diag["request_id"] = _safe_id(diag.get("request_id", ""), secrets)
        diag["cf_ray"] = _safe_id(diag.get("cf_ray", ""), secrets, ray=True)
        if diagnostics is not None: diagnostics.update(diag)
        raise
    except Exception:
        if not request_started:
            diag["attempts"] = 0
            diag["failure_phase"] = "configuration"
            _raise(status, "configuration_error", diag, diagnostics)
        diag["failure_phase"] = diag.get("failure_phase") or "protocol"
        _raise(status, "invalid_stream" if mode == "stream" else "invalid_response", diag, diagnostics)
    finally:
        diag["total_ms"] = round((time.monotonic()-start)*1000)
        try:
            await _close_bounded(iterator, deadline)
            await _close_bounded(error_iter, deadline)
            await _close_bounded(response, deadline)
        finally:
            await _close_bounded(client, deadline)



def _public_diagnostics(value, secrets=()):
    """Allowlist worker metadata again in the parent; never trust arbitrary IPC keys."""
    if not isinstance(value, dict): return {}
    result = {}
    for key in ("attempts", "http_status", "total_ms", "first_byte_ms", "first_content_ms",
                "max_gap_ms", "bytes_received", "heartbeat_count"):
        number = value.get(key)
        if type(number) in (int, float) and math.isfinite(number) and 0 <= number <= 1e12:
            result[key] = number
    if value.get("transport_mode") in ("stream", "json"):
        result["transport_mode"] = value["transport_mode"]
    phase = value.get("failure_phase")
    if isinstance(phase, str) and phase in {"connect", "headers", "first_byte", "body", "idle",
            "total", "protocol", "http", "configuration", "complete", "cancelled", "async_context", "network", "worker"}:
        result["failure_phase"] = phase
    if type(value.get("completion_seen")) is bool:
        result["completion_seen"] = value["completion_seen"]
    for key in ("request_id", "cf_ray"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            result[key] = _safe_id(candidate, secrets, ray=key == "cf_ray")
    return result


def _kill_reap(process):
    """No child is left behind on timeout, invalid IPC, or parent cancellation."""
    if process.poll() is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    # After kill, drain pipes and reap. No executor/thread is used in this parent.
    try:
        process.communicate()
    except Exception:
        # Broken pipes must not replace the safe primary error. The child has
        # already been killed; reap without depending on its pipe state.
        process.wait()


def _close_pipes(process):
    for name in ("stdin", "stdout", "stderr"):
        pipe = getattr(process, name, None)
        if pipe is not None:
            try: pipe.close()
            except Exception: pass


def _worker_environment():
    # Forward runtime/CA/proxy settings, not unrelated exchange/model credentials.
    allowed={'PATH','SYSTEMROOT','WINDIR','HOME','USERPROFILE','APPDATA','LOCALAPPDATA',
             'LD_LIBRARY_PATH','PYTHONPATH','LANG','LC_ALL','TMP','TEMP','TMPDIR',
             'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR',
             'REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE'}
    env={key:value for key,value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONUNBUFFERED='1')
    return env


def request_inference(endpoint: str, headers: dict[str, str], payload: dict[str, Any], *,
        protocol: str, policy: dict[str, Any], timeout: float = 180.0,
        diagnostics: dict[str, Any] | None = None) -> tuple[dict[str, Any], int, int, int]:
    """One isolated worker, one HTTP attempt, never a retry or fallback.

    Startup and pipe I/O consume min(timeout, policy total). Credentials/payload
    travel only over stdin, never argv or temporary files. A timeout kills/reaps
    the worker, including blocked resolver/executor threads. Blocking this facade
    inside an event loop is rejected; async clients own their executor lifecycle.
    """
    started = time.monotonic()
    process = None
    supervisor = None
    expired = threading.Event()
    secrets = _credentials(headers) if isinstance(headers, dict) else ()
    mode = policy.get("mode") if isinstance(policy, dict) else None
    diag = {"attempts": 0, "completion_seen": False}
    if isinstance(mode, str) and mode in {"stream", "json"}: diag["transport_mode"] = mode

    def fail(category, status=0, provider_code="", request_id="", phase="worker"):
        diag["failure_phase"] = phase
        diag["total_ms"] = round((time.monotonic() - started) * 1000)
        diag["http_status"] = status
        safe = _public_diagnostics(diag, secrets)
        if diagnostics is not None: diagnostics.update(safe)
        error = _new_error(status, category, _safe_id(request_id, secrets), provider_code, attempts=diag["attempts"])
        error.transport_diagnostics = safe
        raise error from None

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        fail("async_context_error", phase="async_context")
    try:
        if not isinstance(headers, dict) or not isinstance(payload, dict) or not isinstance(policy, dict):
            raise ValueError("invalid request configuration")
        limits, budget = _policy(policy, timeout)
        if not isinstance(protocol, str) or protocol not in {"openai_chat", "openai_responses", "claude_messages"}:
            fail("unsupported_protocol", phase="configuration")
        deadline = started + budget
        wire_request = json.dumps({"endpoint": endpoint, "headers": headers, "payload": payload,
            "protocol": protocol, "policy": limits, "timeout": budget, "deadline": deadline},
            ensure_ascii=True, allow_nan=False).encode("utf-8")
    except LLMRequestError:
        raise
    except Exception:
        fail("configuration_error", phase="configuration")

    try:
        if time.monotonic() >= deadline: fail("total_timeout", phase="total")
        # Direct file execution bypasses okxquant_backend.__init__ / config imports.
        process = subprocess.Popen(
            [sys.executable, "-B", "-u", str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True, env=_worker_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # A launched worker may already have sent the request: unknown outcomes
        # count as one, even when the parent must kill it during startup.
        diag["attempts"] = 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_reap(process)
            fail("total_timeout", phase="total")
        try:
            # Windows communicate may block in stdin.write before timed waits.
            # This supervisor kills the child independently, releasing the pipe.
            def deadline_expired():
                expired.set()
                try:
                    if process.poll() is None: process.kill()
                except ProcessLookupError:
                    pass
            supervisor = threading.Timer(remaining, deadline_expired)
            supervisor.daemon = True
            supervisor.start()
            output, _ = process.communicate(input=wire_request, timeout=remaining)
            if expired.is_set() or time.monotonic() >= deadline:
                fail('total_timeout', phase='total')
        except subprocess.TimeoutExpired:
            _kill_reap(process)
            fail("total_timeout", phase="total")
        if time.monotonic() >= deadline:
            fail("total_timeout", phase="total")
        if process.returncode != 0:
            fail("network_error", phase="worker")
        # The worker is trusted code with bounded input response bytes; this also
        # rejects unexpectedly large/corrupt IPC before JSON decoding.
        if not isinstance(output, bytes) or len(output) > 8 * limits["max_response_bytes"] + 65536:
            fail("invalid_response")
        try:
            envelope = json.loads(output)
        except (ValueError, UnicodeError):
            fail("invalid_response")
        if not isinstance(envelope, dict) or type(envelope.get("version")) is not int or envelope["version"] != 1:
            fail("invalid_response")
        if type(envelope.get("ok")) is not bool:
            fail("invalid_response")
        diag.update(_public_diagnostics(envelope.get("diagnostics"), secrets))
        diag["attempts"] = 1
        diag["transport_mode"] = mode
        if not envelope["ok"]:
            detail = envelope.get("error")
            if not isinstance(detail, dict): fail("invalid_response")
            category, status = detail.get("category"), detail.get("status_code")
            attempts = detail.get("attempts", 1)
            if type(attempts) is not int or attempts not in (0, 1): fail("invalid_response")
            if not isinstance(category, str) or category not in ERROR_LABELS:
                fail("invalid_response")
            if type(status) is not int or (status != 0 and not 100 <= status <= 599):
                fail("invalid_response")
            provider = detail.get("provider_code")
            provider = provider if isinstance(provider, str) and provider in PROVIDER_ERROR_LABELS else ""
            rid = detail.get("request_id")
            rid = _safe_id(rid, secrets) if isinstance(rid, str) else ""
            diag["attempts"] = attempts
            fail(category, status, provider, rid, diag.get("failure_phase", "worker"))
        response, status = envelope.get("response"), envelope.get("status_code")
        if not isinstance(response, dict) or response.get("error"):
            fail("invalid_response")
        if type(status) is not int or not 200 <= status < 300 or type(envelope.get("attempts")) is not int or envelope["attempts"] != 1:
            fail("invalid_response")
        if mode == "stream" and diag.get("completion_seen") is not True:
            fail("stream_incomplete", status)
        if time.monotonic() >= deadline: fail("total_timeout", phase="total")
        diag["http_status"] = status
        diag["total_ms"] = round((time.monotonic() - started) * 1000)
        safe = _public_diagnostics(diag, secrets)
        if diagnostics is not None: diagnostics.update(safe)
        return response, status, diag["total_ms"], 1
    except LLMRequestError:
        raise
    except Exception:
        if process is not None:
            _kill_reap(process)
        fail("configuration_error" if process is None else "network_error", phase="configuration" if process is None else "worker")
    finally:
        if supervisor is not None:
            supervisor.cancel()
            if supervisor.ident is not None:
                supervisor.join(timeout=0.2)
        if process is not None:
            try:
                if process.poll() is None: _kill_reap(process)
            finally:
                _close_pipes(process)


def _emit_worker_and_exit(envelope):
    """Flush IPC *inside* the coroutine; never wait for default-executor shutdown."""
    try:
        encoded = json.dumps(envelope, ensure_ascii=True, allow_nan=False).encode("utf-8")
    except Exception:
        encoded = b'{"version":1,"ok":false,"error":{"category":"invalid_response","status_code":0},"diagnostics":{}}'
    try:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    finally:
        os._exit(0)


async def _worker_main():
    """Private stdin/stdout protocol. Never import application/trading configuration."""
    diagnostics = {}
    secrets = ()
    invoked = False
    try:
        request = json.loads(sys.stdin.buffer.read())
        if not isinstance(request, dict): raise ValueError("invalid worker request")
        secrets = _credentials(request["headers"])
        remaining = min(request["timeout"], request["deadline"] - time.monotonic())
        if not math.isfinite(remaining) or remaining <= 0:
            diagnostics.update({"failure_phase": "total", "attempts": 0})
            raise _new_error(0, "total_timeout", attempts=0)
        invoked = True
        response, status, _, attempts = await request_inference_async(
            request["endpoint"], request["headers"], request["payload"], protocol=request["protocol"],
            policy=request["policy"], timeout=remaining, diagnostics=diagnostics)
        # The await above does not finish until the core's finally-cleanup ran.
        envelope = {"version": 1, "ok": True, "response": response, "status_code": status,
                    "attempts": attempts, "diagnostics": _public_diagnostics(diagnostics, secrets)}
    except LLMRequestError as error:
        diagnostics.update(getattr(error, "transport_diagnostics", {}))
        envelope = {"version": 1, "ok": False, "error": {"category": error.category,
            "status_code": error.status_code, "attempts": error.attempts, "provider_code": error.provider_code,
            "request_id": _safe_id(error.request_id, secrets)},
            "diagnostics": _public_diagnostics(diagnostics, secrets)}
    except BaseException:
        attempts = int(invoked)
        envelope = {"version": 1, "ok": False,
            "error": {"category": "network_error" if invoked else "configuration_error", "status_code": 0, "attempts": attempts},
            "diagnostics": {"attempts": attempts, "failure_phase": "worker" if invoked else "configuration"}}
    _emit_worker_and_exit(envelope)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        sys.exit(2)
    asyncio.run(_worker_main())
