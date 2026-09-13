# Private protective-order reads

## Scope

`scripts/algo_reader.py` provides account-wide, read-only snapshots for dashboard monitoring and cloud OCO risk verification. Native API-key requests use only `GET /api/v5/trade/orders-algo-pending` with `instType=SWAP`, `ordType=conditional,oco`, and `limit=100`. Results are filtered locally by instrument; existing direction/coverage calculations remain at the caller.

The OKX documentation describes a User-ID limit of 20 requests per 2 seconds for this endpoint. The local coordinator deliberately uses a stricter workspace-wide gate: one request in flight and at least 0.30 seconds between request starts, shared across keys, modes, and Python processes using the same data directory. It cannot govern other applications or deployments using the same OKX account.

Official reference: `https://www.okx.com/docs-v5/en/#order-book-trading-algo-trading-get-algo-order-list`

## Freshness and priority

- Monitoring fetches one complete account snapshot, not two requests for every position. For five positions and fewer than 100 algo orders, the native monitor cycle changes from 10 requests to 1 (or 0 on a valid cache hit).
- Monitor cache TTL: 2 seconds. Risk cache TTL: 0.75 seconds; risk readers only reuse risk-origin snapshots. Age starts when the first page's request is admitted, not when the response finishes.
- Waiting risk readers publish OS-locked markers. Monitoring yields while risk waits, including during cooldown and pagination; an already in-flight request may finish. This is priority admission, not preemption of an HTTP request.
- Pagination follows `after` cursors, up to 10 pages per order type. Partial results, invalid identities, repeated cursors, and exhausted pagination are UNKNOWN, not an empty list.
- Runtime state is private under `data/.private-algos`, excluded from Git. Snapshot scope hashes the credentials, mode, and API host; no credentials are written to these files. Monotonic age and boot identity prevent reuse across reboot/clock changes.
- OAuth-only accounts use two explicitly typed CLI reads instead of the CLI's implicit three-type fan-out. They share admission/cooldown but do not cache snapshots because the account identity cannot be reliably scoped.

## Bounded retry and failures

Risk reads have a default/max budget of 10 seconds, monitoring 3 seconds, with at most 3 complete attempts. HTTP 429 and OKX business rate-limit codes 50011/50061 publish a shared cooldown. `Retry-After` is respected; fallback delays are 1 then 2 seconds. A cooldown beyond the remaining budget returns UNKNOWN without sending another request. Selected transient network/server errors also receive bounded read retries; authentication, parameter, invalid-response, and certificate failures do not.

Each socket timeout is limited to 2 seconds and the remaining budget; late responses are rejected. These are scheduling/retry budgets, not a hard real-time guarantee against every OS DNS/stream stall.

A confirmed complete empty response is valid `[]`. An unavailable or incomplete response raises `AlgoReadError`, never `[]`. Dashboard protection status becomes unknown/stale rather than falsely asserting protection is absent or verified. Tracker enrichment may present previously verified protection as `verification_stale`; it is not a fresh green confirmation.

## Write isolation and fail-closed policy

Existing order, cancel, amend, and close paths receive cache invalidation barriers, not new retries. They do not acquire the read gate. The barrier changes a snapshot epoch before and after each existing write attempt and publishes an active-write marker, so readers cannot publish snapshots obtained across a local mutation. Failed/ambiguous writes invalidate too. Cache bookkeeping failures are logged and do not suppress an emergency write or mask its outcome.

Initial UNKNOWN protection reads do not trigger blind OCO repair. A fresh confirmed gap retains the existing single protective-order submission. After submission (including ambiguous results), reconciliation uses forced fresh reads with a shared 6-second budget and at most 4 polls. It never resends that protective order just because the response was uncertain. A reader error terminates outer polling rather than multiplying retry loops.

Fail-closed remains: if protection cannot be confirmed after bounded verification/reconciliation, the existing confirmed-close path remains in effect. Logs distinguish `UNKNOWN` (unable to verify) from `INSUFFICIENT` (fresh confirmed coverage gap). Failed closes continue preserving position/tracker state. A stop amendment whose read is unknown is skipped, retaining existing protection.

External clients can still mutate orders or consume the same account quota. The coordinator does not claim cross-system atomicity or eliminate all possible 429 responses. Existing SDK internal write behavior is unchanged; this change adds no application-level write retries.

## Validation

Run `python scripts/run_tests.py` under Linux/WSL; the runner copies source into an isolated disposable tree, excludes credentials/runtime data, and blocks unmocked external operations.

Regression coverage includes signed combined reads, real multiprocess admission/priority, orphan markers, shared cooldown, Retry-After/deadlines, pagination failures, credential isolation, risk-vs-monitor freshness, active/in-flight/ambiguous write invalidation, no write retry on 429, no blind repair on UNKNOWN, and successful protection verification after a transient mocked 429. Live validation uses read-only DEMO queries only; intentionally exhausting OKX quota or invoking trading/repair functions is not part of validation.
