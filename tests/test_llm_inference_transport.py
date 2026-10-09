import asyncio
import gzip
import io
import subprocess
import json
import time
import types
import unittest
from unittest.mock import patch

import httpx

from okxquant_backend.llm_inference_transport import request_inference, request_inference_async
from okxquant_backend.llm_transport import LLMRequestError
from okxquant_backend import llm_inference_transport as transport_module


class DelayedStream(httpx.AsyncByteStream):
    def __init__(self, parts): self.parts = parts; self.closed = False
    async def __aiter__(self):
        for delay, data in self.parts:
            if delay: await asyncio.sleep(delay)
            yield data
    async def aclose(self): self.closed = True


class InferenceTransportTests(unittest.TestCase):
    endpoint = "https://inference.invalid/v1/chat/completions"
    payload = {"model": "unchanged", "reasoning_effort": "high", "max_tokens": 77,
               "messages": [{"role": "user", "content": "private prompt"}]}

    def test_nonstream_json_preserves_parameters_and_returns_one_attempt(self):
        seen = {}
        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, stream=DelayedStream([(0, b'{"ok":true}')]))
        async def invoke():
            return await request_inference_async(self.endpoint, {"Authorization": "Bearer secret"}, self.payload,
                protocol="openai_chat", policy={"mode": "json"}, transport=httpx.MockTransport(handler))
        result = asyncio.run(invoke())
        self.assertEqual(result[:2], ({"ok": True}, 200))
        self.assertEqual(result[3], 1)
        self.assertEqual(seen["body"]["stream"], False)
        for key in ("model", "reasoning_effort", "max_tokens", "messages"):
            self.assertEqual(seen["body"][key], self.payload[key])
        self.assertNotIn("stream_options", seen["body"])
        self.assertNotIn("stream", self.payload)

    def test_gzip_json_is_decompressed_and_still_bounded(self):
        packed = gzip.compress(b'{"ok":true}')
        seen = {}
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode":"json"}, transport=httpx.MockTransport(lambda req: (seen.update({"encoding":req.headers.get("accept-encoding")}) or httpx.Response(200, headers={"content-encoding":"gzip"}, content=packed))))
        result = asyncio.run(invoke())
        self.assertEqual(result[0], {"ok":True})
        self.assertEqual(seen["encoding"], "identity")

    def test_gzip_sse_is_decompressed_before_incremental_parse(self):
        raw = b'data: {"choices":[{"delta":{"content":"zip"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        packed = gzip.compress(raw)
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode":"stream"}, transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={"content-encoding":"gzip"}, content=packed)))
        result = asyncio.run(invoke())
        self.assertTrue(result[0])

    def test_nonstream_byte_cap_is_enforced_and_diagnostic_survives(self):
        diag = {}
        body = DelayedStream([(0, b'{"long":"abcdefghijk"}')])
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode": "json", "max_response_bytes": 8}, diagnostics=diag,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=body)))
        with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
        self.assertEqual(caught.exception.category, "response_too_large")
        self.assertGreater(diag["bytes_received"], 8)
        self.assertEqual(caught.exception.transport_diagnostics["http_status"], 200)

    def test_slow_response_close_is_bounded_and_does_not_mask_http_status(self):
        class SlowClose(httpx.AsyncByteStream):
            async def __aiter__(self): yield b'{"error":{"code":"safe"}}'
            async def aclose(self): await asyncio.sleep(5)
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode":"json"}, transport=httpx.MockTransport(lambda req: httpx.Response(524, stream=SlowClose())))
        started = time.monotonic()
        with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
        self.assertLess(time.monotonic()-started, 2.0)
        self.assertEqual(caught.exception.status_code, 524)
        self.assertEqual(caught.exception.category, "http_error")

    def test_http_524_is_single_attempt_and_redacted(self):
        calls = []
        error = b'{"request_id":"req_abcdefgh-123","error":{"code":"server_error","message":"Bearer secret private prompt"}}'
        async def invoke():
            return await request_inference_async(self.endpoint, {"Authorization": "Bearer secret"}, self.payload,
                protocol="openai_chat", policy={"mode": "json"}, transport=httpx.MockTransport(
                    lambda req: calls.append(req) or httpx.Response(524, content=error)))
        with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
        self.assertEqual(caught.exception.status_code, 524)
        self.assertEqual(caught.exception.attempts, 1)
        self.assertEqual(caught.exception.request_id, "req_abcdefgh-123")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("private prompt", str(caught.exception))

    def test_explicit_stream_unsupported_only(self):
        def response(body): return httpx.Response(400, stream=DelayedStream([(0, body)]))
        for body, expected in [
            (b'{"error":{"code":"unsupported_parameter","param":"stream"}}', "stream_unsupported"),
            (b'{"error":{"code":"bad_request","message":"unsupported stream"}}', "http_error"),
            (b'{"error":{"code":"unsupported_parameter","param":"model"}}', "http_error"),
        ]:
            async def invoke(body=body):
                return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                    policy={"mode":"stream"}, transport=httpx.MockTransport(lambda req: response(body)))
            with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
            self.assertEqual(caught.exception.category, expected)

    def test_allowlisted_metadata_shapes_and_secret_echo_are_filtered(self):
        payload = b'{"ok":true}'
        async def invoke(rid, ray):
            return await request_inference_async(self.endpoint, {"Authorization":"Bearer req_SECRETKEY123", "X-API-Key":"api_SECRETKEY123"}, self.payload,
                protocol="openai_chat", policy={"mode":"json"}, diagnostics=diag,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={"x-request-id":rid, "cf-ray":ray}, stream=DelayedStream([(0, payload)]))))
        for rid, ray, expected_id, expected_ray in [
            ("req_abcdefgh-123", "0123456789abcdef-IAD", "req_abcdefgh-123", "0123456789abcdef-IAD"),
            ("arbitrary-printable", "ray-123", "", ""),
            ("req_SECRETKEY123", "0123456789abcdef-IAD", "", "0123456789abcdef-IAD"),
        ]:
            diag = {}
            asyncio.run(invoke(rid, ray))
            self.assertEqual(diag["request_id"], expected_id)
            self.assertEqual(diag["cf_ray"], expected_ray)
            self.assertNotIn("_secrets", diag)

    def test_sync_facade_rejects_running_loop(self):
        async def check():
            with self.assertRaises(LLMRequestError) as caught:
                request_inference(self.endpoint, {}, self.payload, protocol="openai_chat", policy={"mode":"json"})
            self.assertEqual(caught.exception.category, "async_context_error")
            self.assertEqual(caught.exception.attempts, 0)
            self.assertEqual(caught.exception.transport_diagnostics["attempts"], 0)
            self.assertIn(" 0 ", str(caught.exception))
        asyncio.run(check())

    def test_policy_bounds_validated_before_transport(self):
        for value in (0, -1, float("inf"), True):
            async def invoke():
                return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                    policy={"mode":"json", "idle_timeout_seconds":value}, transport=httpx.MockTransport(lambda req: self.fail("sent")))
            with self.subTest(value=value), self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
            self.assertEqual(caught.exception.category, "configuration_error")


class StreamingProtocolTests(unittest.TestCase):
    endpoint = "https://inference.invalid/v1/chat/completions"
    payload = {"model":"m", "messages":[]}

    def test_fragmented_sse_assembles_only_after_terminal(self):
        chunks = [b': heartbeat\r\n\r\ndata: {"choices":[{"delta":{"content":"hel',
                  b'lo"},"finish_reason":null}]}\n\n', b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n', b'data: [DONE]\n\n']
        stream = DelayedStream([(0, part) for part in chunks])
        diag = {}
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode":"stream"}, diagnostics=diag,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)))
        result, status, _, attempts = asyncio.run(invoke())
        self.assertEqual(status, 200); self.assertEqual(attempts, 1)
        self.assertTrue(result)
        self.assertTrue(diag["completion_seen"])
        self.assertEqual(diag["heartbeat_count"], 1)
        self.assertTrue(stream.closed)

    def test_stops_reading_immediately_at_terminal(self):
        stream = DelayedStream([(0, b'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'), (1.0, b'data: {"error":"late"}\n\n')])
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode":"stream", "idle_timeout_seconds":2}, transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)))
        result = asyncio.run(invoke())
        self.assertTrue(result[0])
        self.assertTrue(stream.closed)

    def test_html_or_json_success_body_is_not_accepted_as_stream(self):
        for body in (b'<html>not an event stream</html>', b'{"choices":[{"message":{"content":"ok"}}]}'):
            async def invoke(body=body):
                return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                    policy={"mode":"stream"}, transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=DelayedStream([(0, body)]))))
            with self.assertRaises(LLMRequestError): asyncio.run(invoke())

    def test_truncated_sse_never_returns_partial_output(self):
        stream = DelayedStream([(0, b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')])
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode":"stream"}, transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)))
        with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
        self.assertEqual(caught.exception.category, "stream_truncated")
        self.assertTrue(stream.closed)

    def test_idle_and_total_deadlines_with_heartbeats(self):
        cases = [([(0, b': ping\n\n'), (.12, b': ping\n\n')], {"idle_timeout_seconds":.04, "total_timeout_seconds":.5}),
                 ([(0, b': ping\n\n'), (.04, b': ping\n\n'), (.04, b': ping\n\n'), (.04, b': ping\n\n')], {"idle_timeout_seconds":.2, "total_timeout_seconds":.09})]
        for parts, bounds in cases:
            stream = DelayedStream(parts)
            async def invoke():
                return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                    policy={"mode":"stream", "first_byte_timeout_seconds":.5, **bounds}, timeout=.5,
                    transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)))
            with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
            expected = "idle_timeout" if bounds["idle_timeout_seconds"] < .1 else "total_timeout"
            self.assertEqual(caught.exception.category, expected)
            self.assertTrue(stream.closed)

    def test_first_body_byte_deadline_after_headers(self):
        stream = DelayedStream([(.08, b'{"ok":true}')])
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode":"json", "first_byte_timeout_seconds":.02}, timeout=.5,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)))
        with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
        self.assertEqual(caught.exception.category, "first_byte_timeout")
        self.assertTrue(stream.closed)

    def test_cancellation_closes_response_stream_and_preserves_safe_diagnostics(self):
        class HangingStream(httpx.AsyncByteStream):
            def __init__(self): self.started = asyncio.Event(); self.closed = False
            async def __aiter__(self):
                self.started.set()
                await asyncio.Event().wait()
                yield b""
            async def aclose(self): self.closed = True
        stream = HangingStream(); diag = {}
        async def invoke():
            return await request_inference_async(self.endpoint, {"Authorization":"Bearer secret"}, self.payload,
                protocol="openai_chat", policy={"mode":"json"}, diagnostics=diag,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)))
        async def run_cancel():
            task = asyncio.create_task(invoke())
            await asyncio.wait_for(stream.started.wait(), .2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        asyncio.run(run_cancel())
        self.assertTrue(stream.closed)
        self.assertEqual(diag["failure_phase"], "cancelled")
        self.assertNotIn("_secrets", diag)

    def test_first_byte_deadline_includes_wait_for_headers(self):
        async def handler(req):
            await asyncio.sleep(.08)
            return httpx.Response(200, stream=DelayedStream([]))
        async def invoke():
            return await request_inference_async(self.endpoint, {}, self.payload, protocol="openai_chat",
                policy={"mode":"json", "first_byte_timeout_seconds":.02}, timeout=.5,
                transport=httpx.MockTransport(handler))
        with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
        self.assertEqual(caught.exception.category, "first_byte_timeout")




class BufferedEventAndErrorDeadlineTests(unittest.TestCase):
    def test_rejects_every_contradiction_already_decoded_after_terminal(self):
        terminal = b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        for suffix in (b'event: error\ndata: {"message":"PRIVATE"}\n\n',
                       b'data: [DONE]\n\n',
                       b'data: {"choices":[{"delta":{"content":"extra"}}]}\n\n'):
            body = DelayedStream([(0, terminal + suffix)])
            diag = {}
            async def invoke():
                return await request_inference_async("https://offline.invalid", {}, {}, protocol="openai_chat",
                    policy={"mode":"stream"}, diagnostics=diag,
                    transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=body)))
            with self.subTest(suffix=suffix), self.assertRaises(LLMRequestError) as caught:
                asyncio.run(invoke())
            self.assertIn(caught.exception.category, {"stream_error", "invalid_stream"})
            self.assertFalse(diag["completion_seen"])
            self.assertTrue(body.closed)
            self.assertNotIn("PRIVATE", str(caught.exception))

    def test_absolute_error_deadline_is_not_restarted_for_ready_bytes(self):
        offset = [0.0]
        received = [0]
        real_monotonic = time.monotonic
        class ImmediateTrickle(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(100):
                    received[0] += 1
                    offset[0] += .05
                    yield b"x"
            async def aclose(self): pass
        async def invoke():
            return await request_inference_async("https://offline.invalid", {}, {}, protocol="openai_chat",
                policy={"mode":"json"}, diagnostics=diag,
                transport=httpx.MockTransport(lambda req: httpx.Response(524, stream=ImmediateTrickle())))
        diag = {}
        with patch.object(transport_module, "ERROR_BODY_TIMEOUT_SECONDS", .02), \
             patch.object(transport_module.time, "monotonic", side_effect=lambda: real_monotonic() + offset[0]):
            with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
        self.assertEqual(caught.exception.category, "http_error")
        self.assertEqual(caught.exception.status_code, 524)
        self.assertLessEqual(received[0], 1)
        self.assertEqual(diag["failure_phase"], "http")

    def test_stalled_error_body_preserves_524(self):
        body = DelayedStream([(.2, b'PRIVATE')])
        async def invoke():
            return await request_inference_async("https://offline.invalid", {}, {}, protocol="openai_chat",
                policy={"mode":"json"}, transport=httpx.MockTransport(lambda req: httpx.Response(524, stream=body)))
        with patch.object(transport_module, "ERROR_BODY_TIMEOUT_SECONDS", .02):
            with self.assertRaises(LLMRequestError) as caught: asyncio.run(invoke())
        self.assertEqual(caught.exception.status_code, 524)
        self.assertEqual(caught.exception.category, "http_error")
        self.assertTrue(body.closed)

    def test_client_creation_exception_is_sanitized(self):
        diag = {}
        with patch.object(transport_module.httpx, "AsyncClient", side_effect=ValueError("PRIVATE https://private.invalid")):
            with self.assertRaises(LLMRequestError) as caught:
                asyncio.run(request_inference_async("https://offline.invalid", {}, {}, protocol="openai_chat",
                    policy={"mode":"json"}, diagnostics=diag))
        self.assertEqual(caught.exception.category, "configuration_error")
        self.assertEqual(caught.exception.attempts, 0)
        self.assertEqual(caught.exception.transport_diagnostics["attempts"], 0)
        self.assertIn(" 0 ", str(caught.exception))
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertNotIn("_started_at", diag)
        self.assertEqual(caught.exception.transport_diagnostics, diag)

    def test_success_diagnostics_have_no_internal_keys(self):
        diag = {}
        async def invoke():
            return await request_inference_async("https://offline.invalid", {}, {}, protocol="openai_chat",
                policy={"mode":"json"}, diagnostics=diag,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok":True})))
        asyncio.run(invoke())
        self.assertFalse(any(key.startswith("_") for key in diag))
        self.assertEqual(transport_module.DEFAULTS["idle_timeout_seconds"], 90)
        for category in ("event_too_large", "response_too_large"):
            self.assertEqual(transport_module._stream_category(category), "response_too_large")
        self.assertEqual(transport_module._stream_category("unsupported_finish"), "stream_incomplete")


class FakeInferenceProcess:
    """Entire process boundary is mocked: the isolated test runner forbids spawn."""
    def __init__(self, output, *, action=None, exit_code=0):
        self.output = output
        self.action = action
        self.exit_code = exit_code
        self.returncode = None
        self.killed = False
        self.calls = []
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = None

    def communicate(self, input=None, timeout=None):
        self.calls.append((input, timeout))
        if input is not None and self.action is not None:
            self.action(input, timeout)
        if not self.killed: self.returncode = self.exit_code
        return self.output, None

    def poll(self): return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class IsolatedFacadeTests(unittest.TestCase):
    endpoint = "https://private.invalid/inference?private=PRIVATE"
    headers = {"Authorization":"Bearer req_PRIVATE123", "X-API-Key":"api_PRIVATE"}
    payload = {"model":"fixed", "reasoning_effort":"high", "max_tokens":37,
               "response_format":{"type":"json_object"}, "messages":[{"content":"PRIVATE prompt"}]}

    def envelope(self, mode="json", **changes):
        value = {"version":1, "ok":True, "response":{"ok":True}, "status_code":200,
                 "attempts":1, "diagnostics":{"transport_mode":mode, "completion_seen":mode == "stream"}}
        value.update(changes)
        return json.dumps(value).encode()

    def invoke(self, mode="json", **kwargs):
        return request_inference(self.endpoint, self.headers, self.payload, protocol="openai_chat",
            policy={"mode":mode}, **kwargs)

    def test_success_both_modes_one_process_credentials_only_on_stdin(self):
        for mode in ("json", "stream"):
            process = FakeInferenceProcess(self.envelope(mode))
            diag = {}
            with patch.object(transport_module.subprocess, "Popen", return_value=process) as spawn:
                result = self.invoke(mode, diagnostics=diag)
            self.assertEqual(result[:2], ({"ok":True}, 200))
            self.assertEqual(result[3], 1)
            self.assertEqual(spawn.call_count, 1)
            self.assertEqual(len(process.calls), 1)
            sent = json.loads(process.calls[0][0])
            self.assertEqual(sent["headers"], self.headers)
            self.assertEqual(sent["payload"], self.payload)
            self.assertEqual(sent["policy"]["mode"], mode)
            self.assertNotIn("PRIVATE", repr(spawn.call_args))
            self.assertEqual(spawn.call_args.kwargs["stderr"], subprocess.DEVNULL)
            self.assertIn("--worker", spawn.call_args.args[0])
            self.assertNotIn("-m", spawn.call_args.args[0])
            self.assertTrue(process.stdin.closed and process.stdout.closed)
            self.assertNotIn("stream", self.payload)
            self.assertEqual(diag["total_ms"], result[2])

    def test_startup_time_is_subtracted_from_communicate_budget(self):
        clock = [100.0]
        process = FakeInferenceProcess(self.envelope())
        def spawn(*args, **kwargs):
            clock[0] += .02
            return process
        with patch.object(transport_module.time, "monotonic", side_effect=lambda:clock[0]), \
             patch.object(transport_module.subprocess, "Popen", side_effect=spawn):
            result = self.invoke(timeout=.03)
        self.assertAlmostEqual(process.calls[0][1], .01)
        self.assertEqual(result[2], 20)

    def test_expired_startup_kills_without_sending_input_and_reports_full_wall(self):
        clock = [100.0]
        process = FakeInferenceProcess(b"PRIVATE")
        def spawn(*args, **kwargs):
            clock[0] += .04
            return process
        diag = {}
        with patch.object(transport_module.time, "monotonic", side_effect=lambda:clock[0]), \
             patch.object(transport_module.subprocess, "Popen", side_effect=spawn):
            with self.assertRaises(LLMRequestError) as caught: self.invoke(timeout=.03, diagnostics=diag)
        self.assertTrue(process.killed)
        self.assertTrue(all(call[0] is None for call in process.calls))
        self.assertEqual(caught.exception.category, "total_timeout")
        self.assertEqual(diag["total_ms"], 40)
        self.assertEqual(caught.exception.transport_diagnostics, diag)

    def test_timeout_kills_reaps_once_and_does_not_publish_partial_output(self):
        clock = [100.0]
        def timeout(input, timeout):
            clock[0] += .04
            raise subprocess.TimeoutExpired(["worker"], timeout, output=b"PRIVATE")
        process = FakeInferenceProcess(b"PRIVATE partial", action=timeout)
        diag = {}
        with patch.object(transport_module.time, "monotonic", side_effect=lambda:clock[0]), \
             patch.object(transport_module.subprocess, "Popen", return_value=process) as spawn:
            with self.assertRaises(LLMRequestError) as caught: self.invoke(timeout=.03, diagnostics=diag)
        self.assertEqual(spawn.call_count, 1)
        self.assertTrue(process.killed)
        self.assertEqual(len(process.calls), 2)
        self.assertIsNone(process.calls[1][0])
        self.assertEqual(caught.exception.category, "total_timeout")
        self.assertEqual(diag["total_ms"], 40)
        self.assertNotIn("PRIVATE", str(caught.exception) + json.dumps(diag))
        self.assertTrue(process.stdin.closed and process.stdout.closed)

    def test_worker_error_metadata_is_revalidated_not_echoed(self):
        output = self.envelope(ok=False, error={"category":"http_error", "status_code":524,
            "provider_code":"system_cpu_overloaded", "request_id":"req_PRIVATE123", "message":"PRIVATE"},
            diagnostics={"request_id":"req_PRIVATE123", "cf_ray":"0123456789abcdef-IAD",
                         "_started_at":100, "headers":"PRIVATE", "url":self.endpoint, "failure_phase":"http"})
        process = FakeInferenceProcess(output)
        with patch.object(transport_module.subprocess, "Popen", return_value=process):
            with self.assertRaises(LLMRequestError) as caught: self.invoke()
        error = caught.exception
        self.assertEqual(error.status_code, 524)
        self.assertEqual(error.provider_code, "system_cpu_overloaded")
        self.assertEqual(error.request_id, "")
        self.assertNotIn("PRIVATE", str(error) + json.dumps(error.transport_diagnostics))
        self.assertNotIn("_started_at", error.transport_diagnostics)
        self.assertEqual(error.transport_diagnostics["cf_ray"], "0123456789abcdef-IAD")

    def test_pipe_failure_kills_and_reaps_without_leaking_error_text(self):
        def broken_pipe(input, timeout): raise OSError("PRIVATE pipe detail")
        process = FakeInferenceProcess(b"PRIVATE", action=broken_pipe)
        with patch.object(transport_module.subprocess, "Popen", return_value=process):
            with self.assertRaises(LLMRequestError) as caught: self.invoke()
        self.assertTrue(process.killed)
        self.assertEqual(len(process.calls), 2)
        self.assertEqual(caught.exception.category, "network_error")
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue(process.stdin.closed and process.stdout.closed)

    def test_success_arriving_after_deadline_is_not_accepted(self):
        clock = [100.0]
        def late(input, timeout): clock[0] += .04
        process = FakeInferenceProcess(self.envelope(), action=late)
        with patch.object(transport_module.time, "monotonic", side_effect=lambda:clock[0]), \
             patch.object(transport_module.subprocess, "Popen", return_value=process):
            with self.assertRaises(LLMRequestError) as caught: self.invoke(timeout=.03)
        self.assertEqual(caught.exception.category, "total_timeout")
        self.assertEqual(caught.exception.transport_diagnostics["total_ms"], 40)
        self.assertEqual(len(process.calls), 1)

    def test_worker_known_preflight_error_keeps_zero_attempts_in_message_and_diagnostics(self):
        output = self.envelope(ok=False, error={"category":"configuration_error", "status_code":0, "attempts":0},
            diagnostics={"attempts":0, "failure_phase":"configuration"})
        process = FakeInferenceProcess(output)
        with patch.object(transport_module.subprocess, "Popen", return_value=process):
            with self.assertRaises(LLMRequestError) as caught: self.invoke()
        self.assertEqual(caught.exception.attempts, 0)
        self.assertEqual(caught.exception.transport_diagnostics["attempts"], 0)
        self.assertIn(" 0 ", str(caught.exception))

    def test_startup_failure_is_sanitized(self):
        with patch.object(transport_module.subprocess, "Popen", side_effect=OSError("PRIVATE proxy URL")) as spawn:
            with self.assertRaises(LLMRequestError) as caught: self.invoke()
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(caught.exception.category, "configuration_error")
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_crash_corrupt_or_incomplete_ipc_never_succeeds_or_retries(self):
        for output, exit_code, mode in [
            (b"PRIVATE", 0, "json"), (b"[]", 0, "json"), (b"PRIVATE", 3, "json"),
            (self.envelope(attempts=2), 0, "json"), (self.envelope(status_code=True), 0, "json"),
            (self.envelope(), 0, "stream"), (self.envelope(ok="true"), 0, "json"),
        ]:
            process = FakeInferenceProcess(output, exit_code=exit_code)
            with patch.object(transport_module.subprocess, "Popen", return_value=process) as spawn:
                with self.assertRaises(LLMRequestError) as caught: self.invoke(mode)
            self.assertEqual(spawn.call_count, 1)
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertTrue(process.stdout.closed)

    def test_configuration_and_protocol_fail_before_spawn(self):
        with patch.object(transport_module.subprocess, "Popen") as spawn:
            with self.assertRaises(LLMRequestError) as caught: self.invoke(timeout=0)
            self.assertEqual(caught.exception.category, "configuration_error")
            self.assertEqual(caught.exception.attempts, 0)
            with self.assertRaises(LLMRequestError) as caught:
                request_inference(self.endpoint, {}, {}, protocol="unknown", policy={"mode":"json"})
            self.assertEqual(caught.exception.category, "unsupported_protocol")
            self.assertEqual(caught.exception.attempts, 0)
            self.assertEqual(caught.exception.transport_diagnostics["attempts"], 0)
            self.assertIn(" 0 ", str(caught.exception))
        spawn.assert_not_called()




class WorkerEmissionTests(unittest.TestCase):
    def test_ipc_emission_is_inside_coroutine_and_after_finally_cleanup(self):
        state = {"closed":False}
        incoming = {"endpoint":"https://offline.invalid", "headers":{}, "payload":{},
                    "protocol":"openai_chat", "policy":{"mode":"json"}, "timeout":1,
                    "deadline":time.monotonic()+2}
        async def request(*args, **kwargs):
            try:
                kwargs["diagnostics"].update({"_started_at":7, "request_id":"arbitrary", "completion_seen":False})
                return {"ok":True}, 200, 1, 1
            finally:
                state["closed"] = True
        def emit(envelope):
            self.assertIsNotNone(asyncio.get_running_loop())
            self.assertTrue(state["closed"])
            self.assertTrue(envelope["ok"])
            self.assertNotIn("_started_at", envelope["diagnostics"])
            state["emitted"] = True
        with patch.object(transport_module.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(json.dumps(incoming).encode()))), \
             patch.object(transport_module, "request_inference_async", side_effect=request), \
             patch.object(transport_module, "_emit_worker_and_exit", side_effect=emit):
            asyncio.run(transport_module._worker_main())
        self.assertTrue(state["emitted"])

    def test_unknown_worker_error_never_serializes_exception_message(self):
        incoming = {"endpoint":"https://offline.invalid", "headers":{}, "payload":{},
                    "protocol":"openai_chat", "policy":{"mode":"json"}, "timeout":1,
                    "deadline":time.monotonic()+2}
        async def request(*args, **kwargs): raise RuntimeError("PRIVATE internal URL")
        with patch.object(transport_module.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(json.dumps(incoming).encode()))), \
             patch.object(transport_module, "request_inference_async", side_effect=request), \
             patch.object(transport_module, "_emit_worker_and_exit") as emit:
            asyncio.run(transport_module._worker_main())
        envelope = emit.call_args.args[0]
        self.assertFalse(envelope["ok"])
        self.assertNotIn("PRIVATE", json.dumps(envelope))


if __name__ == "__main__": unittest.main()



class WorkerEnvironmentTests(unittest.TestCase):
    def test_only_runtime_proxy_and_ca_environment_is_inherited(self):
        with patch.dict(transport_module.os.environ, {'LLM_API_KEY':'MODEL-SECRET','OKX_DEMO_API_KEY':'EXCHANGE-SECRET','HTTPS_PROXY':'http://proxy.invalid:3128','SSL_CERT_FILE':'/tmp/fixture.pem'}):
            env=transport_module._worker_environment()
        self.assertNotIn('LLM_API_KEY',env);self.assertNotIn('OKX_DEMO_API_KEY',env)
        self.assertEqual(env['HTTPS_PROXY'],'http://proxy.invalid:3128');self.assertEqual(env['SSL_CERT_FILE'],'/tmp/fixture.pem')
