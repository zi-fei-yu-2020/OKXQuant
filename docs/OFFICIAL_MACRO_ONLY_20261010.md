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
- Production image gate: **2,099 tests passed in 369.043 seconds**, without network or production-volume access. The subprocess/loopback transport gate also passed.
- Controlled rollout completed at **2026-10-10 09:41:46 +08:00**, source `8041655`, image `okxquant:build-8041655`. Container health was healthy with zero restarts at post-release verification. Both protection gates observed zero positions and pending orders; no forced entry, close or model probe was used.
- The FMP entry was physically removed from the active encrypted vault, not merely hidden by the new registry. All other decrypted vault entries were compared and preserved. Project/container environment entries were verified absent, provider configuration fields were removed, and the old runtime cache was invalidated. Historical data and backups were not deleted.
- Post-release collection rebuilt `official-macro-feeds-v2` with exactly `treasury`, `bea`, `bls`. Treasury and BEA were available; BLS reported an error rather than an empty successful calendar. The Treasury observation date was 2026-10-09 and the current snapshot provided six macro fact fields.
- The deployed account-page JavaScript contains the official-source panel and no provider activation/key/timezone controls. Browser visual acceptance is still not claimed.
- AI auto remained/restored to `1`; entry cadence is 900 seconds, the minute engine is retired, quantity limits remain off and the daily threshold remains 3%. Model/high reasoning/streaming and existing protection were verified unchanged.
- Private operational evidence: `okxquant_frontend/.ui-artifacts/remove-fmp-20261010/`; server release directory: `/tmp/okxquant-remove-fmp-8041655/`. Rollback image `okxquant:build-c7fe3ce` is retained but no removed credentials are restored by rollback. Documentation-only follow-up needs no container restart.
