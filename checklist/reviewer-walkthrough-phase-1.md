# Phase 1 Reviewer Walkthrough — verify the running stack yourself

Read-only command pack for the reviewer: inspect the compose stack (app, Postgres,
PaySwift) and try the Phase 1 contracts by hand. No code changes needed.

## 1. Bring the stack up (fresh state)

```bash
cd /Volumes/Mitul/Projects/qd-assignment
docker compose down -v          # wipe DB volume -> fully fresh system
docker compose up -d --build --wait
docker compose ps               # all 3 containers: app, postgres, payswift (healthy)
```

## 2. Service health / fixed contracts

```bash
# Health of all three services
curl -sS http://localhost:8000/health | jq .                    # {"status":"ok"}
curl -sS http://localhost:8081/health                            # PaySwift sandbox

# Fixed contract: POST /messages -> {"reply": "..."}
curl -sS -X POST http://localhost:8000/messages -H 'Content-Type: application/json' \
  -d '{"message_id":"wamid.TRY-1","rider_id":"R003","text":"bhai payout galat aaya hai","received_at":"2026-09-22T09:00:00+05:30"}'

# Trace: ordered steps (message_in + reply) for the rider
curl -sS http://localhost:8000/trace/R003 | jq .

# Ops pending (empty list for now)
curl -sS http://localhost:8000/ops/pending | jq .
```

## 3. Idempotency demo (vendor retry behaviour)

```bash
# Same body twice -> same reply, no second row
BODY='{"message_id":"wamid.TRY-2","rider_id":"R003","text":"order T926334 ka surge nahi mila","received_at":"2026-09-22T09:05:00+05:30"}'
curl -sS -X POST http://localhost:8000/messages -d "$BODY" | jq -r .reply
curl -sS -X POST http://localhost:8000/messages -d "$BODY" | jq -r .reply

# Prove exactly one inbound row was created for it
docker compose exec postgres psql -U qd -d qd -c \
  "SELECT message_id, rider_id, reply IS NOT NULL AS has_reply FROM inbound_messages WHERE message_id='wamid.TRY-2';"
```

## 4. Seeded data in Postgres

```bash
docker compose exec postgres psql -U qd -d qd -c "SELECT count(*) AS riders FROM riders;"
docker compose exec postgres psql -U qd -d qd -c "SELECT count(*) AS trips FROM trips;"
docker compose exec postgres psql -U qd -d qd -c "SELECT count(*) AS payout_lines FROM payout_lines;"

# A trip whose paid line had a verified shortfall in the reconciliation
docker compose exec postgres psql -U qd -d qd -c "
SELECT rider_id, ((started_at AT TIME ZONE 'Asia/Kolkata')::date) AS ist_day, status, distance_km, surge_multiplier
FROM trips WHERE trip_id='T926334';"
```

Expected: 40 riders, 3304 trips, 3222 payout lines.

## 5. Run the test suites (both modes)

```bash
uv run pytest -q                                  # unit + contract (integration skips if service down)
SERVICE_URL=http://localhost:8000 uv run pytest -m integration -q tests/test_service_contract.py
                                                  # live-service tests (needs the stack up)
```

## 6. Logs, restart persistence, PaySwift untouched

```bash
docker compose logs app --tail 20 | grep -E "POST|GET|error"    # no secrets/PII in logs
docker compose restart app && sleep 3 && curl -sS http://localhost:8000/health
# trace survives restart:
curl -sS http://localhost:8000/trace/R003 | jq length

# PaySwift service must be byte-identical to the original
git diff 8ad0a31..HEAD --stat -- payswift/                       # expect empty output
```

## Expected outputs at Phase 1

- `/health` -> `{"status":"ok"}` (503 before seeding completes)
- `/messages` -> Phase 1 **stub** reply (`Aapka message mil gaya (Phase 1 stub)...`) — the real
  agent core lands in Phase 3
- replay -> identical reply, exactly one `inbound_messages` row
- `/trace` -> two steps (`message_in`, `reply`), each with `at/type/name/input/output`
- `/ops/pending` -> `[]` (nothing touches money yet)
- data counts -> 40 riders / 3304 trips / 3222 payout lines
