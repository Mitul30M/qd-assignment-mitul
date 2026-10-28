# Rider Payout Dispute Desk

ProcureYard take-home for the ML/AI Engineer Intern role. Start with `BRIEF.md`.

## Run and evaluate

```bash
LLM_PROVIDER=stub docker compose up -d --build --wait
python evals/run.py http://localhost:8000 --payswift-url http://localhost:8081 \
  --reset-cmd 'docker compose down -v && LLM_PROVIDER=stub docker compose up -d --wait' \
  --report evals/phase-5-report.md
```

The evaluator uses only the public service and PaySwift interfaces. Use
`--only fixture-01` for a focused case or `--no-own` to skip evaluator-owned
safety probes. See [checklist/phase-5-evals.md](checklist/phase-5-evals.md) for
the evaluation contract and clean-run requirements.

Latest recorded evaluation: 2026-10-28, `LLM_PROVIDER=stub`, clean Compose reset
before every case, **30/30 cases passed** (24 supplied fixtures and 6 owned
safety probes). The evaluator records payout and approval deltas, escalations,
reply facts, and actionable failure reasons.
