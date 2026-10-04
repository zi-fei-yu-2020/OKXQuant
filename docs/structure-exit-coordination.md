# Structure targets and coordinated profit exits

This release adds post-entry management and bounded recalibration for newly
admitted entries. It is not a profitability claim or a retrospective backtest.

## Entry compatibility

- No new universe, signal, direction, WAIT, cooldown, daily-stop or leverage gate.
- Existing candidate admission is evaluated first. A new 1M reversal can use a
  bounded 5M ATR stop buffer; a new swing pullback can use a bounded 1H buffer.
- Stop distance is capped at 1.75 times the originally admitted distance. The
  original target is never extended to compensate for a larger stop. If the
  existing fee-aware minimum RR is not retained, the original plan is kept.
- The final gateway rounds prices and recalculates quantity, margin, stop risk
  and portfolio risk exactly as before. Exchange minimum quantities and safety
  gates still apply; no guarantee of any particular trade count is made.
- Existing live stops are never widened. Existing v2/v4/v5/v6/v7 minute contexts
  remain readable, and v7 frozen profiles/reclaim levels remain intact.

## Two target layers

`target_layers.near` is the nearest cost-spaced opposing high/low from validated
closed 5M/15M/1H observations, excluding the trigger bar. It contains timeframe,
price, candle close time and `extrapolated=false`. Missing observed space stays
null. `extension` retains the original target and its observed/projected source.

A near-target touch alone does not close a continuing trend. A fresh completed
post-fill adverse/flat close plus sufficient current net profit can authorize
normal profit-taking near that boundary. An armed floor approaching after a
measured giveback can also authorize a preemptive normal profit exit. It never
authorizes a maker wait when a floor has already been crossed.

## Lifecycle and volatility

- `scalp-management-v8`: new reversal contexts persist the observed 5M reclaim
  level and ATR buffer. Failure requires completed 1M closes, not a single tick;
  a full post-fill 5M close distinguishes a probe from a confirmed reversal.
- `swing-structure-v1`: newly admitted swing candidates persist the 15M trigger
  and target layers. Pullback failure needs two full post-fill 15M closes plus
  a still-invalid current price. Profit management uses observed 1H ATR. Missing
  hourly data retains the armed protection, not an invented minute fallback.
- Contexts are bound to account/instrument/direction/decision and adopted only
  in the opening lifecycle window. Tracker JSON survives restarts. Unrelated or
  external positions are not retrospectively reclassified.

## Normal exits and cloud protection

During a normal profit exit, retain and verify the already armed cloud OCO rather
than tightening it into the passive quote. Local tightened floors remain in
force. The fresh executable quote is checked again against net costs and the
protected floor before one bounded reduce-only post-only attempt. Hard stops,
crossed floors, structural failure and safety exits remain immediate market
paths. No cloud protection is canceled for passive profit-taking.

`normal_profit_evaluation` records the trigger/budget/context. `profit_exit_route`
records eligibility/skip/unknown/terminal outcomes; `profit_exit_execution` records
accepted and terminal maker receipts. Missing cost proof, stale quotes, inadequate
net space and crossed floors have distinct reasons. Unknown acknowledgements or
cancellation outcomes never authorize a blind market fallback. A verified
terminal receipt permits only the freshly observed remaining quantity to close.

Compare future cohorts by setup, entry version, near-target reason, maker filled
quantity, actual entry/exit fees, peak-to-net retention and safety exits. Keep
periodic observed peaks separate from tick extremes or hypothetical profits.
