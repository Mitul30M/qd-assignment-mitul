# Phase 4 — Money Movement and Ops

## Objectives

- Make payouts safe under retries, daily limits, approval, rejection, concurrency, and
  provider uncertainty.

## Checklist

- [ ] Implement a PaySwift adapter using `PAYSWIFT_BASE_URL`, timeouts, status validation, and
  structured error handling.
- [ ] Use a stable idempotency key/reference per verified claim and persist provider response
  identifiers/status.
- [ ] Reconcile against PaySwift before deciding what remains owed; do not rely only on local
  intent/state.
- [ ] Enforce at most ₹200 automatically per rider per IST day, including concurrent requests
  and already-completed/approved amounts.
- [ ] Route larger or repeat amounts to a pending approval with amount, reason, evidence, and
  creation time; never auto-pay the excess.
- [ ] Ensure approval, rejection, duplicate approval, and retry are authorization-checked and
  idempotent.
- [ ] Ensure a PaySwift timeout/429/5xx cannot produce a success-shaped rider reply; make the
  uncertain state visible to ops and retry safely.
- [ ] Add a simple ops page showing conversations, ordered trace, pending approvals/escalations,
  evidence, and approve/reject actions.
- [ ] Verify the UI cannot approve an item for a different rider or alter its amount.
- [ ] Add tests for ₹200 exact, ₹201, repeat same day, next day, concurrent claims, provider
  409, timeout, and approval replay.
- [ ] Implement `pending → approved | rejected`; approve/reject endpoints are idempotent and
  rejection records a reason.
- [ ] Approval re-verifies owed amount and caps immediately before paying.
- [ ] Ops page at `/ops` shows pending queue, computed breakdown, conversation list, rider
  detail, messages, trace timeline, and payments; escape rider text to prevent XSS.
- [ ] If an `OPS_TOKEN` is omitted, document that scope decision in the README.
- [ ] Test two simultaneous messages for one rider, restart between payment intent and send,
  PaySwift outage, and the invariant that confirmed local payments equal the PaySwift ledger.

## Exit gate

- [ ] Every money-moving path has a persisted audit record and deterministic idempotency key.
- [ ] Finance invariants hold under retries and concurrency.
- [ ] An ops user can understand and resolve each pending item from the page.
