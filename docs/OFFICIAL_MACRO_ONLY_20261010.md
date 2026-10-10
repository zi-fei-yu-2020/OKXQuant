# Official-only macro data — 2026-10-10

## Removed

- FMP activation, API key entry/deletion, quota/licensing prompts, provider-timezone selection and index-quote display from the account-center component.
- Provider configuration fields, encrypted-vault key registration, environment example, index-catalogue/quote/calendar adapters and provider endpoint allowlist entries.
- Current quote and economic actual/estimate/previous-value evidence. Legacy fields submitted by old clients are rejected without echoing credential input.

The runtime source code and frontend contain no FMP request or activation path.
Historical release documentation and explicit negative regression tests can still
name the retired integration. Historical decisions and trade records are retained.

## Retained

- Treasury 2Y/10Y daily par yields, changes and spread; BEA/BLS official release
  schedules. These public interfaces require no provider key.
- Source timestamps, stale-data rejection, incomplete-calendar warnings, bounded
  collection and retry backoff, administrator status/refresh, shared AI facts and
  validated citation receipts.
- Official-source enablement and optimistic configuration revisions. The settings
  store no longer reads credentials or environment variables for this feature.
- Quote feeds, economic release values and the Fed policy rate remain explicitly
  not connected. No substitutes or inferred values are introduced.
- Existing 15-minute AI scheduling, retired minute entries, model/reasoning,
  position protection, risk budget and daily circuit remain unchanged.

## Cache and credential retirement

The cache schema is now `official-macro-feeds-v2`. Old provider-era caches are
rejected, including those with apparently matching configuration bindings.
Only Treasury/BEA/BLS sources and schedule-only calendar facts can be admitted.

Operational cleanup must remove the retired key from the active encrypted vault,
project/container environment and current provider configuration/cache. Other
vault entries must be compared and preserved. Do not create a plaintext or
credential-retaining retirement backup. Do not delete historical trade/decision
records or historical backups. Provider-side account/key revocation is outside
this local integration removal and is not performed.

## Verification

- Local isolated backend: **2,099 tests passed**. Frontend: **294 passed, zero
  skips**. Typecheck and production build passed.
- Includes regression checks against old flags/environment credentials, rejected
  old-client fields, blocked provider URLs, old cache reuse and current quote or
  release-value evidence; official daily rates/calendar and technical-entry
  requirements remain covered.
- In-app Browser is unavailable; no fresh visual acceptance is claimed.
- Server rollout and credential-retirement confirmation are pending at this
  source revision. A source push alone does not prove production removal.
