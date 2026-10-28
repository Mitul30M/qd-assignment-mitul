# Rider Payout Dispute Desk: Delivery Checklist

This is the working plan and review contract for the take-home assignment. Work in order
unless a later phase is explicitly proven independent. Each phase should end with a focused
commit and evidence for the gate. The chief reviewer should review every commit against the
phase checklist before allowing the next phase.

This checklist is the operational form of [PLAN.md](../PLAN.md). `PLAN.md` remains the
historical full build plan; this directory is the source to tick during implementation.

## Assignment outcome

Deliver a trustworthy, runnable dispute assistant that:

1. Receives vendor messages and maintains a conversation per rider.
2. Finds and explains payout discrepancies using the supplied exports and policy.
3. Pays only verified shortfalls through PaySwift, with idempotency and finance controls.
4. Routes approvals and unclear/suspicious cases to ops.
5. Exposes an ordered audit trace and a usable ops approval/escalation page.
6. Ships black-box evals using only the public interface and PaySwift.
7. Runs end-to-end with `docker compose up` and is documented for submission.

## Recommended architecture

The suggested baseline is Python 3.14, FastAPI, Postgres, pytest, and ruff, with an LLM behind
a thin provider adapter (Groq primary and Gemini fallback are optional). Use JSON-schema model
outputs and temperature 0 if a hosted model is used.

The LLM may understand language, extract intent/entities (trip IDs, dates, claim type, claimed
amount, language, injection flag), and draft a reply. Application code must resolve dates and
ownership, enforce the dispute window, compute fares, reconcile paid history, enforce caps,
decide payout/approval/escalation, call PaySwift, persist state, and record the trace.

Recommended per-message flow:

`dedupe → load state → extract → resolve → verify each issue → money gate → execute →
compose reply → validate reply numbers against computed facts → trace → store reply`

Recommended issue outcomes are `PAY`, `NO_DISPUTE_EXPLAIN`, `NEED_INFO`, `OUT_OF_WINDOW`,
`NOT_RIDERS_TRIP`, `ALREADY_PAID`, and `ESCALATE`. Suggested persistence includes
`inbound_messages`, `conversation_state`, `issues`, `payments` with a unique idempotency key,
`ops_items`, `trace_steps`, and seeded riders/trips/payout lines.

The fixed service contract is synchronous: the vendor has no later outbound channel, so each
reply must be produced within the roughly 10-second webhook budget.

## Non-negotiable external expectations

- `GET /health` returns 200 when the service is ready.
- `POST /messages` accepts the documented message shape and returns
  `{"reply": "..."}` within the vendor's roughly 10-second retry window.
- Replaying the same `message_id` is safe and returns a consistent result without a second
  payout, approval, or escalation.
- `GET /trace/{rider_id}` returns an ordered JSON list whose steps include `at`, `type`,
  `name`, `input`, and `output`.
- `GET /ops/pending` returns approval/escalation items with the required fields.
- PaySwift is the payout authority for reconciliation; the local database is not a substitute.
- The policy is applied in IST, only to the last seven days, with half-up rupee rounding.
- Auto-pay is at most ₹200 per rider per day; larger or repeat amounts require ops approval.
- No payout may exceed the verified amount owed.
- The supplied PaySwift service remains unchanged.

## Phase map

| Phase | Deliverable | Exit gate |
|---|---|---|
| 0. Understanding and baseline | Requirements matrix, risks, baseline repository notes | Contracts and fixtures are mapped to tests |
| 1. Runtime foundation | Service, Postgres, config, health, data loading | Clean compose startup and persistence work |
| 2. Deterministic payout core | Policy calculator, evidence queries, reconciliation | Boundary and invariant tests pass |
| 3. Conversation workflow | State, intent extraction, safe decisions, replies | All 24 supplied scenarios reach acceptable outcomes |
| 4. Money movement and ops | PaySwift adapter, approvals, escalations, UI | Replay/failure/approval paths are auditable and safe |
| 5. Black-box evals | One-command external eval runner and report | Fresh-system eval is reproducible and meaningful |
| 6. Reliability and submission | Integration hardening, docs, logs, CI | Reviewer can run and verify from a clean checkout |

## Stakeholder priorities

1. **Riders:** clear, respectful replies in the rider's language/register; no silent failure.
2. **Finance:** never overpay; respect the auto-pay and repeat-payment limits; reconcile with
   PaySwift; make uncertain provider outcomes visible.
3. **Ops:** actionable approval/escalation queue, reasons and evidence, approve/reject flow,
   and a complete trace of why the agent acted.
4. **Engineering/review:** deterministic safety boundaries around model output, repeat-safe
   webhooks, testable policy logic, and a reproducible one-command evaluation.
5. **Hiring team:** a small understandable design, explicit assumptions/trade-offs, evidence
   of eval results, and honest skipped scope.

## Known risks to keep visible

- LLM hallucinating trips, amounts, dates, rider identity, or approval state.
- Duplicate webhook delivery causing duplicate money movement.
- Concurrent messages or approvals racing against daily limits.
- UTC/IST date mistakes around the seven-day window and incentive day.
- PaySwift timeout/429/5xx leaving local state inconsistent with the provider.
- Ambiguous, contradictory, multi-issue, or impersonating rider messages.
- Overly broad trace/log data or committed API keys.
- Evals that inspect implementation internals rather than only the public contract.

## Review protocol

For each submitted commit:

1. Identify the phase and checklist items claimed.
2. Inspect the diff for correctness, scope, data safety, and contract compatibility.
3. Run the smallest relevant tests, then the phase gate if the change is cross-cutting.
4. Report blockers first, then high-value improvements, then verified checklist items.
5. Give a green signal only when the phase exit gate has evidence; otherwise specify the exact
   failing item and the next required change.

Use [commit-review-template.md](./commit-review-template.md) for a consistent review.

## Suggested time budget

The original plan assumes roughly 18–19 focused hours in a 24-hour window:

| Phase | Target |
|---|---:|
| 0. Kickoff and hygiene | 45 min |
| 1. Data audit and policy engine | 3 h |
| 2. PaySwift client and probing | 1.5 h |
| 3. Platform skeleton | 2 h |
| 4. Eval harness | 1.5 h |
| 5. Agent core | 5 h |
| 6. Approvals, ops, hardening | 2.5 h |
| 7. Eval pass and polish | 2 h |
| 8. Submission | 1 h; finish at least 1 h before deadline |

The checklist phases combine the original plan's phases where their gates are tightly coupled.

## Reviewer verdict format

Every phase review should return:

1. Verdict: green/proceed, yellow/proceed after fixes, or red/redo.
2. Severity-ranked bugs with file/line and a suggested fix.
3. Gate checklist results item by item.
4. Ranked improvements by value versus remaining time.
5. Watch-outs for the next phase.

Hotfixes to an earlier phase are allowed but must be called out in the next review.
