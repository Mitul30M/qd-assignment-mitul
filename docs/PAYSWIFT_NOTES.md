# PaySwift Black-Box Notes

Observed via `scripts/probe_payswift.py` (raw exchanges: `docs/payswift_probe_raw.json`,
rerun with `uv run python scripts/probe_payswift.py` against the live sandbox). All probes
use synthetic `RPROBE*` riders; the ledger is in-memory and clears on restart.

## Documented + observed behavior

| Case | Result |
|---|---|
| `GET /health` | 200 `{"status":"ok"}` |
| Happy path `POST /v1/payouts` | 201 `{"payout_id":"pout_...", "rider_id","amount","reference","status":"processed","created_at"}` |
| Two calls, no `Idempotency-Key` | two separate payouts — **the key is mandatory for us** |
| Replay same key + same body | returns the *existing* payout, exactly one ledger row (verified) |
| Same key, different body | **409** `{"error":"idempotency_key_reused"}` — treat as a bug/never-retry |
| `amount` 0 / −3 / 5.5 / `"5"` / 10001 / missing | 400 `{"error":"invalid_amount"}` — integers 1..10000 only |
| `amount` 10000 | 201 — the hard ceiling |
| Unknown rider ids (`RPROBE-GHOST`) | **201 accepted** — PaySwift does not validate rider existence; OUR code must |
| `GET /v1/payouts?rider_id=X` | 200 `{"data":[...], "count":N}`, newest-first, empty list for unknown rider |
| List ordering | ledger rows sorted newest-first; do NOT assume stable order — match by `reference`/`payout_id` |

## Rate limiting / chaos (the critical finding)

40 rapid POSTs answered `201×26, 503×8, 504×6`; **no 429 and no `Retry-After` header
anywhere**. "Rate limits apply" manifests as random 503 `service_unavailable` / 504
`gateway_timeout` under load (latency stays ~1–3 ms otherwise).

**5xx-after-write is real:** of 40 burst attempts, the ledger contained **6 more
amount-1 payouts than we received 201s for** — those writes *committed server-side* while
the client saw 503/504. Therefore:

- never blindly retry an ambiguous 5xx: reconcile via `GET /v1/payouts?rider_id=...` first;
- retrying with the **same `Idempotency-Key` and same body** is the one safe blanket
  recovery: proven safe — replays return the original payout, no duplicates;
- anything else (400) must not be retried; 409 means our bookkeeping is broken.

## Client contract derived from this

1. Always send a deterministic `Idempotency-Key` (dispute id + rider + amount + purpose)
   and put the dispute id in `reference` for reconciliation matching.
2. Validate locally before the call: integer 1..10000 (else we refuse before the wire).
3. Timeouts (connect+read), retries only for 429/5xx/timeout with bounded exponential
   backoff + jitter; honoring `Retry-After` if one ever appears (none observed).
4. After an exhausted ambiguous failure: reconcile the rider's ledger before deciding
   `failed` vs `confirmed`.
5. 400 → raise, no retry. A 409 caused by `idempotency_key_reused` raises immediately
   because it indicates a deterministic-key/body bug. The sandbox can also return
   `request_in_progress` while the same idempotent write is still being processed;
   that specific response is retried with the same key and body before reconciliation.
6. Ledger is ephemeral: restart wipes it, so evidence of a payout lives in *our*
   `payments` table; state machine must treat sandbox restarts as external loss.
