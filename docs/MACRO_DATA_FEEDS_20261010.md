> Historical release record, superseded by `OFFICIAL_MACRO_ONLY_20261010.md`. The provider configuration and key instructions below are retired; do not use them as current setup guidance.

# Macro data feeds — 2026-10-10

## Scope and limits

This release connects real source data to the existing 15-minute AI context. It
adds no entry engine, model request, mandatory event pause, leverage change, or
risk-budget change. The independent minute entry engine remains retired.

| Input | Adapter | Frequency / limits |
| --- | --- | --- |
| US 2Y / 10Y par yields, changes and spread | US Treasury XML | Daily observations, hourly polling. NOT intraday bond quotes or the Fed policy rate. |
| GDP / personal income and outlays schedule | BEA official ICS | Hourly polling; schedules only, no invented consensus or actuals. |
| CPI / employment schedule | BLS official ICS | Hourly polling. An HTTP 403 remains an unavailable source, not an empty calendar. |
| S&P 500 / Nasdaq Composite / DXY | FMP index catalogue + quote | Requires an entitled key, explicit enablement and verified index identity. Polls every 15 minutes. No ETF, futures or trade-weighted-dollar substitution. |
| US economic calendar, estimate / previous / actual | FMP economic-calendar | Requires entitled key. Polls every 15 minutes; publisher timezone must be confirmed for timestamps lacking an explicit offset. |

FMP has not been live-validated with an entitled account in this release. API
access, instrument availability, distribution/model-use permissions and delay
entitlements must be checked with the account actually used. No subscription was
purchased. Quote transport is explicitly `provider_delay_unverified`, not a claim
of certified realtime data or a known market session.

Official source / provider documentation inspected:

```text
https://home.treasury.gov/interest-rates-data-csv-archive
https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml
https://www.bea.gov/news/schedule
https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics
https://www.bls.gov/schedule/news_release/bls.ics
https://site.financialmodelingprep.com/developer/docs/stable/indexes-list
https://site.financialmodelingprep.com/developer/docs/stable/index-quote
https://site.financialmodelingprep.com/developer/docs/stable/economics-calendar
```

## Configuration

Superadministrator: **Accounts > Cross-asset and economic calendar**.

1. Official public sources default on. Sources fail independently; the interface
   distinguishes unavailable, denied, stale, partially available and cached after
   a failed refresh.
2. Enter the FMP key and explicitly enable FMP after checking permissions and
   quota. The existing encrypted vault stores the key; it is never returned in
   API responses, sent to the model, or stored in provider error messages.
3. If FMP calendar dates include an explicit offset, that offset is used. If not,
   confirm the time basis with FMP and select UTC or America/New_York. The default
   is unverified: no floating timestamp is guessed. DST ambiguity/nonexistent
   wall times and all-day/undated times are rejected.
4. Save, then check due sources or wait for the collector. Probes do NOT bypass
   provider backoff and do not call an LLM or place an order. The API uses a
   separate process with a 110-second hard deadline; scheduled collection uses
   the existing isolated-job runner with a 120-second limit.
5. A blank key preserves the saved key. Explicit deletion removes the vault key
   and disables FMP; a separately configured environment key is not erased.

At continuous operation the standard FMP path needs roughly 385 requests/day
(3 quotes + calendar every 15 minutes, catalogue once/day), excluding retries,
configuration changes or extra checks. Do not assume a free account covers this.
Credential/configuration binding changes invalidate old provider snapshots.

## Interpretation and freshness

- Source collection time, quote observation time, daily observation date and
  decision evaluation time are distinct. A newly fetched old value is not live.
- Index quotes older than 30 minutes do not enter current numerical evidence.
  Weekend/holiday closes remain labelled stale-or-market-closed: there is no
  fabricated "market open" flag or zero return.
- Treasury observations older than 7 calendar days are excluded; successful
  source retrieval also expires after 24 hours. Changes/spreads use basis points,
  levels use percent. The Fed policy rate is explicitly not connected.
- Calendar window: previous 24 hours and next 7 days, at most 24 events with
  relevant/high-impact events first. Different providers' schedules are retained
  separately rather than silently fused. Coverage is US-focused and incomplete.
- Official ICS supplies only times/titles. Missing values are null, not zero.
  Future event `actual` values are stripped. Supplier values are marked as
  supplier reports, not independently reconciled official releases. Missing
  units cannot support a manufactured surprise calculation.
- One frozen snapshot supplies all coins. Common `/macro/` fact entries are sent
  once in `shared_macro_facts`, but still validated against each coin's complete
  frozen catalogue. Macro/news facts cannot substitute for two technical market
  evidence groups needed for a custom entry proposal.
- Server receipts count validated `/macro/` references and actual feed status.
  Counts are not attention weights, causal contribution, win probabilities or
  proof of better returns. Old receipts are not relabelled as current connectivity.

## Security / operations

- Fixed HTTPS endpoint allowlist, no redirects, no retries on the inference path,
  response-size ceilings, cooperative request deadlines plus hard subprocess
  deadlines, per-source retry backoff and atomic private cache writes.
- The `macro_data` job checks due feeds every 300 seconds (phase 120); it has no
  trade/exchange write path. AI only reads `data/macro_market.json`.
- Credentials are sent only to the fixed FMP origin. No secrets in URLs persisted
  to cache/logs/prompts. Admin configuration/probe routes require superadmin;
  they are registered before the public dashboard catch-all.
- UI follows existing account-center design; source health, real observation
  timestamps, absent values and permissions are more important than a generic
  green connection badge. No new public raw market-data endpoint is added.

## Verification

- Local real-interface smoke at 2026-10-10 01:54 Asia/Shanghai: Treasury available
  (latest returned observation 2026-10-08); BEA calendar readable with zero events
  in the configured seven-day forward window; BLS returned 403/access_denied.
  FMP was disabled/unconfigured and no paid/provider requests were made.
- Full isolated backend gate: **2,112 tests passed locally**; the production candidate image also passed **2,112 tests in 359.305 seconds**, network-disabled and without production volumes. Frontend: **296 tests passed, zero skips**; typecheck and production build passed. The real subprocess/loopback transport gate also passed inside the image.
- In-app Browser is unavailable (`iab`); component/SSR tests, typechecking and
  production build are used. No fresh visual browser acceptance is claimed.
- Controlled server rollout completed at **2026-10-10 02:40:13 +08:00**, commit `c7fe3ce`, image `okxquant:build-c7fe3ce`. Post-release container state was healthy with zero restarts. Both protection gates observed zero positions and zero pending orders; no forced trade, close or model probe was used.
- Automatic AI trading was restored to `1`; entry cadence remains 900 seconds. The independent minute engine remains retired. Quantity quotas remain off, the daily threshold remains 3%, and model/high reasoning/streaming and existing protection remain unchanged.
- The deployed collector was tested against the real public sources. Treasury and BEA were available; BLS returned `access_denied`. Treasury's latest returned observation date was 2026-10-08. The selected calendar window contained zero events; this is not a claim that the complete US macro calendar is empty.
- The deployed snapshot produced **6 validated macro fact fields**. FMP enablement is false and no key is configured: US-index/DXY quotes and FMP economic values are **not connected yet**. No paid provider requests were made. The follow-up verifies collection and prompt-fact integration, not a new completed natural AI inference or profitability.
- Operational evidence is retained privately in `okxquant_frontend/.ui-artifacts/macro-release-20261010/` and server release directory `/tmp/okxquant-macro-release-c7fe3ce/`. Prior image `okxquant:build-fc3ecef` is retained for rollback. Documentation-only follow-up does not require a container restart.
