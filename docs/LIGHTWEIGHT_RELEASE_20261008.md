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
- The operator-approved production profile is light, but **it has not yet been activated on the server**.

## Deployment gate - not completed

The original SSH route became intermittently unavailable (connection/authentication timeouts). An alternative route returned a different SSH host key and was rejected before authentication. Host verification was not disabled. No production entry pause, feature switch, image replacement or rollback was attempted during this release.

Before proceeding, recover a connection to the originally verified server or independently verify the intended host through the provider console. Then build/test the release image, apply the approved maintenance procedure, verify service-UID read/write access to derived SQLite caches and runtime files, restore the original automatic-entry setting, and take matched post-release workload measurements. Do not run application-writing diagnostic imports as root when the service runs as UID 10001.

The new pre-change production sample consumed 371.935 CPU seconds over 300 seconds (**123.978% of one core**) in the existing standard profile. There is **no post-release CPU measurement yet**; no reduction is claimed for undeployed code. Original production image at the last successful identity-verified read: `okxquant:build-ddb1b35`.


## Final review disposition

GPT-6-astra approved the bounded final store/viewer and backend closures for a non-deploying repository push. GPT-6-luna implemented the backend workload/statistics slices; gemini-3.8-flash-high was actually invoked through the configured Codex/Paseo provider for frontend implementation. Main integration independently ran the final Linux/browser gates and tightened ordinary-admin shortcuts and verified prompt rendering.

Private audit text is rendered from a session/account-bound verified receipt, cleared synchronously on context changes; it is not taken from generic monitor payloads. Manual opens fetch the latest record rather than treating a same-account private cache as indefinitely current. Delayed successful responses and delayed authentication failures cannot publish another session/account's content.

Production approval is separate and remains withheld. Repository CI only validates ordinary pushes; image publication is release-event driven. No release event or production deployment was initiated.
