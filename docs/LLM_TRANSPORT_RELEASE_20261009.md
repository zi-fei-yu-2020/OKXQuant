# Unified inference delivery - 2026-10-09

## Incident and objective

The October 9 07:15 and 07:30 Shanghai-time trading cycles received HTTP 524 after 125.625/125.497 seconds. Correlated gateway requests completed with HTTP 200 after approximately 144.488/139.895 seconds. The configured connection was nonstreaming, so a successful source-side generation was not a successfully delivered trading decision.

This release must be portable: no private-network/domain requirement, no hardcoded supplier identity, no model or reasoning-strength downgrade, and no partial-output execution or ambiguous automatic resend.

## Implemented scope

- Standard OpenAI-compatible Chat, Responses and Claude Messages adapters, complete JSON and bounded SSE.
- Independent connect/first-byte/idle/total budgets. Production synchronous calls run once in a small isolated network worker, with a parent deadline supervisor including startup, stdin delivery and process reclamation.
- Incremental UTF-8/SSE parsing, comments/pings distinct from model content, explicit protocol endings, gzip handling, bounded buffers, no redirects forwarding credentials, no mixing reasoning/signatures with decision text.
- Provider-qualified frozen connection and request shape; capability receipts bound to actual endpoint, key digest, model, protocol and reasoning configuration.
- Auto/stream/JSON settings, explicit billable verification, durable expiring/deduplicated verification jobs, superadmin write/probe gates and safe diagnostics. Unknown/failed capability is not advertised as supported.
- Additive private model-call diagnostics table; no schema destruction. Actual zero usage is preserved, missing/invalid usage is unknown.
- No implicit single-model request after committee failure. Qualified seat selection avoids silently choosing the first supplier with a duplicated model ID.
- Existing public display remains anonymous; administration, raw prompts and write operations remain protected.

## Review closure

GPT-6-luna implemented parser and transport slices. The main integration supplied policy/API/UI, probe lifecycle and routing integration. The requested Gemini frontend subtask encountered a provider disconnect before producing files; the main integration completed that UI instead, without representing an unfinished subtask as delivered.

GPT-6-astra found concrete issues involving cross-provider credential fallback, buffered post-terminal contradictions, conflicting Responses representations, executor/IPC deadline escape, HTTP error-body deadline renewal, expired probe publication, wrong request shape for aliased providers and Claude signature events. These were fixed with regression cases. The Windows large-stdin deadline case was independently reproduced after the fix with one worker, terminated/reaped child, closed pipes and stopped supervisor.

## Verification ledger

Local release gate: **2,017 backend tests passed**; **274 Linux frontend tests passed with zero skips**; typecheck and production build passed. Explicit loopback checks passed for JSON, Chat SSE, Responses SSE and Claude SSE plus startup/body/stdin deadlines and process reclamation on Linux; Windows deadline/process checks also passed. A real 135-second loopback heartbeat stream completed in approximately 135.2 seconds. Edge transport-panel/diagnostic checks passed **13 cases**, and the existing public/admin functional gate passed **18 cases**, with no JavaScript errors or real model requests in these UI checks. Server image and real-provider acceptance are appended after rollout. A local loopback test is not described as a real Cloudflare/vendor test, and a small probe is not described as a guarantee for every future request.

## Production status

Not yet deployed in this record. Existing model/effort, account, risk limits and runtime optional-feature settings must remain unchanged. The previous code image is `86fc3d0`. During authorized maintenance, pause future entries, drain the existing cycle, verify exchange protection, recreate only the application, run one explicit transport-capability probe without trading, then restore the original automatic-entry setting. If the gate fails, roll back instead of silently forcing a different model or mode.


## Server gate correction before rollout

The first server image passed all 2,017 offline tests, but its separate process checker failed a socket-close assertion. A diagnostic rerun showed the one-second test budget expired before `/stall` ever reached the loopback fixture (only the four earlier success paths were recorded). This was not evidence of a surviving socket. The checker now allows startup within a five-second total budget, explicitly requires the stall request to have arrived before asserting EOF, and retains separate ultra-short-startup and blocked-stdin deadline cases. Pre-start cancellation is accepted only as zero attempts, never as a leaked connection or successful inference.

Hard-kill diagnostics also no longer invent a zero byte count or HTTP status 0 when the worker did not return an observation; those values remain unknown. No model, reasoning, order, strategy or runtime feature setting is changed by this correction.
