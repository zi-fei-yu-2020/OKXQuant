"""Best-effort model-call telemetry; never stores prompt or response content."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import os
import time
from typing import Any

from okxquant_gateway.publisher import DB_PATH
from okxquant_gateway.store import GatewayStore

BJ_TZ = timezone(timedelta(hours=8))


class ModelCallTelemetry:
    def __init__(self, caller: str, model: str, reasoning_effort: str, system_prompt: str, user_prompt: str,
                 recovery_context: dict[str, Any] | None = None):
        self.caller = caller
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.input_chars = len(system_prompt) + len(user_prompt)
        fingerprint_source = f"{system_prompt}\0{user_prompt}".encode("utf-8")
        self.prompt_fingerprint = hashlib.sha256(fingerprint_source).hexdigest()[:16]
        self.started_at = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        self.started = time.monotonic()
        self.recovery_context = dict(recovery_context or {})

    def finish(self, status: str, response: dict[str, Any] | None = None, output_chars: int = 0, error: Exception | None = None) -> int | None:
        usage = (response or {}).get("usage", {}) if isinstance(response, dict) else {}
        def count(*names):
            for name in names:
                value=usage.get(name)
                if type(value) is int and 0 <= value <= 10**12:return value
            return None
        record = {
            "caller": self.caller,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "status": status,
            "started_at": self.started_at,
            "duration_ms": max(0, round((time.monotonic() - self.started) * 1000)),
            "input_chars": self.input_chars,
            "output_chars": output_chars,
            "prompt_fingerprint": self.prompt_fingerprint,
            "prompt_transport": "python-direct",
            "input_tokens": count("prompt_tokens", "input_tokens"),
            "output_tokens": count("completion_tokens", "output_tokens"),
            "total_tokens": count("total_tokens"),
            "error_type": type(error).__name__ if error else "",
        }
        from okxquant_backend.llm_transport import safe_transport_diagnostics
        record['transport'] = safe_transport_diagnostics(
            getattr(error, 'transport_diagnostics', {}) if error else usage.get('_transport', {}))
        call_id = None
        try:
            store = GatewayStore(DB_PATH)
            call_id = store.record_model_call(record)
            recovery_id = os.environ.get("OKXQUANT_RECOVERY_ID", "")
            if status == "success" and self.caller == "trading_brain_recovery" and recovery_id.isdigit():
                store.set_state(f"ai_recovery.model_success.{recovery_id}", str(call_id))
        except Exception:
            pass
        if status == "failed" and error is not None and self.caller == "trading_brain":
            try:
                from okxquant_backend.llm_transport import public_failure
                from okxquant_gateway.ai_recovery import schedule
                schedule(
                    failure=public_failure(error),
                    duration_ms=record["duration_ms"],
                    recovery_context=self.recovery_context,
                    original_model_call_id=call_id,
                )
            except Exception:
                # Recovery scheduling is best-effort and must never turn a safe
                # inference failure into a trading-process failure.
                pass
        return call_id
