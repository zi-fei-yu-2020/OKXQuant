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

Initial release gate: **2,017 backend tests passed**; after the `c61ff24` process-gate correction, **2,018 backend tests passed locally and inside the server image**. **274 Linux frontend tests passed with zero skips**; typecheck and production build passed. Explicit loopback checks passed for JSON, Chat SSE, Responses SSE and Claude SSE plus startup/body/stdin deadlines and process reclamation on Linux; Windows deadline/process checks also passed. A real 135-second loopback heartbeat stream completed in approximately 135.2 seconds. Edge transport-panel/diagnostic checks passed **13 cases**, and the existing public/admin functional gate passed **18 cases**, with no JavaScript errors or real model requests in these UI checks. Server image and real-provider acceptance are appended after rollout. A local loopback test is not described as a real Cloudflare/vendor test, and a small probe is not described as a guarantee for every future request.

## Production status

Deployed successfully on **2026-10-09 at 12:59:48 Asia/Shanghai**, with application image **`okxquant:build-c61ff24`** (commit `c61ff24d52bf473e88a9114fe7fc088b37561bfa`). The deployment controller reached `complete`. At the 13:08 check, Docker reported `healthy` with zero restarts. The previous image **`okxquant:build-86fc3d0`** remains available for rollback.


## Server gate correction before rollout

The first server image passed all 2,017 offline tests, but its separate process checker failed a socket-close assertion. A diagnostic rerun showed the one-second test budget expired before `/stall` ever reached the loopback fixture (only the four earlier success paths were recorded). This was not evidence of a surviving socket. The checker now allows startup within a five-second total budget, explicitly requires the stall request to have arrived before asserting EOF, and retains separate ultra-short-startup and blocked-stdin deadline cases. Pre-start cancellation is accepted only as zero attempts, never as a leaked connection or successful inference.

Hard-kill diagnostics also no longer invent a zero byte count or HTTP status 0 when the worker did not return an observation; those values remain unknown. No model, reasoning, order, strategy or runtime feature setting is changed by this correction.


## Server rollout acceptance

- The revised server image passed **2,018 offline backend tests**, followed by the opt-in real-process checker: loopback JSON/Chat SSE/Responses SSE/Claude SSE, startup cancellation, body/socket deadline, worker exit before executor join, blocked-stdin deadline and no application-config import. Image tests used no mounted production data and no external network.
- Maintenance paused future entry cycles, drained the in-flight entry jobs, and verified the existing BTC simulated position's cloud OCO protection with zero pending exchange orders. Only the application container was recreated; data volumes and protection orders were not removed. The protection gate passed again before restoring entries.
- The original automatic-entry flag was `1` and was restored to `1`. Account mode remained `demo`. The active `gemini-3.8-flash-high` model, `high` reasoning effort, selected account binding and persisted `light` profile/optional features were verified unchanged.
- Public root/admin-shell/read-only monitoring routes returned HTTP 200. Anonymous requests for administration configuration/logs, raw prompts and the new transport settings returned HTTP 401. Scheduled entry, position protection, ledger/evidence sync, execution quality and news jobs were present after restoration; subsequent position-protection and ledger jobs completed successfully.

### One explicit real-provider capability probe

On the existing endpoint and credentials, exactly one non-trading small-JSON stream probe succeeded through Cloudflare. HTTP 200, **5,928 ms** request total, **4,807 ms** first content, complete protocol end observed, JSON contract accepted, **one attempt**. No heartbeat was observed during this short probe; its result is not presented as proof that a long upstream stall is covered. The provider policy stayed `auto`, and its current connection-bound verified receipt now selects `stream`; JSON capability remains unknown because no additional billable JSON probe was sent.

### Natural production cycle, not an extra probe

The scheduled trading-brain call beginning **2026-10-09 13:00:12 Asia/Shanghai** succeeded with the unchanged model and effort. It used **86,621 input characters**, returned **5,553 decision-text characters**, and recorded HTTP 200 through Cloudflare, **85,260 ms** transport time (**86,557 ms** overall model-call time), **11,401 ms** first content, **two heartbeat events**, **one attempt**, and an explicit complete end marker. Model-call telemetry ID: `3215`. No manual trading cycle, expired-decision replay or forced order was used for acceptance.

This is evidence for a normal production-context streaming call on the current supplier, not a guarantee for every supplier or every future long request. The earlier 135-second continuous-heartbeat acceptance was a real loopback HTTP fixture, not an artificially prolonged billable production request. Live Responses/Claude suppliers, a supplier supporting JSON only, and all possible proxy failures were not separately purchased/tested here; their adapters and failure handling are covered by offline and loopback cases, with explicit connection-specific verification still required before claiming live capability.

### Operational evidence and limitations

Private local evidence is retained under `okxquant_frontend/.ui-artifacts/transport-20261009/continued-release/`; server build/deployment logs, original image metadata and controller remain under `/tmp/okxquant-transport-release-c61ff24/`. Temporary paths are operational evidence, not durable backups. The service release journal remains in the data volume; do not overwrite it or blindly re-run the same release controller.

The in-app Browser was unavailable in the continuation session, so **no new visual browser acceptance is claimed**. Earlier frontend/functional checks remain in the initial verification ledger; this continuation added service-side route/auth checks and live telemetry. SSH management connectivity was intermittent; the release ran independently on the server with bounded checks and rollback handling. No new host key was accepted to bypass the connectivity issue.

For a later rollback, use the same safety sequence (pause future entries, drain, verify account/protection, restore recorded image metadata, recreate only the app, health-check, restore the original entry flag). Keep `okxquant:build-86fc3d0` and the recorded metadata; do not issue an unguarded container switch while entries are active. The failure rollback path was retained but was not exercised in this successful production rollout.
