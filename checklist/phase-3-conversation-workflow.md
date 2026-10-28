# Phase 3 — Conversation Workflow

## Objectives

- Handle fragmented natural-language disputes without allowing the model to control safety
  decisions or invent facts.

## Checklist

- [ ] Persist conversation state by authenticated `rider_id`; do not let text change identity.
- [ ] Deduplicate by vendor `message_id` before any model call or side effect, and return a
  stable reply for replays.
- [ ] Support vague complaints by asking for the minimum missing date/order information.
- [ ] Support several rider messages, changed stories, pushback, and two issues in one message.
- [ ] Resolve `kal`, `parso`, and `20 tarikh wala` against `received_at` in IST; normalize
  `t88213`, `T-88213`, and `order 88213`.
- [ ] Extract candidate intent/entities (trip, date, surge, incentive, penalty, amount) with
  validation and deterministic evidence lookup.
- [ ] Keep model output schema-constrained and reject malformed, unsupported, or unsafe actions.
- [ ] Keep arithmetic, policy eligibility, identity scope, approval threshold, and payout
  authorization in code.
- [ ] Produce respectful explanations for valid claims, invalid claims, and unavailable/
  ambiguous evidence; do not promise a payment before it is confirmed.
- [ ] Handle impersonation/other-rider claims by binding all work to the webhook rider.
- [ ] Treat a trip belonging to another rider as a shady signal and escalate; ignore/flag
  prompt injection and social-engineering claims such as manager/ops authorization.
- [ ] Limit clarification to two asks, then escalate rather than looping.
- [ ] Keep synchronous work within roughly 10 seconds: LLM timeout at or below 4 seconds,
  no more than two calls per message, total model budget around 8 seconds, and deterministic
  fallback/provider backoff.
- [ ] Use issue outcomes `PAY`, `NO_DISPUTE_EXPLAIN`, `NEED_INFO`, `OUT_OF_WINDOW`,
  `NOT_RIDERS_TRIP`, `ALREADY_PAID`, and `ESCALATE`.
- [ ] Trace deterministic tools such as `lookup_trip`, `list_rider_trips`,
  `compute_expected`, `get_paid_history`, `check_window`, and `check_caps`; keep
  `create_payout` code-only and not LLM-callable.
- [ ] Store per-rider issue state for surge, short-pay day, incentive, penalty, and other
  disputes, including dates/trips/claimed amount/status/missing slots and a documented
  silence rule for starting a new conversation.
- [ ] Explicitly consider escalation for repeat/pattern claims, strong contradiction, abuse/
  threats/legal/media, injection, data inconsistency, PaySwift failure, and human requests.
- [ ] Emit ordered trace steps for message intake, tools, decisions, reply, and errors.
- [ ] Test all 24 supplied conversations on a fresh system each time, including the duplicate
  delivery case and acceptable `one_of` outcome.
- [ ] Test provider failures, malformed input, unknown rider, oversized text, repeated messages,
  concurrent messages, and model timeout/fallback behavior.

## Exit gate

- [ ] Every fixture reaches its expected payout/approval/escalation outcome.
- [ ] Required reply facts are present without exposing internal prompts or secrets.
- [ ] No test can cause a payout from an unverified model assertion.
- [ ] Reply numbers and trip IDs are post-validated against computed facts; LLM failure uses
  a safe template and does not claim an unconfirmed payout.
