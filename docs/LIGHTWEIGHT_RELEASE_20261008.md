# Lightweight console and runtime release - 2026-10-08

## Authorized scope

The operator approved enabling the lightweight profile on the existing DEMO deployment and a controlled maintenance window that pauses future entries. No forced position closes, protection cancellation, risk relaxation, destructive ledger cleanup, or full language migration is authorized by this release.

## Implementation boundaries

- Canonical front routes: `/`, `/decisions`, `/market-intelligence`, `/reviews`, `/trades`. Old routes retain redirects, query and hash compatibility; backend direct navigation serves the SPA.
- Seven primary admin sections, contextual advanced controls, retained role enforcement.
- Financial monitor, trade/decision detail, prompt and cache/status APIs are session protected. Public static assets, candles, docs and health remain available. Anonymous users are not given a real-account demo snapshot.
- Original prompts require exact content digest and selected-account ownership; new decision histories carry the original frozen account scope. Legacy unowned evidence is preserved but is not silently adopted by the selected account. Account-switch archival includes these private artifacts.
- Calendar-day statistics use Asia/Shanghai; all-time means retained local verifiable history in one account/environment, not exchange lifetime. Openings and settled closes use distinct timestamps. Unknowns remain null; known partial sums and completeness are separate. Superseded/corrected lifecycle IDs are canonicalized consistently in overview, cards, lists and details. Capital baseline views do not delete retained closed history.
- Optional workloads use one persisted runtime_features configuration. Both scheduled and inline producers obey it. No news/protection/execution/ledger kill switch is exposed through it.
- Static-key common private reads reuse the signed/account-checked REST reader. OAuth and unsupported command grammars retain their existing path. Writes never pass through the read adapter, and read failures never become empty accounts or trigger credential/environment fallback.
- Mature settlement-only work may back off only when activity and durable intent evidence prove no holding and no unresolved exposure. Requests and urgent settlement paths remain fast. Unknown activity or damaged proof uses conservative active checks.
- The display-only background refresh backs off without viewers; no private monitor polling is required for a brand-new unvisited monitor. The trading and protection owner is unchanged.
- New installations explicitly start with automatic entries off. Existing deployments are not reinitialized. Superadmin changes require environment-specific confirmation and do not grant LIVE minute consent.

## Removal and preservation

Unused Vue shells `LegacyRedirect.vue`, `PendingOrders.vue` and `PositionList.vue` are removed after migrating active test coverage. Active holdings and pending-order functionality stays in the tactical desk. Repeated navigation/title definitions are centralized.

The legacy standalone admin HTML still contains advanced backup management not fully represented in the simplified Vue backup UI. It is not blindly deleted in this release. Likewise, deprecated write endpoints returning 410 remain as explicit safety/migration boundaries. Historical evidence, accounting data and backup archives are never treated as disposable code.

## Compatibility and recovery

Use the combined backend entry point and a single gateway owner. Authentication applies to direct monitor/cache/detail routes, not merely visible buttons. Session tokens belong in headers, never query strings. Existing administrator passwords are not reset.

Production release order: source commit/push, server fast-forward pull, image build, isolated image tests without production volumes/network, record original entry switch, pause future entries, drain active entry cycles, verify exchange protection, persist light settings, replace the application container, verify health/account binding/guard/ledger, then restore the original entry switch. Preserve prior images and the previous feature configuration for rollback. Do not use `docker compose down -v`.

## Verification ledger

Exact test counts, browser findings, deployed commit, workload notes and before/after measurements are appended after the final release gate. Static code review and unit tests are not described as live fills or real production mutation testing.


## Local release verification (completed)

- Isolated backend suite: **1,893 tests passed**, no external-operation violations.
- Final Linux frontend suite: **258 tests passed, zero failures and zero skips**. TypeScript checks and production client build passed.
- Edge layout review: **230 page/viewport/theme combinations**, zero document overflow and zero JavaScript errors. Seventeen admin routes and six front/document routes were covered at 320/390/768/1024/1440 widths in light/dark themes, plus keyboard focus, confirmation cancellation and mocked error interactions.
- Functional browser gate: **17 checks passed** on a marked disposable source snapshot without exchange credentials or scheduling. Coverage includes actual unmocked authenticated home API success, all private-request session headers, current-session revocation, ordinary-admin permissions and navigation, today/all totals, unknowns, full-ledger pagination/search, legacy deep links, and scoped prompt audit reopen. Benign HTML/script text remains inert text.
- Private prompt handling validates session and scope after asynchronous success as well as failure. Manual audit opens fetch current records, and the modal renders only a bound receipt, clearing it on account/session changes rather than trusting a raw monitoring field.
- The operator-approved light profile was activated on the server at approximately **2026-10-08 22:42 +08:00**; see the verified deployment section below.

## Verified production deployment

Code image: `okxquant:build-5d36af2`, independently built from the fast-forwarded server checkout. Its isolated image-level suite passed **1,893 tests in 355.218 seconds**, with networking disabled, one CPU, service UID 10001 and no production volumes.

The operator independently confirmed the original SSH ED25519 fingerprint in the provider console. Connections pinned that identity; an alternative route with a different key was rejected before authentication. Intermittent SSH failures were handled by checking durable release markers rather than replaying uncertain mutations.

A host-side locked controller journaled the original entry switch and feature configuration, then paused future entries, drained active entry cycles, checked exchange protection, persisted light mode, recreated only the app service, verified health/account binding/core jobs, and restored the original automatic-entry value `1`. Its completed journal and healthy new image were independently read back. No administrator password, account binding, account mode, risk limit, leverage or LIVE consent changed.

At both protection gates there was **one DOGE-USDT-SWAP holding with verified cloud protection coverage and zero pending orders**. No position was forcibly closed and no cloud protection order was canceled. One old-container ledger run was interrupted by the planned restart; subsequent new-container ledger runs succeeded.

Post-release checks confirmed:

- Same configured DEMO account; automatic entries restored; gateway worker alive.
- Light mode disables factor snapshots, market observations, entry research, scalp research and automatic review. Position guard, ledger/evidence sync, execution quality, news, AI trader, minute trader and scheduled backups remain available. Minute strategy jobs executed successfully after restart. By 23:01 there were 19 successful minute jobs and one safe writer-lock timeout (22:47:05; no order sent), plus 19 successful protection jobs, 18 successful ledger jobs and two successful AI-trader jobs. The minute strategy recovered on subsequent scheduled cycles; the exclusive trading writer was not bypassed or weakened.
- Live Edge/Playwright anonymous smoke checks passed for all four legacy front routes plus admin overview: login redirects, canonical deep-link query/hash preservation and mobile layout, with zero JavaScript errors. No production login/configuration mutation was used.
- Health returns 200. Anonymous requests to private overview/history/cache APIs return 401. Canonical front routes and admin shell return HTML with no-store/no-cache headers. Authenticated read/write/race testing was performed on the isolated preview, not by issuing production trades or resetting credentials.
- At approximately 22:44, ledger state was OK with 626 retained rows, 21 known openings on **2026-10-08**, 21 settled closes that day, zero pending settlements and no estimated PnL. Openings and closes are separate calendar-day measures; these counts must not be treated as identical lifecycle cohorts.
- Earlier exchange history gaps had already caught up before this image was activated. That upstream recovery is not attributed to this release.

### Derived-cache ownership correction

A previous root-run diagnostic/prewarm created `strategy_evidence_projections.db` as root with mode 0600. The real UID-10001 workers could not access it and therefore took the expensive authoritative-source fallback. Container recreation repaired runtime ownership through the existing entrypoint; a post-release service-UID check confirmed owner 10001 and read/write access. A service-UID funnel warmup completed in approximately 0.240 CPU seconds without an error. This is an operational correction contributing to runtime savings, not solely an algorithm improvement. Future application imports/prewarms must use `docker exec -u 10001:10001`.

### CPU and disk acceptance

The latest natural-workload pre-build baseline was **21:51:51-21:56:51 +08:00**: 118.260 CPU seconds over 300 seconds, or **39.420% of one core**, in standard mode with the derived-cache permission problem. The older 123.978% sample came from a different workload and is not the primary before/after denominator. Deployment started with a protected holding, whereas the preceding baseline had no holding; natural activity changes are an explicit comparison limitation.

The first post-release window, **22:48:51-22:53:51 +08:00**, consumed **142.673 CPU seconds / 47.558% of one core** after service-UID cache warmup, with automatic entries enabled. It included anonymous browser route smoke checks; there was no authenticated dashboard polling or image build/test-suite run. This is numerically about 58.5% below the original afternoon 114.512% sample, but about 20.6% above the immediately preceding idle 39.420% sample. Different holdings, strategy phases and API activity prevent either percentage from isolating the causal effect of light mode. No constant CPU ceiling or further reduction against the idle baseline is claimed.

The second window, **23:03:18-23:08:18 +08:00**, consumed **110.278 CPU seconds / 36.759% of one core**. It began after read-only verification of one protected DOGE holding and zero pending orders, with automatic entries enabled. There were no manual UI/API checks, profiles, builds or test suites during this window. The container PID remained unchanged. The same new image was healthy when results were retrieved despite intermittent SSH connection failures.

| Window (all 2026-10-08, Asia/Shanghai) | CPU seconds / approximately 300s | CPU, one core = 100% | Context |
| --- | ---: | ---: | --- |
| Original incident, 16:10:46-16:15:46 | 343.538 | 114.512% | Original code/workload; historical context |
| Latest pre-build, 21:51:51-21:56:51 | 118.260 | 39.420% | Prior standard profile, no holding, cache ownership defect |
| Light sample 1, 22:48:51-22:53:51 | 142.673 | 47.558% | Trading enabled, holding, anonymous page smoke checks |
| Light sample 2, 23:03:18-23:08:18 | 110.278 | 36.759% | Trading enabled, holding confirmed before sample, no manual checks |

Both post-release samples are retained rather than selecting only the lower value. They demonstrate approximately 0.37-0.48 CPU cores in the observed windows, not a guaranteed ceiling or a matched-load estimate of light-mode-only savings. The original incident's 114.512% figure must not be presented as the immediate baseline for this second release.

Earlier cleanup removed 18 unused old OKXQuant images and 944.6 MB of expired reconstructible build cache, freeing approximately 2.70 GB at that time. After this release build, the 22:48 root-filesystem check showed 61,186,306,048 bytes used, 31,284,846,592 bytes available (67% used). The current image plus rollback images `ddb1b35`, `74ba776` and `60b1c0a` remain. No named volumes, ledger/evidence data or unrelated-service images were removed. No further image pruning was needed for this release.

## Final review disposition

GPT-6-astra approved the bounded final store/viewer and backend closures for a non-deploying repository push. GPT-6-luna implemented the backend workload/statistics slices; gemini-3.8-flash-high was actually invoked through the configured Codex/Paseo provider for frontend implementation. Main integration independently ran the final Linux/browser gates and tightened ordinary-admin shortcuts and verified prompt rendering.

Private audit text is rendered from a session/account-bound verified receipt, cleared synchronously on context changes; it is not taken from generic monitor payloads. Manual opens fetch the latest record rather than treating a same-account private cache as indefinitely current. Delayed successful responses and delayed authentication failures cannot publish another session/account's content.

The earlier non-deploying review gate was subsequently followed by the operator-authorized maintenance procedure documented above. Source was pushed, the server fast-forwarded, and the server built/tested/recreated the app explicitly. A documentation-only follow-up commit does not require rebuilding or restarting the verified code image.
