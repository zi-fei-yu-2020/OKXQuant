# AI-only entry release — 2026-10-10 (Asia/Shanghai)

## Scope and safety

- The independent minute entry engine is retired. Only the existing 15-minute AI decision chain schedules automatic new entries; an AI proposal with a scalp holding horizon is not the retired minute engine.
- Minute scheduling, saved DEMO enable flags, saved LIVE consent, re-authorization, final preflight and immediate dispatch cannot restore minute new exposure. Matching retired positions are exit-only, with account/lifecycle isolation before blocking scale-in.
- Legacy minute context and exit libraries are retained for historical replay and existing position protection. No forced close, cloud-protection cancellation, leverage increase, or weakening of the existing USDT risk budget is part of this release.
- The production DEMO minute flag was already set false under the trade writer lock before code rollout; the existing AI auto flag remained 1 and the observed pending-order list was empty.
- The existing daily 3% threshold, capital/coverage constraints and existing event-pause mechanisms remain unchanged. This release must not be described as removing every non-daily safety prerequisite.

## AI candidate corrections

- Non-veto entry-quality warnings now have status observed_warning instead of shadow_only. They cannot pretend to be an enabled hard veto; assessment status and the actual veto remain separate.
- Range reversion requires a completed 15M trigger to actually test the prior closed-hour channel boundary and reenter, rather than merely approach within an ATR tolerance. The current quote must retain that reentry.
- New range candidate invalidation uses the tested extreme and the existing buffer; target boundaries are never extended to rescue RR. The final gateway sizes against the unchanged risk budget. Existing-position stops are not rewritten.
- Frozen boundary facts are registered as checkable references. V6/V7 pullback and range-reversion attribution labels are corrected for new reconciliations.

## Market context and evidence

- Recent macro and relevant-asset reporting receive coverage within the bounded input. Conservatively related reports can be grouped while retaining source claims; conflicting numbers/negations are not dropped as duplicate confirmations.
- Reports are labelled as unverified source claims and expectation language is identified. Crypto 4H price structure, news sentiment, actual cross-asset quotes and economic release values are explicitly different concepts.
- No independent US-equity, dollar/rates quote or economic-calendar provider is connected by this patch. Those values remain not_connected/empty, never fabricated from headlines. No new paid provider or extra model call was introduced.
- Per-cycle history includes a server-computed receipt of validated news/sentiment references from reviewed evidence containers. Missing citations do not prove the model ignored context; citation counts are not internal attention weights, causal contribution or calibrated win probabilities.

## UI

- Control panel displays the retired entry engine and keeps re-authorization controls inaccessible in that state. The backend rejects attempts to enable it.
- AI history exposes provided/cited news counts, sentiment-reference counts and the explicit data-feed limitations. Old history without receipts is not presented as having zero usage.
- Includes the previously requested ledger-pagination transition, stable page state, error/empty distinctions, account isolation and reduced-motion handling.

## Verification and release status

- Local isolated-source backend gate: 2,067 tests passed. Frontend: 287 tests passed, zero skips. Typecheck and production build passed.
- In-app Browser is unavailable; no new visual browser acceptance is claimed. Dynamic/SSR and contract tests cover the changed UI behavior.
- Production candidate image gate: 2,067 backend tests passed in 341.654 seconds, isolated from production volumes with networking disabled. Transport checks also passed locally and in the candidate image (JSON, Chat SSE, Responses, Claude, deadlines and child/socket cleanup).
- Controlled rollout completed at **2026-10-10 01:08:13 +08:00**, source commit `fc3ecef`, image `okxquant:build-fc3ecef`. The container was healthy with zero restarts at post-release verification and again at 01:19:39 +08:00.
- Both protection gates observed zero positions and zero pending orders. No forced entry/close or model probe was used. Stale decision artifacts were invalidated while paused and drained; history and cloud protection were preserved.
- Automatic AI trading was restored to `1`. The active entry schedule is `trader: 900` seconds; the independent minute engine reports `retired` and has no active scheduled job. The last recorded legacy minute job started before replacement, at 01:07:05.
- Runtime verification retained DEMO mode, `gemini-3.8-flash-high`, high reasoning, streaming transport, disabled quantity limits and the 3% daily threshold. Existing position protection and other pre-existing safety prerequisites remain.

### First natural post-release AI cycle

- History timestamp: **2026-10-10 01:15:08 +08:00**. Model call `3266` started at 01:15:13, completed successfully in 65,255 ms, without a model or reasoning downgrade.
- The overview check at approximately 01:23:35 reported cycle timestamp 01:15:01 and status `reviewed`: **6 audited WAIT, 0 incomplete, 0 entry candidates, 0 execution rejections, 0 program plans**, with no executed actions. Its wait code was `NO_PROGRAM_CANDIDATE`: a completed review, not a partial or failed inference.
- The validated market-context receipt recorded **6 articles provided, 0 structured article citations**, and no structured sentiment citations (`provided_without_structured_citation`). This confirms context delivery, not causal use; zero structured citations do not prove the model did not read the news.
- Cross-asset quotes and economic release values remain `not_connected`. No actual US-stock quote feed has been added.
- Private operational evidence is retained under `okxquant_frontend/.ui-artifacts/ai-only-release-20261010/pending-release/`. The prior image `okxquant:build-8645241` remains available for rollback; rollback must not re-enable the minute flag.
- This documentation follow-up does not require rebuilding or restarting the verified production image.
- These are correctness, authorization and data-quality changes, not a validated profitability result. The AI-only strategy requires new forward samples; no historical losses were deleted or reinterpreted as avoided profits.
