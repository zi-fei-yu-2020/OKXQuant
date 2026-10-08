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
