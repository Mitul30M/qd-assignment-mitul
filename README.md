# Rider Payout Dispute Desk

ProcureYard take-home for the ML/AI Engineer Intern role. This is a FastAPI
service with Postgres persistence, a deterministic policy/money boundary,
optional LLM language extraction, and the supplied PaySwift sandbox.

## Run and evaluate

```bash
cp .env.example .env
LLM_PROVIDER=stub docker compose up -d --build --wait
python evals/run.py http://localhost:8000 --payswift-url http://localhost:8081 \
  --reset-cmd 'docker compose down -v && LLM_PROVIDER=stub docker compose up -d --wait' \
  --report evals/phase-5-report.md
```

Health: `curl --fail http://localhost:8000/health`. The public contracts are
`POST /messages`, `GET /trace/{rider_id}`, and `GET /ops/pending`; the local
ops console is `/ops`. Set `OPS_TOKEN` before exposing ops routes outside a
trusted local network. Use `--only fixture-01` for a focused evaluator case or
`--no-own` to skip evaluator-owned safety probes. See
[checklist/phase-5-evals.md](checklist/phase-5-evals.md) for the contract.

## Design boundary and assumptions

The model may extract intent, trip IDs, dates, language, and injection signals,
and draft a response. Code resolves rider identity and IST dates, verifies source
data, computes rupees, enforces the seven-day window and ₹200 daily auto-pay cap,
chooses pay/approval/escalation, reconciles PaySwift, and validates reply facts.
Rider claims are hints, not payout authority. Duplicate `message_id` delivery is
idempotent. Provider timeouts are reconciled before retrying. The stub provider
is the deterministic default; hosted LLM use is optional and requires a key.
The ops console is intentionally local/simple, not a production identity system.

Latest recorded evaluation: 2026-10-28, commit `054195e`, `LLM_PROVIDER=stub`,
clean Compose reset before every case, **30/30 cases passed** (24 supplied
fixtures and 6 evaluator-owned safety probes). A clean 20-request stub latency
run measured p95 **28.3 ms**. Extra hosted-provider coverage, production auth,
outbound notifications, and a fresh-clone run remain outside this take-home scope.
