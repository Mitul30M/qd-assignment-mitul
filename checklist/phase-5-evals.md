# Phase 5 — Black-Box Evals

## Objectives

- Provide a reproducible evaluator that uses only the public service interface and PaySwift,
  as the hiring team will.

## Checklist

- [ ] Add `evals/run.py` (or equivalent) with the documented one-line invocation:
  `python evals/run.py <service_url> [--payswift-url http://localhost:8081] [--only ID]
  [--report out.md] [--reset-cmd ...]`.
- [ ] Start each conversation against a fresh system/ledger or clearly reset all state between
  cases so results do not leak.
- [ ] Send only rider turns; do not use database internals, private endpoints, implementation
  files, or hidden state.
- [ ] Use unique wamid-style message IDs, preserve fixture `received_at`, and warn about
  same-rider/same-day collisions.
- [ ] Verify HTTP shape, reply presence, required reply facts, PaySwift payout totals, pending
  approval totals, escalation expectation, and duplicate-side-effect behavior.
- [ ] Measure PaySwift and pending outcomes as before/after deltas, since the evaluator cannot
  reset arbitrary implementations; provide optional `--reset-cmd` and document the approach.
- [ ] Support fixture `one_of` outcomes and classify pass/fail with actionable reasons.
- [ ] Include own scenarios for duplicate delivery, overclaim, another rider's trip, out-of-
  window, repeat/same-day, >₹200, injection, abuse/out-of-scope, multi-issue, changed story,
  Hinglish/English, and actual overpayment.
- [ ] Add timeout/retry handling appropriate to the webhook contract without masking failures.
- [ ] Print a concise per-case report plus totals and exit non-zero on failures.
- [ ] Run the evaluator against the real Compose stack, not only mocks.
- [ ] Record the command, date, commit, environment/model configuration, and results for the
  final README; do not claim unrun or non-reproducible metrics.

## Exit gate

- [ ] A clean run produces a readable report and correct exit status.
- [ ] Deliberately breaking a known contract makes the evaluator fail for the right reason.
- [ ] The eval observes behavior through public interfaces only.
- [ ] Report categories include payout correctness, safety, escalation, reply facts, total
  overpaid (must be zero), total underpaid, and timeout handling; safety violations exit nonzero.
