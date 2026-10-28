# Phase 4 — Money Movement and Ops

## Objectives

- Make payouts safe under retries, daily limits, approval, rejection, concurrency, and
  provider uncertainty.

## Checklist

- [x] Implement a PaySwift adapter using `PAYSWIFT_BASE_URL`, timeouts, status validation, and
  structured error handling.
- [x] Use a stable idempotency key/reference per verified claim and persist provider response
  identifiers/status.
- [x] Reconcile against PaySwift before deciding what remains owed; do not rely only on local
  intent/state.
- [x] Enforce at most ₹200 automatically per rider per IST day, including concurrent requests
  and already-completed/approved amounts.
- [x] Route larger or repeat amounts to a pending approval with amount, reason, evidence, and
  creation time; never auto-pay the excess.
- [x] Ensure approval, rejection, duplicate approval, and retry are authorization-checked and
  idempotent.
- [x] Ensure a PaySwift timeout/429/5xx cannot produce a success-shaped rider reply; make the
  uncertain state visible to ops and retry safely.
- [x] Add a simple ops page showing conversations, ordered trace, pending approvals/escalations,
  evidence, and approve/reject actions.
- [x] Verify the UI cannot approve an item for a different rider or alter its amount.
- [x] Add tests for ₹200 exact, ₹201, repeat same day, next day, concurrent claims, provider
  409, timeout, and approval replay.
- [x] Implement `pending → approved | rejected`; approve/reject endpoints are idempotent and
  rejection records a reason.
- [x] Approval re-verifies owed amount and caps immediately before paying.
- [x] Ops page at `/ops` shows pending queue, computed breakdown, conversation list, rider
  detail, messages, trace timeline, and payments; escape rider text to prevent XSS.
- [x] If an `OPS_TOKEN` is omitted, document that scope decision in the README.
- [x] Test two simultaneous messages for one rider, restart between payment intent and send,
  PaySwift outage, and the invariant that confirmed local payments equal the PaySwift ledger.

## Exit gate

- [x] Every money-moving path has a persisted audit record and deterministic idempotency key.
- [x] Finance invariants hold under retries and concurrency.
- [x] An ops user can understand and resolve each pending item from the page.

## Phase 4 implementation summary

Phase 4 implemented a safety-first payout and operations layer around PaySwift. The
implementation treats every payout as a persisted state transition, validates the
amount and claim before money movement, and assumes that provider responses can be
delayed, duplicated, or lost.

### Implemented capabilities

- **PaySwift adapter**
  - Uses the configured `PAYSWIFT_BASE_URL` with bounded HTTP timeouts.
  - Validates payout amounts as integer rupees within the supported ₹1–₹10,000 range.
  - Sends a deterministic `Idempotency-Key` and stable dispute reference.
  - Handles successful responses, validation errors, conflicts, rate limits, server
    errors, timeouts, and malformed provider responses explicitly.
  - Retries only transient failures and reconciles ambiguous operations against the
    PaySwift ledger before deciding whether a payout remains unresolved.

- **Persisted payment state machine**
  - Records `intent`, `sent`, `confirmed`, and `failed` states in Postgres.
  - Persists the provider payout ID, reference, idempotency key, IST policy day,
    errors, timestamps, and shortfall details.
  - Reuses the same idempotency key when safely retrying a failed or uncertain
    operation.
  - Reconciles stuck `sent` payments during service startup before allowing a retry.

- **Daily payout limits and duplicate protection**
  - Enforces the ₹200 automatic payout limit per rider per IST day.
  - Uses database constraints and indexes to protect the limit under concurrent
    requests.
  - Prevents duplicate automatic payments for the same rider and policy day.
  - Accounts for already-confirmed shortfalls when calculating what remains owed.

- **Approval and rejection workflow**
  - Routes repeat, over-cap, and provider-uncertain claims to the ops queue instead
    of auto-paying them.
  - Stores the claim evidence, amount, reason, category, issue, and creation time.
  - Re-verifies the current owed amount immediately before approval; the queued
    amount is never treated as the authority.
  - Reserves an approval using a database compare-and-set transition:
    `pending → processing → approved`.
  - Releases `processing` items back to `pending` after verification or provider
    failure, allowing safe operator retry.
  - Supports idempotent approval and rejection, records rejection reasons, and
    prevents concurrent approvers from paying the same item twice.

- **Rider notices**
  - Queues approval and rejection decisions as persisted notices.
  - Delivers the notice on the rider's next message and marks it delivered.
  - Keeps provider failures from producing a misleading success message.

- **Ops console**
  - Provides `/ops` with pending queue, evidence, computed breakdown, conversation
    list, rider details, messages, trace timeline, payments, and actions.
  - Supports approve/reject endpoints with optional `OPS_TOKEN` authorization.
  - Escapes rider-controlled values before rendering HTML, including conversation
    previews and rider detail data.
  - Does not accept an operator-supplied payout amount or rider ID for approval;
    both are derived from the persisted queue item and fresh verification.

- **Recovery, concurrency, and test coverage**
  - Covers retries, duplicate claims, exact and over-limit amounts, same-day repeats,
    next-day behavior, provider conflicts, timeouts, outages, approval replay,
    concurrent approvals, XSS escaping, token enforcement, and service recovery.
  - Uses a controlled PaySwift test double for deterministic integration tests while
    preserving the real adapter and Compose service contract.

### Approach

The phase follows a **persist first, verify again, then pay** approach:

1. Verify the rider's claim using application-owned policy and source data.
2. Persist an intent or an ops queue item with deterministic identifiers and evidence.
3. Enforce daily and duplicate-payment invariants at the database layer.
4. Call PaySwift only with a validated amount and stable idempotency key.
5. Treat uncertain provider outcomes as unresolved until ledger reconciliation proves
   the payout result.
6. Require fresh verification and a database reservation before human approval.
7. Persist the final outcome and notify the rider without claiming success unless the
   payout is confirmed.

This design keeps money decisions deterministic and auditable, makes retries safe,
and ensures operational failures are visible without creating duplicate payouts or
success-shaped replies for unconfirmed payments.

## How to verify Phase 4 locally

The following commands exercise the implementation through the real Compose services
and the host-side integration tests. Run them from the repository root.

### 1. Start the complete stack

For a reproducible walkthrough, reset the local database before the first run.
This removes only the local Compose database volume; do not use it if you want to
preserve existing test data:

```bash
docker compose down -v
```

```bash
LLM_PROVIDER=stub docker compose up -d --build --wait
```

`LLM_PROVIDER=stub` makes this walkthrough deterministic and prevents a live model
from misclassifying fixture identifiers. If you intentionally use a live provider,
configure its credentials separately and do not expect identical wording or
extraction behavior.

Confirm that the application, Postgres, and PaySwift containers are running:

```bash
docker compose ps
curl --fail http://localhost:8000/health
curl --fail http://localhost:8081/health
```

Expected result:

```json
{"status":"ok"}
```

### 2. Submit a verified shortfall that should auto-pay

`R003` and trip `T926334` are present in the supplied fixture data. This example
claims a ₹25 shortfall, which is within the ₹200 automatic daily limit:

```bash
curl --fail --request POST http://localhost:8000/messages \
  --header 'Content-Type: application/json' \
  --data '{
    "message_id": "phase4-auto-001",
    "rider_id": "R003",
    "text": "Sir T926334 ke liye sirf 49 diye, poore 74 bante the",
    "received_at": "2026-09-22T10:00:00+05:30"
  }'
```

On a clean database, the first request should explain the verified shortfall and
confirm the payout only after PaySwift confirms it. Inspect the persisted payment:

```bash
docker compose exec -T postgres psql -U qd -d qd -c \
  "SELECT rider_id, amount, purpose, dispute_id, idempotency_key, state,
          provider_payout_id, ist_day
   FROM payments
   WHERE rider_id = 'R003'
   ORDER BY created_at DESC;"
```

Expected observations:

- one `confirmed` payment for ₹25;
- a non-empty provider payout ID;
- a deterministic dispute/reference and idempotency key;
- `ist_day` equal to `2026-09-22`.

If the command has already been run with this same `message_id`, the stored
success reply is expected to be returned again. That is the inbound-message
idempotency check; it returns the original reply without running the workflow
again. Use the clean-database reset in Step 1 if you need to observe the initial
auto-pay response again.

### 3. Prove message idempotency and no duplicate payout

Send the same `message_id` again, even with the same payload:

```bash
curl --fail --request POST http://localhost:8000/messages \
  --header 'Content-Type: application/json' \
  --data '{
    "message_id": "phase4-auto-001",
    "rider_id": "R003",
    "text": "Sir T926334 ke liye sirf 49 diye, poore 74 bante the",
    "received_at": "2026-09-22T10:00:00+05:30"
  }'
```

The stored reply should be returned without a new side effect. Compare the local
payment count with the PaySwift ledger:

```bash
docker compose exec -T postgres psql -U qd -d qd -c \
  "SELECT COUNT(*) AS local_confirmed
   FROM payments
   WHERE rider_id = 'R003' AND state = 'confirmed';"

curl --fail 'http://localhost:8081/v1/payouts?rider_id=R003'
```

There should not be a second payout for the same verified claim.

To test the separate duplicate-claim guard, send the same claim with a **new**
`message_id`:

```bash
curl --fail --request POST http://localhost:8000/messages \
  --header 'Content-Type: application/json' \
  --data '{
    "message_id": "phase4-auto-repeat-001",
    "rider_id": "R003",
    "text": "Sir T926334 ke liye sirf 49 diye, poore 74 bante the",
    "received_at": "2026-09-22T10:00:00+05:30"
  }'
```

This may return a response such as
`₹25 PaySwift se aa chuke hain. Dobara payment nahi ho sakta.` The new message is
processed, but the confirmed payment and verified shortfall history show that
the claim was already paid, so no second payout is created.

### 4. Exercise the approval queue

The previous `R003` repeat is intentionally **not** an approval item: it is an
already-paid claim, so `/ops/pending` correctly remains empty. To create a
reproducible approval item, use the supplied `R034` fixture. Its verified
shortfall is ₹249, above the ₹200 automatic limit:

```bash
curl --fail --request POST http://localhost:8000/messages \
  --header 'Content-Type: application/json' \
  --data '{
    "message_id": "phase4-approval-001",
    "rider_id": "R034",
    "text": "Sep 19 ko 3 orders ka paisa missing hai payout mein",
    "received_at": "2026-09-23T11:30:00+05:30"
  }'

curl --fail http://localhost:8000/ops/pending
```

The response should explain that ₹249 is waiting for ops approval. The pending
response should contain an approval item for `R034` with amount `249`, evidence,
reason, and creation time. Open the human-facing console:

```bash
open http://localhost:8000/ops
```

The page should show the pending item, evidence, conversation preview, and
approve/reject controls. To inspect the rider-specific audit trail:

```bash
open 'http://localhost:8000/ops?rider=R034'
curl --fail http://localhost:8000/trace/R034
```

### 5. Approve or reject from the API

First capture the pending item ID:

```bash
OPS_ID="$(curl --fail --silent http://localhost:8000/ops/pending \
  | python -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"
printf 'Pending item: %s\n' "$OPS_ID"
```

Approve it through the ops endpoint:

```bash
curl --fail --request POST "http://localhost:8000/ops/${OPS_ID}/approve"
```

The result should show `approved` only when fresh verification and the PaySwift
payment both succeed. Repeating the request must not create another payout:

```bash
curl --fail --request POST "http://localhost:8000/ops/${OPS_ID}/approve"
```

For a rejection-path check, create another pending item and reject it with an
explicit reason:

```bash
curl --fail --request POST "http://localhost:8000/ops/${OPS_ID}/reject" \
  --header 'Content-Type: application/json' \
  --data '{"reason":"manual Phase 4 rejection test"}'
```

The rejection response should be idempotent on replay and the reason should be
visible in the ops item record and trace.

### 6. Check the persisted audit trail and notices

```bash
docker compose exec -T postgres psql -U qd -d qd -c \
  "SELECT id, rider_id, type, amount, status, reason, created_at,
          resolved_at, resolution_note
   FROM ops_items
   ORDER BY created_at DESC;"

docker compose exec -T postgres psql -U qd -d qd -c \
  "SELECT rider_id, ops_item_id, decision, amount, delivered_at
   FROM notices
   ORDER BY created_at DESC;"
```

After an approved or rejected decision, send the rider a new message and verify
that the queued notice is included once in the response and then marked delivered.

The assignment has no outbound rider notification endpoint, so the ops action
does not immediately produce a separate HTTP response to the rider. The notice
is delivered on the rider's next `POST /messages` request. For the approved
`R034` item, use:

```bash
curl --fail --request POST http://localhost:8000/messages \
  --header 'Content-Type: application/json' \
  --data '{
    "message_id": "phase4-rider-followup-001",
    "rider_id": "R034",
    "text": "Mera dispute ka kya update hai?",
    "received_at": "2026-09-23T12:00:00+05:30"
  }'
```

The response should begin with a rider-facing approval update similar to:

```text
Update: aapka dispute ops approval ke baad process ho gaya — ₹249 PaySwift se aa gaya hai.
```

For a rejection, the response begins with a rejection update and the stored
category explanation. Confirm that the notice was consumed exactly once:

```bash
docker compose exec -T postgres psql -U qd -d qd -c \
  "SELECT rider_id, ops_item_id, decision, amount, delivered_at
   FROM notices
   WHERE rider_id = 'R034'
   ORDER BY created_at DESC;"
```

`delivered_at` should be populated after the follow-up message. Replaying the
same `message_id` returns the stored reply and does not deliver the notice a
second time.

### 7. Run the deterministic Phase 4 test suite

The host-side integration tests use the Postgres port exposed by Compose:

```bash
source .venv/bin/activate
TEST_DATABASE_URL='postgresql://qd:qd@localhost:5433/qd' \
SERVICE_URL='http://localhost:8000' \
pytest -q \
  tests/test_agent_flow.py \
  tests/test_payments_db.py \
  tests/test_payswift_client.py \
  tests/test_contracts.py \
  tests/test_service_contract.py
```

The suite covers automatic limits, repeat claims, approval replay, concurrent
approvals, provider errors, timeout/retry behavior, reconciliation, token
authorization, XSS escaping, and service contracts. A successful Phase 4 run
should report all selected tests passing; integration tests may skip only when
the test database is unavailable.

Run the complete quality checks:

```bash
source .venv/bin/activate
ruff check app tests
docker compose config >/dev/null
git diff --check
```

### 8. Stop the local stack

Keep the database volume when you want to inspect data later:

```bash
docker compose down
```

To remove the local Postgres volume and start with a clean database on the next
run, use this only when intentionally resetting local test data:

```bash
docker compose down -v
```
