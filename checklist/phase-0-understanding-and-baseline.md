# Phase 0 — Understanding and Baseline

## Objectives

- Convert the brief, policy, provider API, data exports, and submission logistics into
  executable acceptance criteria.
- Establish the smallest credible architecture and identify deliberate assumptions.

## Checklist

- [ ] Read [BRIEF.md](../BRIEF.md), [README.md](../README.md), [SUBMISSION.md](../SUBMISSION.md),
  [data/README.md](../data/README.md), [docs/policy.md](../docs/policy.md), and
  [payswift/README.md](../payswift/README.md).
- [ ] Record all fixed routes, request/response fields, status expectations, and environment
  variables in the implementation design.
- [ ] Profile the supplied CSV/JSON data: row counts, date range, statuses, null/duplicate
  behavior, rider/trip relationships, and representative edge cases.
- [ ] Enumerate all 24 fixture conversations and map each expected payout, approval, escalation,
  and reply fact to a test case.
- [ ] Decide and document the model boundary: language/intent extraction versus deterministic
  evidence, arithmetic, authorization, and side effects.
- [ ] Decide and document conversation state, pending questions, multi-issue handling, and
  conflict/impersonation behavior.
- [ ] Decide how approval/rejection is exposed and how an approved item is paid exactly once.
- [ ] Write an assumptions/skipped-scope list rather than hiding uncertainty.
- [ ] Confirm the worktree is clean except for intentional assignment files and leave
  `.DS_Store`/secrets untracked.

## Exit evidence

- [ ] A short design decision record exists in the implementation docs or README.
- [ ] Every fixed contract has an owner module and at least one planned test.
- [ ] No unresolved ambiguity can cause an unsafe payout.

## Plan-specific kickoff gates

- [ ] Record assignment email receipt time and exact deadline at the top of
  [docs/DECISIONS.md](../docs/DECISIONS.md).
- [ ] Confirm the repository is a clone, not a fork; create/repoint the private submission
  repository, push it, and invite `Sarthak-pyard` as collaborator when submission work begins.
- [ ] Choose the stack and record why in `docs/DECISIONS.md` (suggested baseline: Python 3.12,
  FastAPI, Postgres, pytest, and ruff).
- [ ] Establish one raw AI-session export per phase and export it into [ai-logs/](../ai-logs/)
  at phase end.
- [ ] Plan the expected layout: `app/`, `evals/`, `tests/`, `scripts/`, `docs/DECISIONS.md`,
  `.github/workflows/ci.yml`, `.env.example`, and `.gitignore` covering `.env`,
  `__pycache__`, and virtual environments.
- [ ] Commit the skeleton with no secrets and no changes to `payswift/`.
