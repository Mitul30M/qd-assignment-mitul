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
a thin provider adapter. Use JSON-schema model outputs and temperature 0 if a hosted model is used.

The LLM may understand language, extract intent/entities, and draft a reply. Application code must
resolve dates and ownership, enforce the dispute window, compute fares, reconcile paid history,
enforce caps, decide payout/approval/escalation, call PaySwift, persist state, and record the trace.

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
| 0. Understanding and baseline | Requirements matrix, risks, baseline repo notes | Contracts and fixtures are mapped to tests |
| 1. Runtime foundation | Service, Postgres, config, health, data loading | Clean compose startup and persistence work |
| 2. Deterministic payout core | Policy calculator, evidence queries, reconciliation | Boundary and invariant tests pass |
| 3. Conversation workflow | State, intent extraction, safe decisions, replies | All 24 supplied scenarios reach acceptable outcomes |
| 4. Money movement and ops | PaySwift adapter, approvals, escalations, UI | Replay/failure/approval paths are auditable and safe |
| 5. Black-box evals | One-command external eval runner and report | Fresh-system eval is reproducible and meaningful |
| 6. Reliability and submission | Integration hardening, docs, logs, CI | Reviewer can run and verify from a clean checkout |

## Stakeholder priorities

1. Riders: clear, respectful replies in the rider's language/register; no silent failure.
2. Finance: never overpay; respect the auto-pay and repeat-payment limits; reconcile with
   PaySwift; make uncertain provider outcomes visible.
3. Ops: actionable approval/escalation queue, reasons and evidence, approve/reject flow,
   and a complete trace of why the agent acted.
4. Engineering/review: deterministic safety boundaries around model output, repeat-safe
   webhooks, testable policy logic, and a reproducible one-command evaluation.
5. Hiring team: a small understandable design, explicit assumptions/trade-offs, evidence
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
5. Give a green signal only when the phase exit gate has evidence.

## End-to-end local runbook

Use the commands below when you want to run the app locally and try the key behaviors.

### 1. Start the full stack

```bash
cp .env.example .env
LLM_PROVIDER=stub docker compose up -d --build --wait
```

This starts:

- the app on `http://localhost:8000`
- Postgres on `localhost:5433`
- the PaySwift mock on `http://localhost:8081`

### 2. Confirm the app is healthy

```bash
curl --fail http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

### 3. Send a rider message and inspect the result

```bash
curl --fail --request POST http://localhost:8000/messages \
  --header 'Content-Type: application/json' \
  --data '{
    "message_id": "demo-001",
    "rider_id": "R003",
    "text": "Sir T926334 ke liye sirf 49 diye, poore 74 bante the",
    "received_at": "2026-09-22T10:00:00+05:30"
  }'
```

This should return a JSON object with a `reply` field.

Inspect the trace:

```bash
curl --fail http://localhost:8000/trace/R003
```

Inspect pending ops items:

```bash
curl --fail http://localhost:8000/ops/pending
```

### 4. Test duplicate-message safety

Send the same request again with the same `message_id`:

```bash
curl --fail --request POST http://localhost:8000/messages \
  --header 'Content-Type: application/json' \
  --data '{
    "message_id": "demo-001",
    "rider_id": "R003",
    "text": "Sir T926334 ke liye sirf 49 diye, poore 74 bante the",
    "received_at": "2026-09-22T10:00:00+05:30"
  }'
```

Expected behavior: it returns the same stored reply and does not create a second payout or
second approval.

### 5. Try a pending-approval case

Use a rider whose claim needs approval because it exceeds the automatic cap. This must include
specific evidence (date or trip/order context), otherwise the app will ask for missing details
instead of creating a pending ops item.

```bash
curl --fail --request POST http://localhost:8000/messages \
  --header 'Content-Type: application/json' \
  --data '{
    "message_id": "demo-approval-001",
    "rider_id": "R034",
    "text": "Sep 19 ko 3 orders ka paisa missing hai payout mein",
    "received_at": "2026-09-23T12:00:00+05:30"
  }'
```

Then inspect the pending queue:

```bash
curl --fail http://localhost:8000/ops/pending
```

This should produce a pending approval/escalation item for the verified shortfall, not a
follow-up question for more data.

### 6. Run the Python test suite

```bash
source .venv/bin/activate
pytest -q
```

Targeted checks:

```bash
source .venv/bin/activate
pytest -q tests/test_agent_flow.py tests/test_payments_db.py tests/test_payswift_client.py
```

Lint checks:

```bash
source .venv/bin/activate
ruff check app tests evals scripts
```

### 7. Run the black-box evaluator

```bash
source .venv/bin/activate
python evals/run.py http://localhost:8000 \
  --payswift-url http://localhost:8081 \
  --reset-cmd 'docker compose down -v && LLM_PROVIDER=stub docker compose up -d --wait' \
  --report evals/phase-5-report.md
```

Focused single-case run:

```bash
source .venv/bin/activate
python evals/run.py http://localhost:8000 --only fixture-01 --no-own
```

### 8. Measure latency

```bash
source .venv/bin/activate
python scripts/measure_latency.py http://localhost:8000 --count 20 --max-p95-ms 10000
```

### 9. Clean reset

```bash
docker compose down -v
LLM_PROVIDER=stub docker compose up -d --build --wait
```

### 10. Stop the stack

```bash
docker compose down -v
```

This is the simplest end-to-end flow for validating the assignment before handing it over or
reviewing any later commit.
