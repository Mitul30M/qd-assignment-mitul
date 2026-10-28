# Phase 1 — Runtime Foundation

## Objectives

- Make the service bootable, configurable, observable, and persistent under Docker Compose.
- Audit the supplied data and resolve policy ambiguities before wiring money movement.

## Checklist

- [x] Add the application service and Postgres service without modifying the existing PaySwift
  service definition.
- [x] Provide `.env.example` listing every required variable; ensure `.env` and secrets are
  ignored.
- [x] Implement `GET /health` so readiness reflects required dependencies, not merely process
  startup.
- [x] Create schema/migrations for riders, source data/evidence, inbound messages, conversation
  state, trace steps, payouts/claims, approvals, escalations, and idempotency keys as needed.
- [x] Load or mount the supplied exports reproducibly; define behavior for malformed/unknown
  records without silently inventing data.
- [ ] Add startup/connection retry behavior with explicit errors and bounded timeouts.
- [x] Add structured application logs that exclude credentials and avoid logging unnecessary
  sensitive content.
- [x] Add unit/integration coverage for configuration validation, migrations, data loading,
  health/readiness, and persistence across process restarts.
- [ ] Verify `docker compose up` starts all services and the app can reach PaySwift by its
  Compose service name.
- [x] Implement the service on port 8000 with idempotent migrations and idempotent source-data
  seeding.
- [x] Validate `POST /messages`, insert `message_id` before processing, and make concurrent
  duplicate requests wait for and return the stored reply. Valid messages should return 2xx;
  malformed requests may return 4xx; model failure must produce a safe fallback and escalation.
- [x] Implement the exact trace and ops-pending response shapes from
  [SUBMISSION.md](../SUBMISSION.md), including allowed trace types and required fields.
- [x] Add the app and Postgres services with healthcheck, volume, healthy dependency ordering,
  and `PAYSWIFT_BASE_URL=http://payswift:8081`; keep the PaySwift service byte-identical.
- [x] Add contract tests against JSON schemas for all fixed HTTP response shapes.
- [x] Add push-triggered CI for ruff, pytest, Compose build, Compose startup with `--wait`,
  `/health`, and teardown without secrets.

## Plan-specific data and policy gates

- [x] Profile row counts, nulls, duplicate IDs, status values, timestamp/timezone formats,
  orphan payout `trip_id`s, riders missing from the rider export, odd distances/surge values,
  and penalty signs; record findings in `docs/DATA_AUDIT.md`.
- [x] Build expected-versus-paid reconciliation for all trips/days and categorize missed
  surge, missing trip lines, missing incentive, wrong penalty, duplicate line, rounding, and
  overpayment anomalies in `docs/RECONCILIATION.md`.
- [x] Resolve prorated-km, incentive trip-counting, and incentive/penalty date attribution
  ambiguities from data evidence and record counts in `docs/DECISIONS.md`.
- [x] Match payout lines to trips by `trip_id`, not payout date; account for PaySwift's
  in-memory ledger in final paid history.
- [x] Implement and test pure `trip_fare`, `ist_day`, `daily_incentive`,
  `cancellation_penalty`, `expected_for_trip`, and `expected_for_rider_day` functions.
- [x] Test 3.5 km × 1.25 = ₹43, exactly 2 km = base only, half-up .5 cases, IST day
  boundaries, 11 versus 12 completed trips, customer cancellation = 0, and rider
  cancellation = -₹10.

## Exit gate

- [x] From a clean checkout, Compose reaches healthy state and `/health` returns 200.
- [x] A message can be persisted and traced after an app restart.
- [ ] Tests fail clearly when required configuration or dependencies are unavailable.
- [x] The policy engine has no I/O, floats, or wall-clock `now()` and uses
  `Decimal`/`ROUND_HALF_UP`; golden fixture mismatches are explained.
