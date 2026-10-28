# Phase 2 — Deterministic Payout Core

## Objectives

- Build a trusted calculation/evidence layer before connecting model decisions or money movement.
- Probe PaySwift safely and implement a failure-aware client without modifying the sandbox.

## Checklist

: ₹25 base plus ₹6 for each km after the first 2 km.
- [x] Apply surge to the whole fare and round each trip to the nearest rupee with 0.5 rounding
  up, using exact arithmetic.
- [x] Implement IST calendar-day grouping and the ₹150 incentive at 12 or more completed trips.
- [x] Implement rider-cancel penalties of ₹10 and zero earnings for customer cancellations.
- [x] Enforce the seven-day dispute window using the relevant message/claim date and IST.
- [x] Calculate the verified shortfall from source trip/payout evidence; never trust a rider
  claim or model-provided amount as proof.
- [x] Handle already-paid, duplicate payout lines, missing trips, unknown riders, cancelled
  trips, and contradictory source records explicitly.
- [x] Add tests for 0/1/2 km, fractional km, surge/no surge, exact 0.5 rounding, 11/12 trips,
  IST midnight, day-window boundaries, penalties, and negative/no-shortfall cases.
- [x] Add invariant/property-style tests: owed amount is never negative; proposed payout is
  never greater than shortfall; unsupported records cannot create a payout.

## Exit gate

- [x] Calculation results independently match hand-worked examples and relevant fixtures.
- [x] The core can run without an LLM or network.
- [x] Evidence returned to the workflow is sufficient to explain the result to a rider and ops.

## Plan-specific PaySwift gates

- [x] Add a probe script and record response shapes, idempotency behavior, invalid amounts,
  unknown riders, rate limits/`Retry-After`, latency, list ordering/filtering, 5xx-after-write
  behavior, and ledger reset behavior in `docs/PAYSWIFT_NOTES.md`.
- [x] Implement connect/read timeouts, bounded exponential backoff with jitter for 429/5xx/
  timeout, `Retry-After`, no retry for 400, and explicit 409 handling.
- [x] Validate PaySwift amounts as integer ₹1–₹10,000 before calling; use a deterministic key
  from dispute ID, rider, amount, and purpose, and put the dispute ID in `reference`.
- [x] Persist payment state `intent` before the call, then `sent`, `confirmed`, or `failed`;
  recover stuck `sent` records on startup by reconciling before retrying.
- [x] Mock transport tests cover 429→201, 500-after-commit with ledger reconciliation, timeout,
  409, and 400, proving no intent can create two payouts.

---

## Implementation annex — what Phase 2 built and why (brief)

**What this phase achieves:** a trusted, LLM-free money layer. Every rupee this system
can ever pay is decided here, deterministically from source data. PaySwift is treated as
a black box used only through its documented API plus the documented safe probe.
Nothing in this phase reads rider text meaningfully or calls an LLM.

### How it's composed (small pure/one-purpose modules)

1. `app/policy.py` — pure money math (already from Phase 1, now bulk-ticked here):
   `trip_fare` (₹25 + ₹6/km after 2 km), surge × whole fare, `Decimal` + `ROUND_HALF_UP`,
   IST-day grouping, ₹150 @ ≥12 completed trips, −₹10 rider-cancel penalty, ₹0 for
   customer cancels, 7-day IST window, grand sums. Entry points: `expected_for_trip` /
   `expected_for_rider_day`.
2. `app/reconciliation.py` — owed vs paid per trip and per rider-day, with categories:
   underpaid / overpaid / missing_pay_line / duplicate_trip_line / missing_penalty /
   duplicate_penalty / surge_not_applied / paid_non_earning_trip / penalty_for_customer_cancel /
   incentive_missing / incentive_overpaid. Strict "line count checks" guard against
   double-counting duplicates.
3. `app/claims.py` (new) — `verify_dispute()`: the "convert a rider claim into verified
   shortfall" gate. It is the single authority on whether money moves.
   - Identity: unknown rider ⇒ `unknown_rider` (nothing else allowed).
   - Trip binding: a claimed trip not in data ⇒ `cannot_verify`; owned by another rider
     ⇒ `foreign_trip`. Never adopt another rider's shortfall.
   - Amount: shortfall per trip = max(0, expected − paid) from source rows. A stated
     "₹300 missing" is never trusted; the verified amount wins (tested).
   - Routing: auto-pay iff 0 < amount ≤ ₹200 **and** not already auto-paid that rider-day;
     else `approval` with reason. Hand-worked ≤/>&lt; boundary cases tested (41/41/228).
   - Incentive gaps are folded into `shortfalls` as `incentive:<IST day>`.
4. `app/payswift.py` (new) — failure-aware client (see probe notes):
   - local integer validation (1..10000) *before* any wire call (`PaySwiftInvalidRequest`);
   - deterministic `Idempotency-Key` = dispute id | rider | amount | purpose; dispute id
     always in `reference` (probe's reconciliation match key);
   - mandatory connect/read timeouts; bounded exponential backoff + jitter only for
     429/5xx/timeout, honoring `Retry-After` when it appears (the sandbox never emits one);
     400 & 409 raise immediately, never retried;
   - `create_payout` retries under the probe-proven safety rule: same key + same body
     replay is hazard-free — the provider replays the original payout.
5. `app/payments.py` (new) — the state machine (see tests):
   - `intent` persisted **before** any provider call (crash-first is safe by design),
     then `sent` → `confirmed | failed`.
   - On ambiguous outcomes the provider ledger is **reconciled via GET**, never blindly
     resent; `reference` is the match key; leading to `confirmed` (write proved) or
     `failed` (provably absent).
   - Startup recovery: any row still `sent` from a crash mid-call is reconciled at boot
     (wired into app lifespan via `_recover_stuck_payments`, see DEMO below).
   - `payments` schema gets `reference/updated_at/confirmed_at/error/details` + a UNIQUE
     partial index `(dispute_id, purpose) WHERE state <> 'failed'` so the DB itself
     refuses a second auto payout for one dispute; `idempotency_key UNIQUE` blocks
     the same bill from being raised twice.
6. `scripts/probe_payswift.py` + `docs/PAYSWIFT_NOTES.md` (new) — the black-box dossier.
   Critical findings:
   - 503/504 happen **even when the write committed** (burst 40 calls: 201×26, 503×8,
     504×6 but ledger grew by +6 writes). So a bare transport failure NEVER means
     "not paid" — reconcile is mandatory before any retry decision.
   - Replay of same key+same body returns the original payout, one ledger row. Same key
     + different body = 409. This pair makes the deterministic key both safe and a bug alarm.
   - Validation: strictly integer 1..10000 (0/negative/float/string/out-of-range → 400).
   - Sandbox does NOT validate rider existence (RPROBE-GHOST accepted, 201) — our code
     must be the identity authority (see claims.py).
   - Rate limiting = chaos, not 429s: no Retry-After observed anywhere, influencing the
     backoff design (jittered exponential; optional Retry-After honored if it appears).
   - Ledger is in-memory and **erases on restart**: evidence of money must live in our DB.

### Tests (16 new; suite total 69 passed, 11 integration-skipped locally, 4 live passed)

- `tests/test_payswift_client.py` — mocked transport (no network):
  201/happy, 429→201 with Retry-After honored (asserted sleep duration), bounded retries
  (attempts counted), 400 never retried, 409 never retried, timeout vs 5xx distinguish,
  503-after-commit→201 with same key on the ledger, exhausted-5xx raises Unavailable,
  amount validation 7 ways, deterministic key rigour.
- `tests/test_payments_db.py` — against the real compose Postgres (skips without it):
  intent-before-call + confirmed-with-payout-id ordering, `failed` is terminal-safe,
  second `intent` for the same (dispute, purpose) rejected by UNIQUE,
  stuck-`sent` recovery resolves via ledger (`confirmed` when written, `failed` when absent),
  rider-day listing excludes failed.
- `tests/test_claims.py` — pure decision tests incl. exact hand-worked amounts:
  surge-missed pays 41 (65 − 24, not the rider's "300"), over-200 routes approval,
  repeat-day routes approval, foreign/missing trip/unknown rider never route auto,
  no-negative invariant, incentive day adds 150.

### Live-stack evidence (this machine)

- `docker compose up -d --build --wait` healthy; `healthcheck` fine.
- Probe run added: `docs/payswift_probe_raw.json`, burst histogram 201×26 / 503×8 / 504×6.
- Stuck-`sent` recovery demo: manually inserted a `sent` payment as
  `disp-recovery`, restarted `docker compose restart app`; after restart the row is
  **`failed` with `error="reconciled: absent from payswift ledger"`** — recovery verified live.
- `payswift/` and its compose block remain untouched (git diff clean).

### Things to remember (carry into Phase 3/4)

- The app's readiness (`/health`) and startup seeding must ALWAYS stay LLM-free.
- Reconcile-before-retry is a MUST inside any phase-3 phase-money tool, a caller should
  never copy `create_payout` retry loops on its own.
- `payments.state <> 'failed'` UNIQUE protects duplicates only within one purpose "auto";
  ops-approved payouts need a different purpose value; keep the money amounts decided by
  this layer, never the model.
- Restore sandbox wipes its ledger; any test that asserts payout ids against the
  sandbox must tolerate a wiped ledger (IDs are not stable across restarts).
- The claim-engine never touches the model; Phase 3's model MUST route through these
  pure functions (or be the absolute only caller of `send_payout`).
- PaySwift rate-limit-like 503/504s are random even under a single burst; never make a
  test depend on zero-error responses from the sandbox; always coordinate with the mock.




--------------------------------------------------------------


Your understanding: claim verification ✅
- A rider says "300 miss kar diya!" — that's just talk, not proof.
- Our code (app/claims.py + app/policy.py + app/reconciliation.py) independently computes what the rider should have been paid from the trip sheets, compares with the actual payout lines, and gets the verified shortfall (₹41 in one case, even when the rider claimed ₹300).
- The LLM only reads/parses rider language; it never picks the rupee amount. That's the model/code boundary.
What PaySwift is
It's a dummy payment gateway (like a fake Razorpay/UPI sandbox), not a DB:
- QuickDrop would use it to actually send money to riders: POST /v1/payouts → it "pays" and keeps a ledger (list of payments it made).
- We never touch/trust our own DB for "did the money really go?" — the brief says: reconcile against PaySwift, not your database. Its ledger is the ground truth.
- It's closed-source (server.pyc we must not decompile) and deliberately misbehaves to simulate production chaos — that's why probing exists.
What the probe is
The probe (scripts/probe_payswift.py) is a safe spy on the black box: a script that sends diagnostic requests to PaySwift and records how it actually behaves, so we can write our payment code to match reality instead of trusting its README. Like test-driving a car for scratches before buying.
What it checked and found:
1. Standard stuff works as documented — 201 with payout_id, amount must be integer ₹1–₹10,000 (0/−3/5.5/"5"/10001 all rejected with 400), real payouts get recorded in the ledger, list endpoint returns them filtered by rider.
2. The crucial trap it caught: in a burst of 40 rapid calls, we sent 40 requests, only ~26 came back 201 (rest 503/504)... but the ledger held 6 MORE payouts than the 201s we received. Meaning: PaySwift sometimes pays the money and THEN tells you it failed. If our code naively thinks "503 = not paid, let me retry", we'd pay the rider twice. Sounds innocent, that's exactly the bug the brief warns about ("we lost money on this last quarter").
3. The escape hatch it proved: replaying the exact same request with the same Idempotency-Key is safe — PaySwift recognizes it and returns the original payout instead of creating a second one. But same key + different body = 409 (a scam/bug alarm).
4. Also learned: it doesn't validate rider IDs (a made-up rider gets paid!), no 429s or Retry-After headers ever appear, its ledger is in-memory (wiped on restart), and latency is ~1–3 ms.
So the probe's answer feeds straight into the client we wrote: validate amounts ourselves, always send a deterministic idempotency key, time out, back off, and on any ambiguous 5xx → look at PaySwift's ledger first before deciding the money moved or not.
TL;DR: claims verification = are we paying the right amount; PaySwift = the fake gateway we pay through; probe = the secret manual for that gateway we wrote ourselves because the docs weren't enough.
---

## Reviewer command walkthrough — run Phase 2 yourself

Read-only on the repo; execute-actions only move fake money in the sandbox
(resettable with `docker compose restart payswift`).

### 0. Make sure the stack is up

```bash
cd /Volumes/Mitul/Projects/qd-assignment
docker compose up -d --wait
curl -sS http://localhost:8000/health   # -> {"status":"ok"}
curl -sS http://localhost:8081/health   # -> {"status":"ok"} (PaySwift)
```

### 1. Run the probe yourself (watch the black box)

```bash
uv run python scripts/probe_payswift.py
# Watch the burst section: 201s interleaved with 503/504 — that's the chaos.

# Check the discrepancy (writes committed but reported as errors):
curl -sS "http://localhost:8081/v1/payouts?rider_id=RPROBE-1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('ledger rows:', d['count'])"
# -> compare with the '201' count the burst printed: ledger will be LARGER (5xx-after-write proof)
```

### 2. See the hand-worked claim math (the rupee-41 case)

```bash
uv run python - <<'PYEOF'
from decimal import Decimal
from app.policy import TripRecord, expected_for_trip
# 5 km, surge 1.5, rider was paid the no-surge fare 24
t = TripRecord("T1","R003","completed","2026-09-21T12:30:00+00:00",
               Decimal("5"), Decimal("1.5"))
print("expected:", expected_for_trip(t))   # -> 65 (43 x 1.5 = 64.5, half-up)
# verified shortfall vs what was actually paid:
paid = Decimal("24")
print("verified shortfall:", int(expected_for_trip(t) - paid))  # -> 41, not the rider's "300"
PYEOF
```

### 3. Decision engine: claims never pay more than verified

```bash
uv run pytest tests/test_claims.py -q      # pure decision tests (no LLM, no network)
# Key cases in the file: foreign/missing trips, rupee-300 claim ignored,
# >rupee-200 -> approval, repeat-day -> approval, overpay -> no negative owed
```

### 4. Payment state machine + safe replay (mocked provider / real Postgres)

```bash
uv run pytest tests/test_payswift_client.py -q   # 429->201, 400/409 never retried, timeouts, 503-after-commit
TEST_DATABASE_URL=postgresql://qd:qd@localhost:5433/qd uv run pytest tests/test_payments_db.py -q
# intent -> sent -> confirmed|failed, stuck-sent recovery, one-payout-per-dispute DB constraint
```

### 5. Try the safe-replay rule against the real sandbox (harmless demo)

```bash
# Send the SAME request 3 times with the SAME idempotency key:
K="qd-demo-$(date +%s)"
for i in 1 2 3; do
  curl -sS -X POST http://localhost:8081/v1/payouts \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $K" \
    -d '{"rider_id":"R003","amount":5,"reference":"demo-pay-once"}' | jq -c '{payout_id, status}'
done
# -> 201 with the SAME payout_id each time = only one payment exists

# But same key + different body -> 409 (bug alarm):
curl -sS -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8081/v1/payouts \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $K" \
  -d '{"rider_id":"R003","amount":9,"reference":"demo-pay-once"}'
# -> 409

# Ledger check — exactly ONE demo row exists:
curl -sS "http://localhost:8081/v1/payouts?rider_id=R003" | jq '[.data[] | select(.reference=="demo-pay-once")] | length'

# Validation demo: 0, 5.5, 10001 all rejected with 400
for a in 0 5.5 10001; do
  curl -sS -X POST http://localhost:8081/v1/payouts -H 'Content-Type: application/json' \
    -d "{\"rider_id\":\"R003\",\"amount\":$a,\"reference\":\"demo-bad\"}"; echo
done
```

### 6. Clean reset of the fake money

```bash
docker compose restart payswift   # ledger is in-memory; wipe = restart
curl -sS "http://localhost:8081/v1/payouts?rider_id=RPROBE-1"   # -> count 0
```

### 7. Everything at once (what CI runs)

```bash
uv run ruff check . && uv run pytest -q && \
SERVICE_URL=http://localhost:8000 uv run pytest -m integration -q
```

### Recap (simple words)

- **Claim verification** — a rider's "300 miss kar diya" is just talk; code computes the
  verified shortfall from trip sheets vs payout lines (41 wins, not the claim).
- **PaySwift** — a dummy payment gateway (not our DB) whose ledger is the ground
  truth for "did the money really go"; we reconcile against it, never our own DB.
- **Probe** — a safe "spy" script against the black-box gateway to learn its real
  behavior (5xx-after-write, safe replay rule, validation, no rider validation,
  in-memory ledger) so our client matches reality instead of the README.
