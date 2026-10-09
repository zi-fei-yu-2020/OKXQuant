#!/usr/bin/env python3
"""Explicit loopback-only subprocess checks, separate from the offline unit runner.

Run under Linux/WSL: python scripts/check_llm_transport_process.py
No application config, credentials, live data, external endpoints, or models are
loaded. Unit tests mock Popen; this opt-in checker exercises real worker startup,
kill/reap, loopback HTTP, and exit before a stuck default-executor thread joins.
"""
from __future__ import annotations

import asyncio
import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_transport():
    # Bypass the application's config-importing package initializer, just as the
    # production worker does. Reading/creating runtime data is unnecessary here.
    package = types.ModuleType("okxquant_backend")
    package.__path__ = [str(ROOT / "okxquant_backend")]
    sys.modules["okxquant_backend"] = package
    module = importlib.import_module("okxquant_backend.llm_inference_transport")
    assert "okxquant_backend.config" not in sys.modules
    assert "okxquant_backend.app" not in sys.modules
    return module


def clean_environment():
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "LD_LIBRARY_PATH", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", NO_PROXY="127.0.0.1,localhost,::1")
    return env


class Fixtures(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    received = []
    disconnected = threading.Event()
    long_seconds = 0

    def log_message(self, *args): pass

    def do_POST(self):
        assert self.client_address[0] == "127.0.0.1"
        length = int(self.headers.get("Content-Length", 0))
        assert 0 < length < 65536
        body = json.loads(self.rfile.read(length))
        self.received.append((self.path, body))
        if self.path == "/stall":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.flush()
            self.connection.settimeout(5)
            try:
                if self.connection.recv(1) == b"": self.disconnected.set()
            except (ConnectionResetError, BrokenPipeError):
                self.disconnected.set()
            self.close_connection = True
            return
        if self.path == '/long-stream':
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            until=time.monotonic()+self.long_seconds
            while time.monotonic()<until:
                self.wfile.write(b': heartbeat\n\n');self.wfile.flush();time.sleep(min(10,max(0,until-time.monotonic())))
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"offline-ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            self.wfile.flush();self.close_connection=True;return
        assert self.path in {"/json", "/stream", "/responses", "/claude"}
        if self.path == '/responses':
            response={'status':'completed','output':[{'type':'message','status':'completed','content':[{'type':'output_text','text':'offline-ok'}]}]}
            data=('event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':response})+'\n\n').encode()
            content_type='text/event-stream'
        elif self.path == '/claude':
            frames=[('message_start',{'type':'message_start','message':{'id':'fixture','role':'assistant','model':'fixture','content':[]}}),
                ('content_block_start',{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}),
                ('content_block_delta',{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'offline-ok'}}),
                ('content_block_stop',{'type':'content_block_stop','index':0}),
                ('message_delta',{'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':2}}),
                ('message_stop',{'type':'message_stop'})]
            data=''.join('event: '+event+'\ndata: '+json.dumps(value)+'\n\n' for event,value in frames).encode();content_type='text/event-stream'
        elif self.path == "/json":
            data = b'{"choices":[{"message":{"content":"offline-ok"}}]}'
            content_type = "application/json"
        else:
            data = (b'data: {"choices":[{"delta":{"content":"offline-ok"},"finish_reason":"stop"}]}\n\n'
                    b'data: [DONE]\n\n')
            content_type = "text/event-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        self.close_connection = True


def check_executor_exit(module, env):
    """Only mocked HTTP: the 5s executor thread must not delay worker exit."""
    program = r'''
import asyncio, importlib, json, sys, time, types
from pathlib import Path
root = Path(sys.argv[1])
package = types.ModuleType("okxquant_backend")
package.__path__ = [str(root / "okxquant_backend")]
sys.modules["okxquant_backend"] = package
m = importlib.import_module("okxquant_backend.llm_inference_transport")
assert "okxquant_backend.config" not in sys.modules
assert "okxquant_backend.app" not in sys.modules
original = m.request_inference_async
async def handler(request):
    await asyncio.to_thread(time.sleep, 5)
    return m.httpx.Response(200, json={"ok": True})
async def mock_request(*args, **kwargs):
    return await original(*args, transport=m.httpx.MockTransport(handler), **kwargs)
m.request_inference_async = mock_request
asyncio.run(m._worker_main())
'''
    request = {"endpoint":"http://127.0.0.1:1/not-used", "headers":{},
        "payload":{"model":"offline-fixture"}, "protocol":"openai_chat",
        "policy":{"mode":"json"}, "timeout":.03, "deadline":time.monotonic()+10}
    started = time.monotonic()
    child = subprocess.Popen([sys.executable, "-B", "-u", "-c", program, str(ROOT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        output, _ = child.communicate(json.dumps(request).encode(), timeout=3)
        elapsed = time.monotonic()-started
        envelope = json.loads(output)
        assert child.returncode == 0
        assert not envelope["ok"] and envelope["error"]["category"] == "total_timeout"
        assert elapsed < 2, "Worker waited for a stuck default-executor thread"
        return round(elapsed*1000)
    finally:
        if child.poll() is None: child.kill()
        child.communicate()
        for pipe in (child.stdin, child.stdout):
            if pipe is not None: pipe.close()


def check_blocked_stdin(module, env):
    """Large input must not bypass timeout if the child never reads its pipe."""
    real_popen=subprocess.Popen
    children=[]
    def sleeper(*args,**kwargs):
        child=real_popen([sys.executable,'-c','import time; time.sleep(10)'],**kwargs)
        children.append(child);return child
    started=time.monotonic();diagnostics={}
    with patch.object(module.subprocess,'Popen',side_effect=sleeper):
        try:
            module.request_inference('http://127.0.0.1:1/never-sent',{},
                {'model':'fixture','messages':[{'role':'user','content':'x'*262144}]},
                protocol='openai_chat',policy={'mode':'json'},timeout=.1,diagnostics=diagnostics)
            raise AssertionError('Expected stdin deadline')
        except module.LLMRequestError as exc:assert exc.category=='total_timeout'
    elapsed=time.monotonic()-started
    assert elapsed<1, 'Blocked stdin bypassed the independent deadline'
    assert len(children)==1 and children[0].poll() is not None
    return round(elapsed*1000)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--long-seconds',type=int,default=0)
    args=parser.parse_args();assert 0<=args.long_seconds<=180
    Fixtures.long_seconds=args.long_seconds
    module = load_transport()
    env = clean_environment()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixtures)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    children = []
    real_popen = subprocess.Popen

    class TrackedProcess(real_popen):
        def __init__(self, *args, **kwargs):
            self.kill_called = False
            super().__init__(*args, **kwargs)
            children.append(self)
        def kill(self):
            self.kill_called = True
            return super().kill()

    payload = {"model":"offline-fixture", "reasoning_effort":"high", "max_tokens":20,
               "messages":[{"role":"user", "content":"offline fixture only"}]}
    elapsed_values = {}
    try:
        with patch.dict(os.environ, env, clear=True), patch.object(module.subprocess, "Popen", TrackedProcess):
            for mode in ("json", "stream"):
                diag = {}
                result = module.request_inference(f"http://127.0.0.1:{server.server_port}/{mode}", {}, payload,
                    protocol="openai_chat", policy={"mode":mode}, timeout=5, diagnostics=diag)
                assert result[1] == 200 and result[3] == 1
                assert result[0]["choices"][0]["message"]["content"] == "offline-ok"
                if mode == "stream": assert diag["completion_seen"] is True
                elapsed_values[mode] = result[2]
                assert children[-1].poll() == 0
            for path,protocol in [('/responses','openai_responses'),('/claude','claude_messages')]:
                diag={}
                result=module.request_inference(f'http://127.0.0.1:{server.server_port}'+path,{},payload,protocol=protocol,policy={'mode':'stream'},timeout=5,diagnostics=diag)
                assert result[1]==200 and result[3]==1 and diag['completion_seen']
                assert ('offline-ok' in str(result[0]))
                elapsed_values[protocol]=result[2]
            # Budget shorter than Python startup: parent must kill/reap, never
            # wait for worker import/DNS/default-executor shutdown.
            diag = {}
            started = time.monotonic()
            try:
                module.request_inference(f"http://127.0.0.1:{server.server_port}/stall", {}, payload,
                    protocol="openai_chat", policy={"mode":"json"}, timeout=.005, diagnostics=diag)
                raise AssertionError("Expected startup timeout")
            except module.LLMRequestError as error:
                assert error.category == "total_timeout"
                assert children[-1].kill_called and children[-1].poll() is not None
                elapsed = time.monotonic()-started
                assert elapsed < .5, "Startup deadline did not kill/reap the worker"
                assert abs(diag["total_ms"] - round(elapsed*1000)) < 50
                elapsed_values["startup_timeout"] = diag["total_ms"]
            # A real loopback request stalls after headers. The request must end
            # at the shared deadline, and no outbound socket survives its worker.
            diag = {}
            started = time.monotonic()
            try:
                module.request_inference(f"http://127.0.0.1:{server.server_port}/stall", {}, payload,
                    protocol="openai_chat", policy={"mode":"json"}, timeout=1, diagnostics=diag)
                raise AssertionError("Expected body timeout")
            except module.LLMRequestError as error:
                assert error.category == "total_timeout"
                assert time.monotonic()-started < 1.5
                assert children[-1].poll() is not None
                assert Fixtures.disconnected.wait(1), "Worker left a live loopback connection"
                elapsed_values["body_timeout"] = diag["total_ms"]
        assert len(children) == 6, "Unexpected retry/process count"
        assert all(child.poll() is not None and child.stdin.closed and child.stdout.closed for child in children)
        for path, sent in Fixtures.received:
            assert sent["model"] == payload["model"] and sent["reasoning_effort"] == "high"
            assert sent["stream"] == (path in {"/stream","/responses","/claude"})
            assert "stream_options" not in sent
        assert "stream" not in payload
        elapsed_values["executor_exit"] = check_executor_exit(module, env)
        elapsed_values["blocked_stdin"] = check_blocked_stdin(module, env)
        if args.long_seconds:
            diag={}
            with patch.dict(os.environ,env,clear=True):
                result=module.request_inference(f'http://127.0.0.1:{server.server_port}/long-stream',{},payload,
                    protocol='openai_chat',policy={'mode':'stream'},timeout=args.long_seconds+15,diagnostics=diag)
            assert result[0]['choices'][0]['message']['content']=='offline-ok' and diag['completion_seen']
            assert diag['heartbeat_count']>=args.long_seconds//10
            assert result[2]>=args.long_seconds*1000
            elapsed_values['long_heartbeat_stream']=result[2]
        print(json.dumps({"ok":True, "checks":["loopback_json", "loopback_sse", "loopback_responses", "loopback_claude", "startup_kill_reap",
            "body_deadline_socket_closed", "worker_exits_before_executor_join", "blocked_stdin_kill_reap", "no_app_config_import"],
            "wall_ms":elapsed_values}, sort_keys=True))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
