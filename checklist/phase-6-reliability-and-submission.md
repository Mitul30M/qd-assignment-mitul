# Phase 6 — Reliability and Submission

## Objectives

- Turn the working prototype into a reviewable, reproducible submission with honest scope.

## Checklist

- [ ] Add focused CI jobs on every push for formatting/lint, type checks, unit tests,
  integration tests, and evaluator smoke/full run as appropriate; baseline CI should run ruff,
  pytest, Compose build, detached Compose startup with `--wait`, `/health`, and teardown
  without requiring secrets.
- [ ] Add a clean-checkout Compose smoke test and verify service shutdown leaves no required
  state outside declared volumes.
- [ ] Test malformed JSON, validation errors, dependency outage, slow model/provider, and
  graceful error replies.
- [ ] Measure latency and record p95, targeting well under the vendor's 10-second limit.
- [ ] Run the full evaluator from `docker compose down -v` followed by a clean startup; classify
  remaining failures honestly and do not claim unrun results.
- [ ] Check trace ordering and timestamps under concurrent/duplicate messages.
- [ ] Review authorization, injection resistance, secret handling, CORS/CSRF exposure if
  applicable, and data retention/logging choices.
- [ ] Keep the final [README.md](../README.md) to one page: run steps, design and model/code
  boundary, assumptions, eval results, and skipped items.
- [ ] Confirm `.env.example`, Docker instructions, eval command, and health endpoint are
  accurate from a clean checkout.
- [ ] Export raw AI session logs into [ai-logs/](../ai-logs/) and verify they are complete,
  attributable, and free of keys/tokens.
- [ ] Remove temporary files, debug endpoints, test credentials, generated artifacts, and
  unrelated changes.
- [ ] Review the final diff and commit history for accidental secrets, large files, or changes
  to PaySwift.
- [ ] Confirm the private repository, collaborator, deadline, and `main` branch submission
  logistics in [SUBMISSION.md](../SUBMISSION.md).
- [ ] Run a fresh-clone test: clone, copy `.env.example` to `.env`, add the key if needed,
  start Compose, and run the README eval command.
- [ ] Confirm everything is on `main`, tag the final state `submission`, and send the link
  before the deadline.

## Final release gate

- [ ] `docker compose up` works from a clean checkout.
- [ ] Health, message, trace, ops, PaySwift, and eval contracts are verified.
- [ ] All available fixture cases pass or any failure is explicitly documented.
- [ ] A reviewer can reproduce the stated results using only the README.
