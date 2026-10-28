# Phase 3 — Conversation Workflow

## Objectives

- Handle fragmented natural-language disputes without allowing the model to control safety
  decisions or invent facts. Phase-2's code layer (`claims.py`, `payments.py`,
  `payswift.py`) is the money authority; the model never touches it.

## Agreed plan (framework + persistence decisions, confirmed with reviewer)

- **This is the phase where the LLM enters.** Model role is strictly bounded: language
  parsing, entity/intent extraction, clarifying questions, reply drafting. It never picks
  amounts, routes money, decides approval, or changes rider identity.
- **Orchestration: LangGraph + LangChain** (chosen over plain-code adapter for state-machine
  ergonomics). Deps: `langgraph`, `langgraph-checkpoint-postgres`, `langchain-core`.
- **Persistence: Postgres (already in the stack) via LangGraph's official Postgres
  checkpointer**, threads keyed by `rider_id`; plus hand-written `issues` table beside it
  for per-rider dispute state; `conversations` started_at for the silence rule. One DB
  instance, no new infrastructure.
- Graph flow: `intake → extract (LLM #1) → normalize dates/ids → verify (claims.py) →
  route → compose reply (LLM #2, optional) → reply`. Linear StateGraph, bounded LLM nodes.
- Budget guardrails: LLM timeout ≤4 s per call, ≤2 calls/message, ~8 s total budget,
  deterministic fallback reply afterwards (never 5xx to vendor, never fake payout claim).
- LLM adapter: provider `groq|gemini|openai` behind LangChain's chat interface; needs
  `LLM_PROVIDER/LLM_API_KEY/LLM_MODEL` (already in config.py/.env.example); **stub
  provider** keeps tests deterministic without network.

## Checklist

- [x] Persist conversation state by authenticated `rider_id`; do not let text change identity.
- [x] Deduplicate by vendor `message_id` before any model call or side effect, and return a
  stable reply for replays.
- [x] Support vague complaints by asking for the minimum missing date/order information.
- [x] Support several rider messages, changed stories, pushback, and two issues in one message.
- [x] Resolve `kal`, `parso`, and `20 tarikh wala` against `received_at` in IST; normalize
  `t88213`, `T-88213`, and `order 88213`.
- [x] Extract candidate intent/entities (trip, date, surge, incentive, penalty, amount) with
  validation and deterministic evidence lookup.
- [x] Keep model output schema-constrained and reject malformed, unsupported, or unsafe actions.
- [x] Keep arithmetic, policy eligibility, identity scope, approval threshold, and payout
  authorization in code.
- [x] Produce respectful explanations for valid claims, invalid claims, and unavailable/
  ambiguous evidence; do not promise a payment before it is confirmed.
- [x] Handle impersonation/other-rider claims by binding all work to the webhook rider.
- [x] Treat a trip belonging to another rider as a shady signal and escalate; ignore/flag
  prompt injection and social-engineering claims such as manager/ops authorization.
- [x] Limit clarification to two asks, then escalate rather than looping.
- [x] Keep synchronous work within roughly 10 seconds: LLM timeout at or below 4 seconds,
  no more than two calls per message, total model budget around 8 seconds, and deterministic
  fallback/provider backoff.
- [x] Use issue outcomes `PAY`, `NO_DISPUTE_EXPLAIN`, `NEED_INFO`, `OUT_OF_WINDOW`,
  `NOT_RIDERS_TRIP`, `ALREADY_PAID`, and `ESCALATE`.
- [x] Trace deterministic tools such as `lookup_trip`, `list_rider_trips`,
  `compute_expected`, `get_paid_history`, `check_window`, and `check_caps`; keep
  `create_payout` code-only and not LLM-callable.
- [x] Store per-rider issue state for surge, short-pay day, incentive, penalty, and other
  disputes, including dates/trips/claimed amount/status/missing slots and a documented
  silence rule for starting a new conversation.
- [x] Explicitly consider escalation for repeat/pattern claims, strong contradiction, abuse/
  threats/legal/media, injection, data inconsistency, PaySwift failure, and human requests.
- [x] Emit ordered trace steps for message intake, tools, decisions, reply, and errors.
- [x] Test all 24 supplied conversations on a fresh system each time, including the duplicate
  delivery case and acceptable `one_of` outcome.
- [x] Test provider failures, malformed input, unknown rider, oversized text, repeated messages,
  concurrent messages, and model timeout/fallback behavior.

## Exit gate

- [x] Every fixture reaches its expected payout/approval/escalation outcome.
- [x] Required reply facts are present without exposing internal prompts or secrets.
- [x] No test can cause a payout from an unverified model assertion.
- [x] Reply numbers and trip IDs are post-validated against computed facts; LLM failure uses
  a safe template and does not claim an unconfirmed payout.

## Implementation annex (phase 3)

**Architecture.** `app/agent/` — `normalize.py` (pure trip-id/date normalization),
`extraction.py` (LLM call #1, bounded), `tools.py` (deterministic traced tools),
`issues.py` (per-rider issue state), `replies.py` (deterministic Hindi/Hinglish
templates), `flow.py` (stages + per-claim verify/act), `graph.py` (LangGraph
StateGraph `triage → safety → claims → respond`; PostgresSaver checkpointer, thread
`rider:{rider_id}`; `main.py` builds one graph + saver per process in lifespan).
`flow.handle_message` stays as a thin graph delegate for tests/scripts.

**Model boundary (enforced, not convention).** The LLM returns only
`{issues[{kind,trip_ids,days,claimed_amount}], self_rider_mention, injection,
tone_flag, asks_human}` from delimited rider text (`<rider_text>` tags; prompt
forbids instruction-following). It never sees tools, never picks amounts, and every
rupee/authorization comes from `claims.verify_dispute` + `payments.py` on source
rows. Safety overrides are regex-deterministic (`_IDENTITY_RE`, `_INJECT_RE`) so a
model miss cannot mis-bind identity or obey injection.

**Determinism measures.** Date anchor in the prompt (`aaj/kal/parso` resolved
against `received_at` IST only); multi-issue schema (each distinct problem its own
entry); sentence-split deterministic fallback (`fallback_triage`) used on provider
error/429/timeout/bad JSON; pushback rerun (`_PUSHBACK_RE`) only when the prior
issue has evidence (`ist_day`/trip_ids) — otherwise the ask-limit path; replies are
templates over verified facts only (no LLM #2 drafting; post-validation is the
template composition itself).

**Evidence guards found necessary by testing.** Out-of-window days with zero rider
rows are model hallucinations → dropped silently (real claims always have rows);
hallucinated coverage: repeat disputes subtract shortfalls already covered by
confirmed payments (`payments.details.shortfalls`) — verified shortfall is never
paid twice; `₹200/day` cap and history keyed on `payments.ist_day` (policy day =
received IST day, not wall clock; migration 001_4); `payments_idem_active_idx`
partial unique (migration 001_3) so a retry after a failed payout reuses the same
idempotency key and provider dedupe remains the guard against hidden commits.

**Deviations from plan (recorded).**
- Clarification limit is **one ask, then escalate** (stricter than "two asks").
- LLM #2 reply drafting dropped — deterministic templates only.
- Outcome strings in `issues.outcome`/trace: `PAY-DECIDED` (== PAY),
  `NO_DISPUTE_EXPLAIN`, `NEED_INFO`, `OUT_OF_WINDOW`, `NOT_RIDERS_TRIP`,
  `ALREADY_PAID`, `ESCALATE`, plus `STATUS` (status query on confirmed payouts) and
  `NO_CLAIM` (dropped hallucination).
- LLM budget: timeout 4 s, `max_retries=0`, 1 call/message (2 with one repair);
  observed ~8 s worst case per turn.

**Evidence.**
- `tests/test_agent_flow.py`: 20 integration tests (stub LLM + in-memory PaySwift
  double): verified-shortfall auto-pay + no-double-pay, second-claim-same-day →
  approval, foreign trip → identity bound + escalation, injection escalation,
  vague→ask→day-resolves→pushback no-repay, out-of-window, incentive earned vs not,
  cap→approval (R034 249), vendor-redelivery idempotency via `_claim_inbound`,
  failed-payout replay key reuse, LLM-crash fallback, malformed-LLM-output fallback,
  ask-limit escalation, fresh-graph continuity, regex identity guard, status query,
  `ist_day` policy-day, oversized text clipping, bad `received_at` 400 at the
  contract layer, and 8-way concurrent duplicate delivery (exactly one claim).
  Full suite: **100 passed**, ruff clean.
- 24-fixture replay through the graph: zero errors; confirmed = R003 25, R027 15,
  R014 10, R024 150, R007 34, R031 22, R026 32, R033 176, R035 25; approvals =
  R027 10 (conv2 `one_of` ✓), R016 425, R034 249. Groq 429-rate-limited 15 turns →
  every one absorbed by the deterministic fallback with identical money outcomes.
- Live container smoke: auto-pay ₹25 confirmed end-to-end; duplicate `message_id`
  returns stored reply; PaySwift ledger shows exactly one payout; status turn
  answered from confirmed payments.
