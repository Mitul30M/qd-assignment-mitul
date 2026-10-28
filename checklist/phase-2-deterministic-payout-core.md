# Phase 2 — Deterministic Payout Core

## Objectives

- Build a trusted calculation/evidence layer before connecting model decisions or money movement.
- Probe PaySwift safely and implement a failure-aware client without modifying the sandbox.

## Checklist

- [ ] Implement completed-trip fare: ₹25 base plus ₹6 for each km after the first 2 km.
- [ ] Apply surge to the whole fare and round each trip to the nearest rupee with 0.5 rounding
  up, using exact arithmetic.
- [ ] Implement IST calendar-day grouping and the ₹150 incentive at 12 or more completed trips.
- [ ] Implement rider-cancel penalties of ₹10 and zero earnings for customer cancellations.
- [ ] Enforce the seven-day dispute window using the relevant message/claim date and IST.
- [ ] Calculate the verified shortfall from source trip/payout evidence; never trust a rider
  claim or model-provided amount as proof.
- [ ] Handle already-paid, duplicate payout lines, missing trips, unknown riders, cancelled
  trips, and contradictory source records explicitly.
- [ ] Add tests for 0/1/2 km, fractional km, surge/no surge, exact 0.5 rounding, 11/12 trips,
  IST midnight, day-window boundaries, penalties, and negative/no-shortfall cases.
- [ ] Add invariant/property-style tests: owed amount is never negative; proposed payout is
  never greater than shortfall; unsupported records cannot create a payout.

## Exit gate

- [ ] Calculation results independently match hand-worked examples and relevant fixtures.
- [ ] The core can run without an LLM or network.
- [ ] Evidence returned to the workflow is sufficient to explain the result to a rider and ops.

## Plan-specific PaySwift gates

- [ ] Add a probe script and record response shapes, idempotency behavior, invalid amounts,
  unknown riders, rate limits/`Retry-After`, latency, list ordering/filtering, 5xx-after-write
  behavior, and ledger reset behavior in `docs/PAYSWIFT_NOTES.md`.
- [ ] Implement connect/read timeouts, bounded exponential backoff with jitter for 429/5xx/
  timeout, `Retry-After`, no retry for 400, and explicit 409 handling.
- [ ] Validate PaySwift amounts as integer ₹1–₹10,000 before calling; use a deterministic key
  from dispute ID, rider, amount, and purpose, and put the dispute ID in `reference`.
- [ ] Persist payment state `intent` before the call, then `sent`, `confirmed`, or `failed`;
  recover stuck `sent` records on startup by reconciling before retrying.
- [ ] Mock transport tests cover 429→201, 500-after-commit with ledger reconciliation, timeout,
  409, and 400, proving no intent can create two payouts.
