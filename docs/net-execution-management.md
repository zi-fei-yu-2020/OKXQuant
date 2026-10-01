# Joint execution replay and net-profit management

## Scope

Candidate generation, ranking, entry admission, position limits, leverage and
daily drawdown gates are unchanged. These changes diagnose execution and manage
already-open positions. They are engineering hypotheses, not proven alpha.

## Frozen geometry and read-only replay

Entry submission records persist `entry_geometry`: candidate entry, submitted
entry, structural stop, target, trigger, reclaim and target provenance. The ledger
projects `joint_execution_replay` alongside official exchange accounting.

Diagnostics separate invalid geometry at fill, material entry deterioration,
observed structure failure, a closing price beyond the frozen original stop,
and insufficient evidence. A stop breach is not proof of a model error. Deterioration thresholds
are descriptive only: 0.25 initial R or 20% reward compression. They never reject
an entry. Missing observations do not prove a signal failed. Periodic peak samples
are not tick extrema. Full post-fill 1M candles may be supplied; partial entry
candles are excluded and same-bar stop/target touches remain ambiguous.

Run from the repository root:

```sh
python -m scripts.joint_trade_replay --ledger data/trading_ledger.json --evidence-db data/strategy_evidence.db --start 2026-09-29 --end 2026-10-01 --output /tmp/joint-trade-replay.json
```

The archive is opened read-only and payload digests are checked. Reports do not
change official PnL, invent fills or claim simulated profit. An optional
`--candles` JSON maps trade IDs to closed 1M bars with `close_ms/high/low`.

## Actual opening fees and remaining costs

The live tracker reconciles account, instrument, direction, decision and exact
opening order/trade IDs. Duplicate receipts are deduplicated. Size and VWAP must
match the currently held position. Conflicts, missing evidence, foreign fee
currency, scale-ins and partial exits fall back to conservative estimates rather
than allocating fictional fees. Reads are cached for 30 seconds and invalidated
on lifecycle/size/entry changes.

Verified opening fees replace the assumed opening fee. Expected taker exit fees
use current price; the existing slippage reserve remains. Net target and net
profit distances are recorded in the tracker and holding observations. Rebates
do not reduce the conservative reserve below zero. Funding remains separately
reported and is not invented for future holding time.

## Setup-specific net-profit protection

`scalp-management-v7` freezes each new entry's management parameters. Breakouts
and trend pauses retain less early net profit to allow continuation. Range
reversion retains more, tightening further near the frozen opposite boundary.
Reversals and pullbacks use their own fractions and structure observation windows.
Range positions may realize net profit near the opposite boundary. Continuation
positions require sufficient extension and two complete contrary-bodied candles
before a normal exhaustion exit. Both require positive net surplus and an intact
protective floor; neither changes candidate admission.

Profit floors retain a positive risk/volatility-scaled net surplus, not just the
fee distance. Early partial risk reduction remains distinct from net profit lock.
Existing tightened stops are never widened. Older v2/v4/v5/v6 contexts remain
readable; v6 range reclaim levels remain authoritative after restart.

## Bounded normal-profit exits

Only `trailing_exit` and profitable `time_exit` may try one `post_only` close,
and only with verified opening costs, a matching position lifecycle and a fresh
executable quote above the net floor. The order is reduce-only, journaled before
dispatch and observed for two seconds. Terminal cancellation must be read back
before fallback; market fallback uses the freshly read remaining position size.
Unexpected position growth or a changed entry price prevents a stale fallback.
Unknown ACK/status/cancellation never authorizes a second write.

Hard stops, breached profit floors, risk reduction, setup failure and protection
safety exits keep immediate market routing. Cloud OCO protection is not removed
to attempt passive profit-taking. Exchange/cloud-triggered closes are unchanged;
this change cannot turn all closing fills into maker trades.

## Verification

Use `scripts/run_tests.py` inside Linux/WSL with installed dependencies. Tests
run against a disposable source snapshot with credentials and runtime data
excluded and network/order writes blocked. Live historical replay is read-only;
its findings are diagnostic and must not be presented as a profitability backtest.

Read-only replay of September 29 through October 1 covered 112 confirmed closes:
41, 32 and 39 by Beijing close date. All had usable frozen geometry; 109 had
holding observations, totaling 2,144 periodic samples. Maximum adverse entry
deterioration was approximately 0.0714 initial R, below the descriptive threshold.
55 receipts closed beyond the original frozen stop; 10 had journaled structure
failure evaluations; 47 had no material entry deterioration but insufficient
proof of a particular structure-failure cause. Seven were cost-only net losses.
Among 19 positions with an observed peak of at least 1R, six ultimately had a
negative official net result. A 1R gross peak does not necessarily cover costs.
These are original trade outcomes, not returns from replaying the new exits.
