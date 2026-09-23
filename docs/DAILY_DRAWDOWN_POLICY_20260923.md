# Daily Drawdown Policy Correction ? 2026-09-23

The intended opening circuit is account-equity drawdown within the current Beijing calendar day. At or above the configured 3% threshold, the system blocks new entries for the remainder of that day; the next Beijing day establishes a new daily anchor.

Historical peak drawdown remains recorded for diagnostics, but it does not independently block entries across days. This policy applies to the standard and small300 execution profiles. Existing positions remain subject to their normal exchange-native protection and lifecycle management.

The daily ledger-loss gate remains in place. Other independent safety gates (for example, macro/black-swan holds, authorization failures, stale evidence, account-query failures, position limits, and final order preflight) are unchanged. No order is submitted to validate this change.
