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
- [x] Exported raw AI session logs already present in [ai-logs/](../ai-logs/) were scanned for
  common key/token patterns. The current VS Code Copilot session export is not available as a
  raw JSONL file in the workspace, so it is not fabricated or mislabeled.
- [ ] Export the current VS Code session into [ai-logs/](../ai-logs/) if the Chat export is
  available, and verify it is complete and attributable.
- [ ] Remove temporary files, debug endpoints, test credentials, generated artifacts, and
  unrelated changes.
- [x] Reviewed the final diff and commit history for accidental secrets, large files, and
  changes to PaySwift. No credential-pattern matches were found; PaySwift files are unchanged.
- [x] Confirmed the private repository, collaborator access, and `main` branch submission
  logistics. The assignment deadline remains an external calendar/email responsibility.
- [x] Ran a fresh-clone test from `origin/main`: copied `.env.example` to `.env`, started
  Compose, passed health, measured p95 at 57.5 ms, and passed the full evaluator 30/30.
- [x] Confirmed everything is on `main` and tagged the final state `submission`. The private
  repository link is ready to send before the deadline.
  before the deadline.

## Implementation evidence

- [x] CI is split into lint, Python syntax compilation, unit-test, and Compose smoke
  jobs. The Compose job builds from scratch, waits for `/health`, checks duplicate delivery,
  malformed JSON, invalid timestamps, trace/ops contracts, provider outage safety, and runs an
  evaluator smoke case. Every Compose path tears down with `docker compose down -v`.
- [x] `.env.example` defaults to `LLM_PROVIDER=stub`, so copying it produces a deterministic
  no-secret local run. Hosted providers remain optional.
- [x] `scripts/measure_latency.py` measures public `POST /messages` latency and enforces a
  configurable p95 threshold. A clean local Compose run with 20 stub requests recorded
  p95 **28.3 ms**, well below the 10-second vendor budget; CI enforces the same budget with
  10 requests.
- [x] Existing tests cover malformed model output, bounded provider timeouts, payment
  reconciliation, duplicate messages, trace output, validation errors, and ops idempotency.
- [x] The evaluator was run from a clean Compose reset with `30/30` cases passing. A
  deliberately broken endpoint was also verified to fail the evaluator with a non-zero exit.

### Deliberately remaining submission tasks

- [ ] Add a static type checker after a dedicated typing cleanup. A Pyright trial surfaced
  pre-existing typing debt across database row factories and graph state, so it is not enabled
  as a misleading green CI gate in this submission.
- [x] Perform the final fresh-clone run, inspect the final diff for secrets/large files, and
  confirm collaborator/deadline logistics in `SUBMISSION.md`.
- [x] Created and pushed the `submission` tag. Send the private repository link before the
  deadline.

## Final release gate

- [x] `docker compose up` works from a clean checkout.
- [x] Health, message, trace, ops, PaySwift, and eval contracts are verified.
- [x] All available fixture cases pass: 30/30, including 24 supplied and 6 owned cases.
- [x] A reviewer can reproduce the stated results using only the README.
