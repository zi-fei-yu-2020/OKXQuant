from __future__ import annotations

import json
from typing import Any


class StreamProtocolError(Exception):
    """A safe, content-free stream failure identified by category."""

    def __init__(self, category: str):
        self.category = str(category)
        super().__init__(self.category)


def _fail(category: str = "malformed_stream") -> None:
    raise StreamProtocolError(category)


class SSEDecoder:
    """Incremental, bounded decoder for Server-Sent Events byte streams."""

    def __init__(self, max_event_bytes: int = 1_048_576, max_response_bytes: int = 8_388_608):
        if max_event_bytes <= 0 or max_response_bytes <= 0:
            raise ValueError("limits must be positive")
        self.max_event_bytes = max_event_bytes
        self.max_response_bytes = max_response_bytes
        self._response_bytes = 0
        self._line = bytearray()
        self._line_bytes = 0
        self._pending_cr = False
        self._data: list[str] = []
        self._event_name = "message"
        self._event_bytes = 0
        self._saw_comment = False
        self._heartbeat_count = 0
        self._finished = False
        self._first_line = True

    @property
    def heartbeat_count(self) -> int:
        return self._heartbeat_count

    def _decode_line(self, raw: bytes) -> str:
        try:
            line = raw.decode("utf-8", "strict")
            if self._first_line:
                self._first_line = False
                if line.startswith("\ufeff"):
                    line = line[1:]
            return line
        except UnicodeDecodeError:
            _fail("invalid_utf8")

    def _line_ready(self, events: list[tuple[str, str]]) -> None:
        raw = bytes(self._line)
        self._line.clear()
        self._line_bytes = 0
        line = self._decode_line(raw)
        if line == "":
            self._dispatch(events)
            return
        self._event_bytes += len(raw) + 1
        if self._event_bytes > self.max_event_bytes:
            _fail("event_too_large")
        if line.startswith(":"):
            self._heartbeat_count += 1
            self._saw_comment = True
            return
        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event_name = value or "message"

    def _dispatch(self, events: list[tuple[str, str]]) -> None:
        if self._data:
            data = "\n".join(self._data)
            events.append((self._event_name, data))
            if self._event_name.lower() in {"ping", "heartbeat"}:
                self._heartbeat_count += 1
        self._data = []
        self._event_name = "message"
        self._event_bytes = 0
        self._saw_comment = False

    def feed(self, chunk: bytes) -> list[tuple[str, str]]:
        if self._finished:
            _fail("data_after_eof")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            _fail("invalid_chunk")
        chunk = bytes(chunk)
        self._response_bytes += len(chunk)
        if self._response_bytes > self.max_response_bytes:
            _fail("response_too_large")
        events: list[tuple[str, str]] = []
        for byte in chunk:
            if self._pending_cr:
                self._pending_cr = False
                self._line_ready(events)
                if byte == 0x0A:
                    continue  # The pending CR and this LF form one terminator.
            if byte == 0x0D:
                self._pending_cr = True
                if self._line_bytes + self._event_bytes + 1 > self.max_event_bytes:
                    _fail("event_too_large")
            elif byte == 0x0A:
                self._line_ready(events)
            else:
                self._line.append(byte)
                self._line_bytes += 1
                if self._line_bytes + self._event_bytes > self.max_event_bytes:
                    _fail("event_too_large")
        return events

    def validate_terminal_tail(self) -> None:
        """Already-received partial data after a terminal is not discardable."""
        if self._data or self._event_name != 'message':
            _fail('data_after_terminal')
        if self._line:
            text=self._decode_line(bytes(self._line))
            if text.strip() and not text.startswith(':'):
                _fail('data_after_terminal')

    def finish(self) -> list[tuple[str, str]]:
        """Validate trailing UTF-8; discard events lacking a blank separator.

        A final CR is a real line terminator (and may end a blank separator),
        but EOF itself never supplies a missing line or event terminator.
        """
        if self._finished:
            return []
        self._finished = True
        events: list[tuple[str, str]] = []
        # A pending CR ends exactly one line. Any other partial line is parsed,
        # but an event is deliberately not dispatched without a blank separator.
        if self._pending_cr:
            self._pending_cr = False
            self._line_ready(events)
        elif self._line:
            self._line_ready(events)
        return events


class StreamAssembler:
    def __init__(self, protocol: str):
        if protocol not in {"openai_chat", "openai_responses", "claude_messages"}:
            raise ValueError("unsupported stream protocol")
        self.protocol = protocol
        self._complete = False
        self._failed = False
        self._result: dict[str, Any] | None = None
        self._chat_text: list[str] = []
        self._chat_reasoning: list[str] = []
        self._chat_meta: dict[str, Any] = {}
        self._chat_usage: dict[str, Any] | None = None
        self._chat_finish: str | None = None
        self._chat_done = False
        self._resp_text: list[str] = []
        self._resp_reasoning: list[str] = []
        self._resp_meta: dict[str, Any] = {}
        self._claude_blocks: dict[int, dict[str, Any]] = {}
        self._claude_closed: set[int] = set()
        self._claude_usage: dict[str, Any] = {}
        self._claude_meta: dict[str, Any] = {}
        self._claude_stop: str | None = None
        self._claude_started = False
        self._claude_message_delta = False
        self._claude_message_stop = False

    @property
    def complete(self) -> bool:
        return self._complete and not self._failed

    @property
    def content_started(self) -> bool:
        """Model text/thinking, not role, usage, signatures or heartbeat scaffolding."""
        if any(part.strip() for part in self._chat_text + self._chat_reasoning + self._resp_text + self._resp_reasoning):
            return True
        if any(str(block.get('text') or block.get('thinking') or '').strip() for block in self._claude_blocks.values()):
            return True
        if self._complete and self._result:
            return bool(str(self._result.get('output_text') or '').strip())
        return False

    def _json(self, data: str) -> dict[str, Any]:
        try:
            obj = json.loads(data)
        except (TypeError, ValueError):
            _fail("malformed_json")
        if not isinstance(obj, dict):
            _fail("malformed_json")
        return obj

    def _mark_complete(self, result: dict[str, Any]) -> None:
        if self._complete:
            _fail("contradictory_terminal")
        self._complete = True
        self._result = result

    def feed(self, event_name: str, data: str) -> None:
        if self._failed:
            _fail("stream_failed")
        try:
            self._feed(event_name or "message", data)
        except StreamProtocolError:
            self._failed = True
            self._complete = False
            self._result = None
            raise

    def _feed(self, event_name: str, data: str) -> None:
        if self.protocol == "openai_chat":
            self._feed_chat(event_name, data)
        elif self.protocol == "openai_responses":
            self._feed_responses(event_name, data)
        else:
            self._feed_claude(event_name, data)

    def _after_terminal(self, event_name: str, data: str) -> None:
        if self._complete:
            _fail("data_after_terminal")

    def _feed_chat(self, event_name: str, data: str) -> None:
        if event_name in {"error", "response.error"}:
            _fail("provider_error")
        if data.strip() == "[DONE]":
            if self._chat_done or self._chat_finish != "stop":
                _fail("invalid_done")
            self._chat_done = True
            message: dict[str, Any] = {"role": "assistant", "content": "".join(self._chat_text)}
            if self._chat_reasoning:
                message["reasoning_content"] = "".join(self._chat_reasoning)
            result = dict(self._chat_meta)
            result["choices"] = [{"index": 0, "message": message, "finish_reason": "stop"}]
            if self._chat_usage is not None:
                result["usage"] = self._chat_usage
            self._mark_complete(result)
            return
        if self._chat_done:
            _fail("data_after_terminal")
        obj = self._json(data)
        # Validate errors and identity before the late usage-only fast path.
        if obj.get("error") is not None or obj.get("type") in {"error", "response.error"}:
            _fail("provider_error")
        if obj.get("refusal") not in (None, ""):
            _fail("refusal")
        calls = obj.get("tool_calls")
        if calls is not None and (not isinstance(calls, list) or calls):
            _fail("tool_call")
        if obj.get("function_call") is not None:
            _fail("tool_call")
        for key in ("id", "model"):
            if key in obj:
                value = obj[key]
                if not isinstance(value, str) or not value.strip():
                    _fail("malformed_identity")
                if key in self._chat_meta and self._chat_meta[key] != value:
                    _fail("identity_mismatch")
                self._chat_meta[key] = value
        for key in ("object", "created", "system_fingerprint"):
            if key in obj:
                self._chat_meta.setdefault(key, obj[key])
        if obj.get("usage") is not None:
            if not isinstance(obj["usage"], dict):
                _fail("malformed_usage")
            self._chat_usage = obj["usage"]
        if self._chat_finish is not None:
            # Standard usage follows finish_reason, but precedes [DONE].
            if obj.get("choices") == [] and isinstance(obj.get("usage"), dict):
                return
            _fail("data_after_finish")
        choices = obj.get("choices")
        if choices is None:
            return  # usage-only final chunk
        if choices == [] and obj.get("usage") is not None:
            return
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            _fail("unsupported_choices")
        choice = choices[0]
        if choice.get("index", 0) != 0:
            _fail("unsupported_choices")
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            _fail("malformed_delta")
        if delta.get("refusal"):
            _fail("refusal")
        calls = delta.get("tool_calls")
        if calls is not None and (not isinstance(calls, list) or calls):
            _fail("tool_call")
        if delta.get("function_call") is not None:
            _fail("tool_call")
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                _fail("unsupported_content")
            self._chat_text.append(content)
        reasoning = delta.get("reasoning_content", delta.get("reasoning"))
        if reasoning is not None:
            if not isinstance(reasoning, str):
                _fail("unsupported_reasoning")
            self._chat_reasoning.append(reasoning)
        finish = choice.get("finish_reason")
        if finish is not None:
            if finish != "stop" or self._chat_finish is not None:
                _fail("unsupported_finish")
            self._chat_finish = finish

    @staticmethod
    def _check_response_event_type(typ: str) -> None:
        if not isinstance(typ, str):
            _fail("malformed_event")
        if typ in {"error", "response.error", "response.failed", "response.incomplete"}:
            _fail("incomplete_response" if typ == "response.incomplete" else "provider_error")
        if "refusal" in typ.split("."):
            _fail("refusal")
        if any("tool" in part or "_call" in part or part.startswith("mcp_")
               for part in typ.split(".")):
            _fail("tool_call")

    def _feed_responses(self, event_name: str, data: str) -> None:
        if self._complete:
            _fail("data_after_terminal")
        self._check_response_event_type(event_name)
        obj = self._json(data)
        if obj.get("error"):
            _fail("provider_error")
        typ = obj.get("type", event_name)
        self._check_response_event_type(typ)
        if typ in {"response.output_text.delta", "output_text.delta"}:
            delta = obj.get("delta")
            if not isinstance(delta, str):
                _fail("malformed_delta")
            self._resp_text.append(delta)
            return
        if typ in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            delta = obj.get("delta")
            if not isinstance(delta, str):
                _fail("malformed_delta")
            self._resp_reasoning.append(delta)
            return
        if typ in {"response.output_item.added", "response.output_item.done",
                   "response.content_part.added", "response.content_part.done"}:
            item = obj.get("item", obj.get("part", {}))
            allowed = {"message", "reasoning"} if "output_item" in typ else {"output_text", "reasoning_text", "summary_text"}
            if not isinstance(item, dict) or item.get("type") not in allowed:
                _fail("unsupported_output")
            if item.get("type") == "message":
                blocks = item.get("content", [])
                if not isinstance(blocks, list) or any(
                    not isinstance(block, dict) or block.get("type") != "output_text"
                    for block in blocks
                ):
                    _fail("unsupported_output")
            return
        if typ == "response.completed":
            response = obj.get("response")
            if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error") or response.get("incomplete_details"):
                _fail("incomplete_response")
            output = response.get("output")
            if not isinstance(output, list):
                _fail("malformed_response")
            final_text: list[str] = []
            final_reasoning: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    _fail("malformed_response")
                kind = item.get("type")
                if kind not in {"message", "reasoning"}:
                    _fail("unsupported_output")
                if item.get("status") not in (None, "completed"):
                    _fail("incomplete_response")
                if kind == "message":
                    blocks = item.get("content")
                    if not isinstance(blocks, list):
                        _fail("malformed_response")
                    for block in blocks:
                        if not isinstance(block, dict) or block.get("type") != "output_text" or not isinstance(block.get("text"), str):
                            _fail("unsupported_output")
                        final_text.append(block["text"])
                else:
                    for block in item.get("summary", []):
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            final_reasoning.append(block["text"])
            # The structured final output is authoritative, not an optional
            # convenience string preferred by some callers. Never accept a
            # missing message representation or two conflicting final answers.
            if not final_text:
                _fail("missing_output_text")
            joined = "".join(final_text)
            deltas = "".join(self._resp_text)
            if self._resp_text and joined != deltas:
                _fail("delta_mismatch")
            canonical = {k: response[k] for k in ("id", "object", "created_at", "model", "status", "output", "usage", "incomplete_details") if k in response}
            canonical.setdefault("status", "completed")
            if "output_text" in response:
                if not isinstance(response["output_text"], str) or response["output_text"] != joined:
                    _fail("delta_mismatch")
                canonical["output_text"] = response["output_text"]
            elif final_text:
                canonical["output_text"] = joined
            if self._resp_reasoning and final_reasoning and "".join(self._resp_reasoning) != "".join(final_reasoning):
                _fail("delta_mismatch")
            self._mark_complete(canonical)

    def _feed_claude(self, event_name: str, data: str) -> None:
        if self._complete:
            _fail("data_after_terminal")
        if event_name == "ping":
            return
        if event_name == "error":
            _fail("provider_error")
        obj = self._json(data)
        typ = obj.get("type", event_name)
        if typ == "ping":
            return
        if typ == "error":
            _fail("provider_error")
        if typ != "message_start" and not self._claude_started:
            _fail("missing_message_start")
        if self._claude_message_delta and typ != "message_stop":
            _fail("data_after_finish")
        if typ == "message_start":
            if self._claude_started:
                _fail("duplicate_message_start")
            message = obj.get("message")
            if not isinstance(message, dict):
                _fail("malformed_message")
            self._claude_started = True
            for key in ("id", "type", "role", "model"):
                if key in message:
                    self._claude_meta[key] = message[key]
            if isinstance(message.get("usage"), dict):
                self._claude_usage.update(message["usage"])
        elif typ == "content_block_start":
            index, block = obj.get("index"), obj.get("content_block")
            if (type(index) is not int or index != len(self._claude_blocks)
                    or len(self._claude_closed) != len(self._claude_blocks)
                    or not isinstance(block, dict)):
                _fail("malformed_block")
            kind = block.get("type")
            if kind not in {"text", "thinking"}:
                _fail("unsupported_output")
            field = "text" if kind == "text" else "thinking"
            initial = block.get(field, "")
            if not isinstance(initial, str):
                _fail("malformed_block")
            self._claude_blocks[index] = {"type": kind, field: initial}
        elif typ == "content_block_delta":
            index, delta = obj.get("index"), obj.get("delta")
            if (type(index) is not int or index not in self._claude_blocks
                    or index in self._claude_closed or not isinstance(delta, dict)):
                _fail("malformed_block")
            block = self._claude_blocks[index]
            if delta.get("type") == "text_delta" and block["type"] == "text":
                field = "text"
            elif delta.get("type") == "thinking_delta" and block["type"] == "thinking":
                field = "thinking"
            elif delta.get("type") == "signature_delta" and block["type"] == "thinking":
                signature = delta.get("signature")
                if not isinstance(signature, str):
                    _fail("malformed_delta")
                # Opaque verification metadata; never decision/reasoning text.
                block["signature"] = block.get("signature", "") + signature
                return
            else:
                _fail("unsupported_output")
            value = delta.get(field)
            if not isinstance(value, str):
                _fail("malformed_delta")
            block[field] += value
        elif typ == "content_block_stop":
            index = obj.get("index")
            if (type(index) is not int or index not in self._claude_blocks
                    or index in self._claude_closed):
                _fail("malformed_block")
            self._claude_closed.add(index)
        elif typ == "message_delta":
            if len(self._claude_closed) != len(self._claude_blocks):
                _fail("unclosed_block")
            delta = obj.get("delta")
            if not isinstance(delta, dict):
                _fail("malformed_delta")
            stop = delta.get("stop_reason")
            if stop not in {"end_turn", "stop_sequence"} or self._claude_message_delta:
                _fail("unsupported_finish")
            self._claude_stop = stop
            self._claude_message_delta = True
            if isinstance(obj.get("usage"), dict):
                self._claude_usage.update(obj["usage"])
        elif typ == "message_stop":
            if not self._claude_message_delta or self._claude_message_stop or len(self._claude_closed) != len(self._claude_blocks):
                _fail("invalid_terminal")
            self._claude_message_stop = True
            result = dict(self._claude_meta)
            result["stop_reason"] = self._claude_stop
            result["content"] = [self._claude_blocks[i] for i in sorted(self._claude_blocks)]
            if self._claude_usage:
                result["usage"] = self._claude_usage
            self._mark_complete(result)

    def result(self) -> dict[str, Any]:
        if self._failed:
            _fail("stream_failed")
        if not self._complete or self._result is None:
            _fail("truncated_stream")
        return self._result
