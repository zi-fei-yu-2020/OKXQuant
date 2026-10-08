# 2026-10-08 CPU and entry incident

## Observed baseline (Beijing time)

Production and local source were both `74ba776`. The selected trading environment was DEMO.
A five-minute cgroup CPU counter window (16:10:46?16:15:46) consumed 343.538 CPU seconds: **114.512% of one core**. This is container usage, not whole-server usage; the host runs other applications.

Ledger jobs around 15:56?15:58 took 24.39?26.08 CPU seconds per minute, including identical reconciliations with no JSON write. A read-only profile of the 7-day funnel decoded 6,492 large event payloads and took 10.82 CPU seconds. Strategy attribution also decoded features for 1,616 opening orders even though the current exchange page contains 100 position receipts.

At 15:56 the day had 908 minute cycles: 686 evaluated, 157 execution rejected, 59 shadow-only, 5 submitted, 1 expired. Forty Maker cancellations ended as `fallback_unknown/RiskRejected`. This is not evidence of 40 failed exchange writes: the exception handler also covered preflight.

## Fixes

- A disposable SQLite sidecar stores compact funnel facts keyed by source event ID, digest, scope and projection version. Each run queries current source metadata; only new/corrected payloads are parsed. Window movement and deletions remain authoritative. A missing/corrupt source cannot produce cached success; a broken cache falls back to the source.
- Restrict opening-decision feature decoding to order IDs relevant to the current receipt/live-position interval. Canonical historical rows and financial evidence are preserved.
- Use the `(scope,kind,at)` index directly for typed event exports rather than an optional-kind OR predicate.
- Archive each distinct position-receipt revision once, preventing another identical 100-row batch from accumulating each minute. Existing evidence is not deleted.
- A verified zero-fill canceled Maker (or explicit business rejection) can create one deterministic child decision. The original decision, expiration, account binding, candidate and risk checks remain intact. The existing unique intent constraint allows at most one child dispatch. Only that verified parent is exempt from its own correlation reservation; other correlated reservations still block.
- A partial fill during cancellation forbids a second full-size order. Missing/unknown cancellation evidence forbids fallback. Preflight rejection is reported as rejection, not an unknown exchange write.
- Fallback uses original candidate geometry, not the favorably adjusted Maker quote.
- Ledger status separates known openings today, settled closes today and openings pending exchange settlement; no missing PnL is estimated.

## Exchange history discrepancy (not fabricated away)

The SOL row opened **2026-10-07 23:46:28** and closed **2026-10-08 00:01:55**. It is a close today, not an opening today.
At 16:01?16:03, signed REST and CLI positions history both still ended at that SOL receipt. The ETH order `3990743069268537345` was nevertheless verifiably `filled` with `accFillSz=10.28` via the exact order endpoint. Different page sizes/instrument filters and a unique request query did not refresh position history; response headers were `DYNAMIC` and `no-cache/no-store`. The program must retain unresolved lifecycles as `closed_pending`, not invent closing prices, fees or realized PnL. This upstream discrepancy is a remaining external verification item, separate from the fixed local fallback bugs.

## Validation and release

Use `scripts/run_tests.py` in its disposable source snapshot (Linux/WSL). No production data, credentials, exchange writes, subprocesses or networks are available inside the offline test process. Added regression tests cover incremental decoding, source/cache corruption, digest changes, deletions, scopes, zero/partial/unknown fills, one child intent, original price geometry and rejection classification.

Deploy via local Git commit/push, server fast-forward pull, image build, isolated image tests, then Compose. Preserve the prior image for rollback. CPU comparison must use another five-minute natural-workload cgroup window after build/tests/cache warmup; never compare lifetime `ps` figures across a restart.

Cleanup targets only unused `okxquant:build-*` tags older than the current and previous releases plus expired reconstructible build cache. No volumes, application data, running containers or other services' rollback images are removed.
