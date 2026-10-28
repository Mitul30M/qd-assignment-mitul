# Rider Payout Dispute Desk — Decision Record

## Kickoff

- Assignment email received: **Tue, 27 Oct 2026, 11:51 PM** → deadline **Tue, 28 Oct 2026, 11:51 PM** (24h), followed by a 30-minute review call.
- Submission repo: `Mitul30M/qd-assignment-mitul` (private, clone of `Procureyard/qd-assignment` via `upstream`). Graded on `main` at deadline. Collaborator `Sarthak-pyard` invited at kickoff.
- Working style: single `main` branch on the private repo; commits small and prefixed `phase-N:`; AI session raw exports saved per phase into `ai-logs/`.

## D1: Stack — Python 3.14, FastAPI, Postgres, pytest, ruff, managed by uv

- **Choice:** Python 3.14 (context-free `Decimal`, strong typing, asyncio) + FastAPI on port 8000, Postgres as state store, pytest + ruff, dependency/project management via `uv` (user's installed CPython 3.14.6).
- **Alternatives:** Node/TypeScript (fine, but the policy spec is written in India-familiar rupee/TEE terms with `Decimal` semantics that map naturally to Python's `decimal`; also the plan's reference implementation is Python); no framework (rejects — FastAPI gives the exact POST/GET contract, validation, and JSON responses cheaply).
- **Rationale:** fastest path to a testable, auditable system; reviewer expectation set by PLAN §4.
- Deviation from suggested baseline: **Python 3.14 instead of 3.12** (user's machine ships 3.14; language features used are equivalent). Recorded here for transparency.
- Note: the runtime container will use a Python 3.14 base image (`python:3.14-slim`).

## D2: Model / code boundary

| Concern | Owner | Notes |
|---|---|---|
| Understand messy language, Hinglish, relative dates, multi-issue texts, changed stories | **LLM** | extraction only, schema-constrained JSON, temperature 0 |
| Identify claimed entities: trip ids, dates, claim type, claimed ₹, language, injection signals | **LLM** | output validated; malformed → repair once → deterministic fallback |
| Rider identity | **Code** | `rider_id` binds from the webhook payload (phone-derived), never from text |
| IST calendar-day resolution, 7-day window | **Code** | against `received_at`, pure functions |
| Fare computation (`₹25 + ₹6/km beyond 2km`), surge on whole fare, half-up round | **Code** | `Decimal` + `ROUND_HALF_UP` |
| Reconciliation (CSV history + PaySwift ledger), verified shortfall | **Code** | rider-claimed ₹ is a hint only |
| Caps: ₹200/rider/IST-day auto-pay; repeat/large → approval | **Code** | |
| Payout/approval/escalation decisions | **Code** | outcome enum per issue |
| PaySwift calls, idempotency keys, reconcile-before-retry | **Code** | |
| Draft reply in rider's language from verified facts | **LLM** | every number/trip-id post-validated against computed facts |
| Reply number validation, template fallback | **Code** | LLM down → safe template reply, no false success |

Rider text is untrusted: passed to the model as delimited data, never as instructions.

## D3: Conversation state design

- Per-rider state in Postgres: open issues (`surge_missing`, `short_pay_day`, `incentive_missing`, `penalty_wrong`, `other`) each carrying trip ids, date, claimed ₹, status, missing slots; plus message history.
- Multi-issue message → issues processed independently, one combined reply.
- Clarification policy: ask only for the minimum missing slot; **max 2 asks**, then escalate.
- New issue from the same rider after silence (new problem introduced later) starts a new conversation/issue; old resolved/escalated issues stay closed and are referenced rather than re-litigated. Exact silence window decided in Phase 3 against fixtures.
- Changed story: latest turn supersedes; both are traced.

## D4: Approvals and money safety

- `ops_items` lifecycle `pending → approved | rejected`; approve/reject endpoints idempotent.
- Approve re-verifies owed amount and caps immediately before paying via PaySwift with the same deterministic idempotency key scheme (dispute id enters `reference`).
- Payment state machine: `intent` (persisted before the call) → `sent` → `confirmed|failed`; ambiguous timeout/5xx ⇒ reconcile with `GET /v1/payouts` before any retry.
- Invariant: confirmed local payments == PaySwift ledger, and confirmed payout ≤ verified shortfall, per rider.

## D5: Evals are black-box deltas

- Evals use only `POST /messages`, `/trace`, `/ops/pending`, and PaySwift's `GET /v1/payouts`. Since arbitrary implementations can't be reset, outcomes measured as before/after deltas with unique message ids, plus optional `--reset-cmd` for our own stack. Same-rider/same-day collisions warn.

## Assumptions / open items (to resolve with Phase 1 data evidence)

- Whether ₹6/km is prorated or whole-km — decided from `payout_lines.csv` history.
- Whether only `completed` trips count for the ₹150/12-trip incentive; date attribution of incentive/penalty lines (trip day vs payout day).
- Anything else surfaced by the data audit will be recorded in `docs/DATA_AUDIT.md` (Phase 1).

## Skipped scope (honest list)

- None deliberate yet; will be updated as phases settle (candidates per PLAN §5: extra eval scenarios, ops-page polish, Gemini fallback provider).
