# Macro status and close-ledger reconciliation

## Root causes
- The public dashboard omitted `macro_assessment` despite cached model output. The UI also displayed the dashboard refresh timestamp as the analysis timestamp.
- Exchange settlement was reconciled mainly after the 15-minute trader job. Manual close confirmation did not request reconciliation.
- PnL thresholds incorrectly labeled exit causes. Positive/negative PnL is not evidence of a manual close, stop loss or take profit.

## Contract
- `macro_analysis` provides actual generation time, source, age and ready/running/blocked/failed/stale/empty states. Historical output is explicitly marked, never treated as fresh trade authorization.
- A dedicated read-only `ledger_sync` scheduler reconciles positions, position history and filled orders every 60 seconds without invoking models or trade writes.
- Confirmed admin closes journal an environment/lifecycle-scoped event and request refresh (next eligible scheduler tick, minimum 5 seconds since prior attempt). Pending settlements can poll every 10 seconds for at most 120 seconds; errors back off for 60 seconds.
- Exchange settlement latency is not eliminated: `closed_pending` explicitly means zero position confirmed but settlement unavailable. Price, PnL and ROI remain null, not fabricated zero.
- Exact filled-order evidence and full quantity coverage are required for attribution. The reserved admin client ID prefix identifies admin manual closes. Unknown external orders remain unknown; algo evidence alone does not distinguish TP from SL.
- Prefer official `realizedPnl`; otherwise sum gross PnL, fees and funding. Never split fees arbitrarily across fills.
- Verified attribution survives the recent-history window. Read failures preserve the existing ledger. Worker-discovered closes retain pending notification state for the regular notifier.
- Existing strategy evidence archival, independent position guard, writer gates and fail-closed policies remain intact.

## Deployment verification
Use read-only health/dashboard endpoints and scheduler state. Check revision and DEMO environment; preserve runtime configuration and named volumes. Do not place or close positions merely to test reconciliation. Automated regression fixtures cover the SOL manual-close receipt and net PnL of 2.78 USDT.
