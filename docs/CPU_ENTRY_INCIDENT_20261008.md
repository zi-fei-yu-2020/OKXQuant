# 2026-10-08 CPU and entry incident

## Observed baseline (Beijing time)

Production and local source were both `74ba776`. The selected trading environment was DEMO.
A five-minute cgroup CPU counter window (16:10:46-16:15:46) consumed 343.538 CPU seconds: **114.512% of one core**. This is container usage, not whole-server usage; the host runs other applications.

Ledger jobs around 15:56-15:58 took 24.39-26.08 CPU seconds per minute, including identical reconciliations with no JSON write. A read-only profile of the 7-day funnel decoded 6,492 large event payloads and took 10.82 CPU seconds. Strategy attribution also decoded features for 1,616 opening orders even though the current exchange page contains 100 position receipts.

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
At 16:01-16:03, signed REST and CLI positions history both still ended at that SOL receipt. A same-day ETH opening order was nevertheless verifiably `filled` via the exact order endpoint (raw account-specific identifiers are omitted here). Different page sizes/instrument filters and a unique request query did not refresh position history; response headers were `DYNAMIC` and `no-cache/no-store`. The program must retain unresolved lifecycles as `closed_pending`, not invent closing prices, fees or realized PnL. This upstream discrepancy is a remaining external verification item, separate from the fixed local fallback bugs.

## Validation and release

Use `scripts/run_tests.py` in its disposable source snapshot (Linux/WSL). No production data, credentials, exchange writes, subprocesses or networks are available inside the offline test process. Added regression tests cover incremental decoding, source/cache corruption, digest changes, deletions, scopes, zero/partial/unknown fills, one child intent, original price geometry and rejection classification.

Deploy via local Git commit/push, server fast-forward pull, image build, isolated image tests, then Compose. Preserve the prior image for rollback. CPU comparison must use another five-minute natural-workload cgroup window after build/tests/cache warmup; never compare lifetime `ps` figures across a restart.

Cleanup targets only unused `okxquant:build-*` tags older than the current and previous releases plus expired reconstructible build cache. No volumes, application data, running containers or other services' rollback images are removed.


## Verified release results

Code release: `ddb1b35`, deployed at approximately **2026-10-08 16:38 +08:00**. Local WSL and the independently built server image both passed **1,827 offline tests**. Image tests ran with networking disabled, a one-core limit, and no production data volumes. The old application stayed running until this gate passed. The pre-release protection gate found zero active positions at the actual switch. The new container is healthy and continues to run the scheduler and reconciliation.

| Measurement | Before | After |
| --- | ---: | ---: |
| Container cgroup CPU, five-minute natural window | 343.538 CPU seconds / 114.512% of one core | 203.011 CPU seconds / 67.670% of one core |
| Successful warm ledger jobs | 24.39-26.08 CPU seconds | 12.793-13.989 CPU seconds |
| Same-data funnel benchmark, identical output verified | 9.6733 CPU seconds | 0.3101 CPU seconds |

The observed container average fell **40.9%**; the same-data funnel computation fell **96.8%**. The after window was **16:40:49-16:45:49**. These are short natural-workload samples, not a promise of a constant CPU ceiling. The after window includes one cold receipt-revision rebuild (100 rows) and subsequent successful warm jobs (95 reused / 5 rebuilt). Exchange timeouts occurred before and shortly after restart; normal scheduled reconciliation recovered. Production workload, positions, visitors and upstream latency can differ between windows. The additional old/new benchmark ran only after the natural CPU window ended.

At the 16:40-16:43 checks, the ledger showed **8 known openings today, all 8 pending settlement, and 1 settled close today**. Position history still ended at the previous night's SOL lifecycle. `ledger_sync.settlement_diagnostics` now exposes this discrepancy through the existing dashboard API. No settlement values were fabricated. No natural post-release Maker fallback event had yet occurred at the final targeted check; its full route was verified offline, not by forcing a trade.

Cleanup removed **18 unused old OKXQuant images** and **944.6 MB of expired reconstructible build cache**. Root filesystem usage fell from 63,518,195,712 to 60,815,245,312 bytes immediately after cleanup: **2.70 GB freed**. After building the replacement image, final measured usage was 60,933,038,080 bytes (66% full), with 31,538,114,560 bytes available. Running containers, named volumes, evidence/ledger data and other services' rollback images were preserved. Application rollback images `okxquant:build-74ba776` and `okxquant:build-60b1c0a` remain.

The Compose image/build metadata keys are persisted in the server checkout's ignored `.env`; no credentials were written to source or this report. The deploy uses `okxquant:build-ddb1b35`. A later documentation-only commit does not require another application restart.


### Final natural execution check

A subsequent read-only check observed a naturally scheduled SUI minute candidate completing `maker_canceled -> fallback_result(status=accepted)`, with its cycle recorded as `submitted`. No manual/test opening was triggered. Acceptance is not a guarantee of fill or profitability. This supersedes the earlier observation that no post-release fallback event had yet occurred.

At the final health check the ledger reported **9 known openings today / 8 pending settlements / 1 settled close today**, and the container remained healthy. An active/changed-position ledger cycle used 21.24 CPU seconds; the 12.793-13.989-second figures above describe unchanged warm reconciliation, not every possible future cycle. The historical-settlement gap is still unresolved upstream.


## Subsequent light release and measurement correction

The earlier sections record observations at their stated times, not the final state of October 8. By 21:46 the upstream settlement-history gap had caught up; this recovery preceded the light release and is not credited to its code. At approximately 22:42, image `5d36af2` replaced `ddb1b35` through the approved protected maintenance procedure. See `LIGHTWEIGHT_RELEASE_20261008.md` for release gates and final measurements.

An intervening root diagnostic/prewarm had created the derived evidence-projection SQLite file as root:0600. UID-10001 production workers could not use that cache and fell back to full source parsing. The light-release restart repaired ownership, and subsequent service-UID checks and warmup verified cache access. Earlier same-data warm-cache benchmarks remain isolated benchmark results; they must not be represented as proof that the prior production workers were actually benefiting from that cache. This operational mistake and correction are included in the before/after interpretation.
