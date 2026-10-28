# Assignment Working Agreement

The detailed build plan is preserved in [PLAN.md](./PLAN.md); the executable version is
organized by phase in [checklist/](./checklist/). Read the relevant phase before starting
work. If time is short, cut extra eval scenarios, UI polish, and a second LLM provider
before cutting the policy engine, money safety, idempotency, trace, evals, README, or raw
AI logs.

This repository is a ProcureYard take-home assignment for a Rider Payout Dispute Desk. Treat
the external interfaces and policy in [BRIEF.md](./BRIEF.md), [SUBMISSION.md](./SUBMISSION.md),
and [docs/policy.md](./docs/policy.md) as acceptance criteria, not suggestions.

## Before changing code

- Read the relevant phase in [checklist/README.md](./checklist/README.md) and its linked
  checklist.
- Preserve the fixed contracts: `POST /messages`, `GET /trace/{rider_id}`,
  `GET /ops/pending`, `GET /health`, and PaySwift's documented API.
- Keep PaySwift unchanged. Do not commit credentials, `.env`, generated databases, or
  secrets.
- Inspect existing worktree changes before editing; do not overwrite unrelated user work.
- Keep commits small and prefix implementation commits with `phase-N:`. Add a `phase-N-done`
  tag only after the reviewer confirms that phase's gate. Keep `main` buildable.
- Record material design choices, alternatives, rationale, and evidence in
  [docs/DECISIONS.md](./docs/DECISIONS.md).

## Engineering rules

- Keep money calculations, policy enforcement, identity binding, idempotency, and PaySwift
  reconciliation deterministic and testable in application code. The model may interpret
  rider language, but it must not be the sole authority for rupee amounts or authorization.
- The LLM may extract language, entities, intent, dates, claimed amounts, language, and
  injection signals, and draft replies from verified facts. It must never choose an amount,
  authorize a payout, decide approval, or decide escalation.
- Rider text is untrusted data: delimit it as data for any model call, never treat it as
  instructions, and never allow it to change policy or rider identity.
- Use IST calendar dates for policy decisions and the seven-day dispute window. Use decimal or
  integer-rupee arithmetic and implement the policy's half-up rounding exactly.
- Policy functions must be pure: no I/O, floating-point arithmetic, or wall-clock `now()`.
  The message's `received_at` is the only reference time. Use `Decimal` with
  `ROUND_HALF_UP`.
- Every inbound message must be safely repeatable by `message_id`; duplicate vendor delivery
  must return the same reply and must not create another payout, approval, or side effect,
  including while the first request is still processing.
- Never pay more than the verified shortfall. Enforce the ₹200-per-rider-per-day automatic
  limit and route larger/repeat amounts to ops approval.
- Do not edit `payswift/` or the PaySwift service block in `docker-compose.yml`. Treat it as a
  black box, do not decompile `server.pyc`, and learn behavior only through its documented API
  and safe probes.
- Reconcile against PaySwift before retrying an ambiguous timeout or 5xx. Always use a
  deterministic idempotency key and validate PaySwift's integer amount range before calling.
- Record an auditable trace for inbound messages, tool calls, decisions, replies, errors, and
  payout/approval/escalation outcomes. Do not put secrets or unnecessary personal data in logs.
- Surface provider, validation, and persistence failures explicitly. Do not silently approve,
  pay, or claim success after an uncertain operation.
- Prefer small, composable modules with focused tests. Add or update tests for every policy,
  boundary, failure, and replay case touched.

## Validation and phase gates

- Run the smallest relevant test/lint/type-check command after each coherent change.
- Before calling a phase complete, run its checklist and record evidence in the commit/PR
  description.
- A phase is not green if an external contract, payout invariant, trace requirement, or eval
  scenario is unverified.
- Use [checklist/commit-review-template.md](./checklist/commit-review-template.md) when
  reviewing a commit.
- After each phase, provide the reviewer the phase number, commit/tag or
  `git diff <previous-tag>..HEAD`, uncertainties, commands, and evidence. Do not start the
  next phase on a yellow or red verdict.

## Submission discipline

- Keep the final README to one page as required by the assignment; document run instructions,
  design boundaries, assumptions, eval results, and skipped items.
- Run the black-box eval against a fresh system and verify `docker compose up` from a clean
  checkout.
- Export the raw AI session logs into [ai-logs/](./ai-logs/) before submission, one file per
  session, and confirm no secrets are present.
- The final README must state actual measured eval results, assumptions, skipped work, the
  model/code decision boundary, and how to explain the design in the review call.
