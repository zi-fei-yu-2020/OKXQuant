# CPU work reduction: ledger and monitoring (2026-10-08)

## Scope

- Gateway `ledger_monitor.should_run`: a scalar active/pending projection keyed by source path, device, inode, size, mtime_ns and ctime_ns. Source deletion/corruption invalidates the cache. No regular ledger parse before the 60-second active minimum; explicit refresh (5s), young pending settlements (10s), active reconciliation (60s) and idle reconciliation (300s) remain unchanged.
- Daily-loss reads: versioned settled-accounting projections exclude candle/evidence blobs but retain all account markers, close-date and nullable net/gross alias behavior. Anchor and threshold are re-evaluated independently of file caching; no financial result or account identity is cached globally.
- Dashboard: on-demand/background producers share the existing lock. The background producer skips already fresh snapshots and refreshes at five seconds, while monitoring health continues to expose cache age/stale state. This changes display cadence only, not exchange protection/strategy/risk scheduling. AI summaries are rebuilt only when their source changes; full audit details remain on the deferred endpoints.
- Checkpoints: compact monitoring projection instead of duplicate full evidence, minimum 10s between ordinary valuation-only disk checkpoints. Account/position/order/protection/health/decision changes bypass coalescing. Atomic replace, restrictive permissions and fsync remain. Live memory snapshots remain available immediately.
- Closed lifecycle reconciliation: reuse requires exact input revision (scope/baseline/instrument metadata, entire receipt, instrument fills, origins/decisions, exit inputs, peer receipts, active-position identity/quantity and lifecycle observations), complete prior accounting/origin/exit proof and an intact row digest. Missing/unverified inputs or corrections take the full reconciliation path. A stored row is not proof without matching financial inputs. Runtime status records rebuild/reuse counts and phase CPU/wall durations.
- JSON ledger serialization is compact without removing fields. An identical result does not rewrite the authoritative file. Opening trade IDs are indexed once per cycle. SQLite mirror consumes the in-memory rows, validates all rows/aliases before opening a write transaction, and updates only different projected values using NULL-safe comparisons. No deletion inferred from an incomplete snapshot; only explicit verified aliases can be removed.

## Validation

Tests cover atomic generation replacement (even same mtime/size), invalid source behavior, account isolation, 5s/10s urgent refresh preservation, no repeated parsing, complete-row reuse, revised receipt fee/net accounting, late origin evidence, peer/position input changes, compact checkpoint/account-switch behavior and idempotent NULL-aware SQL deltas.

Benchmarks are synthetic source-only tests (no credentials, exchange reads or orders). Local microbenchmark savings are not the same as whole-server CPU savings. Deployment verification should compare CPU-time deltas over equal five-minute natural-workload windows and list changing workload/positions/API visitors; do not compare lifetime ps percentages across a restart. Load averages and instantaneous container CPU alone cannot prove causality.

## Safety

No strategy thresholds, leverage, daily circuit, position protection, exchange writes or retry rules changed. Canonical ledger evidence and equity/account baselines are not reset. A corrupt cache is not used as a successful stale accounting result. Full reconciliation is retained as the fallback, not replaced by speculative fee estimates.
