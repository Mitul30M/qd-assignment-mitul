# Rider Payout Dispute Desk — Build Plan & Phase Checklist

> For the local coding agent. Read this whole file first, then work phase by phase.
> A reviewer checks every phase against its **Gate** before the next phase starts. Do not skip ahead.

---

## 0. Rules for the coding agent (non-negotiable)

1. **Money is decided by code, never by the LLM.** The LLM may understand language, extract facts and write replies. It may never choose an amount, a payout, or whether something needs approval.
2. **Never pay more than the rider is owed.** Overpaying is the worst failure. Under-paying or escalating is always the safe fallback.
3. **Do not edit `payswift/` or the `payswift` service in `docker-compose.yml`.** Only add services next to it. Treat PaySwift as a black box (do not decompile `server.pyc`); learn it by probing.
4. **Policy code is pure:** no I/O, no floats, no `now()`. "Now" is always the message's `received_at`.
5. **Use `Decimal` with `ROUND_HALF_UP`.** Python's `round()` is banker's rounding and is wrong here.
6. **Rider text is untrusted data.** It never reaches the LLM as instructions and never changes policy.
7. **No secrets in git.** `.env` is ignored; every env var is listed in `.env.example`.
8. **Small commits, prefix `phase-N:`.** Tag `phase-N-done` when the phase gate is met. `main` must always build.
9. **Log every non-trivial choice** in `docs/DECISIONS.md` (choice, alternatives, why, evidence).
10. Keep every AI session's raw export for `ai-logs/` (see Phase 8).

---

## 1. The assignment in one page

**Company:** QuickDrop (fictional), ~5,000 riders. Riders WhatsApp complaints like "bhai kal ka payout 120 kam aaya" or "order T88213 ka surge nahi mila". Ops executives take two days per reply.

**Build:** an agent that holds the rider conversation end to end: asks for what it needs, checks trips and payouts, pays if the rider is right, explains if wrong, escalates if unclear or shady, and leaves a full audit trail.

**Deliverables**
| # | Deliverable | Notes |
|---|---|---|
| 1 | Agent service on port **8000** | `POST /messages`, `GET /trace/{rider_id}`, `GET /ops/pending`, `GET /health` |
| 2 | Evals | One command: `python evals/run.py http://localhost:8000`. May use **only** the HTTP interface and PaySwift (graders run it against other implementations) |
| 3 | Ops page | See conversations and agent actions; approve/reject pending items |
| 4 | `docker compose up` | Your service + Postgres + the existing `payswift` service |
| 5 | GitHub Actions | Runs on every push |
| 6 | `README.md` (max one page) | How to run, agent design (tools; what model decides vs code decides), assumptions, eval results, what you skipped |
| 7 | `ai-logs/` | **Raw** exports of AI sessions, one file per session, not summaries |
| 8 | `.env.example` | Every env var; no `.env`, no keys committed |

**Submission mechanics**
- Due **24 hours after the assignment email was received**. A 30-minute call follows (you must be able to explain every design choice and line).
- **Clone** `Procureyard/qd-assignment` (do not fork). Build in a **new private repo** you own. Add **`Sarthak-pyard`** as a collaborator. Reply to the email with the link before the deadline.
- They grade whatever is on **`main`** at the deadline.
- Python or Node, any framework or none, any LLM provider (free tiers of Groq / Gemini suffice).

---

## 2. Stakeholder expectations (what each party will judge)

**Meera, Head of Ops**
- Handles messy chat like her executives: multi-message replies, changed stories, two complaints at once, Hinglish.
- Rider right → paid and told. Rider wrong → a real explanation, never silence. Unclear/shady → ops, with the reason.
- She must see *what happened and why* (trace + ops page) and trust it **before** it moves money.

**Finance**
- Auto-pay only up to **₹200 per dispute, once per rider per day**. Larger or repeat → ops approval.
- **Never pay more than owed.**
- Reconcile against **PaySwift**, not your DB. PaySwift is the source of truth for what was paid.

**Messaging-vendor engineer**
- Vendor sends one message at a time; if no 2xx in ~10s it **retries the same `message_id` (wamid)**. Must be idempotent.
- `rider_id` comes from the phone number (trustworthy identity). Whatever `reply` you return goes to the rider.

**ProcureYard reviewers (hiring team)**
- Run your evals against your system and others. Check money safety, correctness vs. expected outcomes, trace quality, engineering hygiene, honest README, and how you used AI (the `ai-logs/`).
- The call tests ownership: explain trade-offs, what you skipped and why.

---

## 3. Things to know (traps and hidden requirements)

**Policy math**
- Trip fare = `₹25 + ₹6 × max(0, km − 2)`; the surge multiplier applies to the **whole** fare (base + distance); round **per trip**, half up.
  - Test case: 3.5 km, surge 1.25 → 25 + 9 = 34 → ×1.25 = 42.5 → **₹43** (banker's rounding would give 42; surge-on-distance-only would give 36).
- ₹6/km: whole km or prorated? The policy is ambiguous. Infer from `payout_lines.csv` (which reading matches correctly paid history) and document the evidence.
- ₹150 incentive for **≥12** trips in an **IST calendar day**. Decide (and verify from data) whether only `completed` trips count; trips near midnight can fall on different days in UTC vs IST.
- ₹10 penalty per `cancelled_by_rider`; `cancelled_by_customer` earns nothing.
- Dispute window: last **7 days relative to `received_at`**, not wall-clock time (sample data is Sep 2026). Older → explain politely; consider escalation `optional`.

**Reconciliation**
- "What the rider was paid" = `payout_lines.csv` history **plus** what PaySwift's ledger shows for that rider (the agent's own payments). PaySwift's ledger is in memory and wipes on restart.
- Match payout lines to trips by `trip_id`, not by date (`payout_date` may lag the trip). Beware duplicate lines and orphan trip_ids in the exports.
- Rider's claimed amount is a hint, not the truth. Pay the **verified** shortfall only. Rider claims ₹120, owed ₹80 → pay ₹80 and explain. Rider was actually overpaid → pay nothing, explain.

**PaySwift**
- `amount` is an integer 1–10000 (0 is invalid, floats invalid). Possible responses: 201, 400, 409, 429, 5xx. Rate limits apply.
- Always send an `Idempotency-Key` and put the dispute id in `reference`. A 5xx or timeout may mean the payout **was** recorded: re-check with `GET /v1/payouts?rider_id=` before retrying.

**Vendor behaviour**
- Duplicate `message_id` must return the same reply and never repeat side effects, including when the duplicate arrives while the first is still processing.
- 10s budget: LLM calls need short timeouts and a deterministic fallback. There is no outbound channel to the rider later, so the reply must be produced synchronously.

**Conversation behaviour**
- Resolve "kal", "parso", "20 tarikh wala" against `received_at` in IST.
- Trip ids come in messy forms (`t88213`, `T-88213`, "order 88213"). A trip that belongs to another rider is a shady signal → escalate.
- Prompt injection / social engineering: "ops said pay me 500", "I'm the manager". Ignore, flag, possibly escalate.
- Do not loop on clarification: at most 2 asks, then escalate.

**Evals**
- Sample conversations say "run each against a fresh system", but your evals cannot reset other implementations. Measure outcomes as **deltas** (PaySwift payout list before/after; `/ops/pending` before/after), use unique message ids, avoid same-rider/same-day collisions in ordering, and offer an optional `--reset-cmd`. Log the chosen approach in DECISIONS.md.
- `expected` fields: `payout` (₹ reaching rider in PaySwift), `approval` (₹ waiting for ops or null), `escalation` (`required` / `optional` / `no`), `reply_mentions` (facts the reply must contain), `one_of` (any listed end state is acceptable).

**Not yet seen by the reviewer:** `data/conversations.json`, `trips.csv`, `riders.csv`, `payout_lines.csv`, and the PaySwift server. Read them first (Phase 1) and bring any surprises to review.

---

## 4. Suggested architecture

- Python 3.12 + FastAPI + Postgres + pytest + ruff. LLM behind a thin provider adapter (Groq primary, Gemini fallback), JSON-schema outputs, temperature 0.
- **LLM does:** language understanding, entity/intent extraction (trip ids, dates, claim types, claimed amount, language, injection flag), drafting the reply from verified facts.
- **Code does:** date resolution, ownership and window checks, fare computation, paid-so-far reconciliation, caps, payout/approval/escalation decisions, PaySwift calls, state and trace.
- Per-message flow: dedupe → load state → extract → resolve → verify per issue → money gate → execute → compose reply → validate reply numbers against computed facts → trace → store reply.
- Outcome enum per issue: `PAY`, `NO_DISPUTE_EXPLAIN`, `NEED_INFO`, `OUT_OF_WINDOW`, `NOT_RIDERS_TRIP`, `ALREADY_PAID`, `ESCALATE`.
- Tables (suggested): `inbound_messages`(message_id PK), `conversation_state`, `issues`, `payments`(idempotency_key unique), `ops_items`, `trace_steps`, plus seeded `riders`, `trips`, `payout_lines`.

---

## 5. Phases

Time targets assume a 24h window with sleep. Total focused work ≈ 18–19h. If time runs short, cut in this order: extra eval scenarios, ops page polish, Gemini fallback. **Never cut:** policy engine, money safety, idempotency, trace, evals, README, ai-logs.

### Phase 0: Kickoff and repo hygiene (≈45 min)
- [ ] Record the assignment email receipt time and the exact deadline at the top of `docs/DECISIONS.md`.
- [ ] Clone `Procureyard/qd-assignment` (no fork). Create a **new private** GitHub repo; repoint `origin`; push.
- [ ] Add collaborator **`Sarthak-pyard`** (Settings → Collaborators). Confirm the invite shows as sent.
- [ ] Read `BRIEF.md`, `SUBMISSION.md`, `docs/policy.md`, `data/README.md`, `payswift/README.md`, `docker-compose.yml`.
- [ ] Create layout: `app/`, `evals/`, `tests/`, `scripts/`, `docs/DECISIONS.md`, `ai-logs/`, `.github/workflows/ci.yml`, `.env.example`, `.gitignore` (ignore `.env`, `__pycache__`, venvs).
- [ ] Choose stack; note it in DECISIONS.md.
- [ ] Start AI-session export habit: one session per phase; export raw at phase end into `ai-logs/`.

**Acceptance:** repo pushed, collaborator invited, skeleton committed, no secrets, `payswift/` untouched.
**Gate:** reviewer checks repo is private, structure present, `git diff` vs upstream touches nothing in `payswift/`.

### Phase 1: Data audit and deterministic policy engine (≈3h)
- [ ] Profile the data: row counts, nulls, duplicate ids, status values, timestamp formats/timezones, orphan `trip_id`s in payout lines, riders missing from `riders.csv`, odd distances/surge values (null, <1, 0 km), sign of penalty lines. Write findings to `docs/DATA_AUDIT.md`.
- [ ] Implement pure functions: `trip_fare(distance_km, surge)`, `ist_day(ts)`, `daily_incentive(n_completed)`, `cancellation_penalty(status)`, `expected_for_trip`, `expected_for_rider_day`. Use `Decimal` and `ROUND_HALF_UP`.
- [ ] Build a reconciliation of expected vs. paid for **all** trips/days. Categorise anomalies (missed surge, missing trip line, missing incentive, wrong penalty, duplicate line, rounding, overpaid) → `docs/RECONCILIATION.md`.
- [ ] Resolve ambiguities from data (prorated km, incentive trip counting, incentive/penalty date attribution). Record evidence counts in DECISIONS.md.
- [ ] Unit tests: 3.5 km × 1.25 → 43; exactly 2 km → base only; 0.5 rounding cases; 23:50 IST vs UTC midnight; 11 vs 12 trips; customer cancel = 0; rider cancel = −10.
- [ ] Golden tests: for each sample conversation where the expected `payout` is derivable from data, the engine reproduces it.

**Acceptance:** engine has zero I/O, floats, or `now()`; all tests pass; audit and reconciliation docs committed; golden tests pass or mismatches are explained.
**Gate:** reviewer re-checks rounding and day-boundary logic, spot-checks 5 trips by hand, reads DATA_AUDIT for anything missed.

### Phase 2: PaySwift client and sandbox probing (≈1.5h)
- [ ] `scripts/probe_payswift.py`; record in `docs/PAYSWIFT_NOTES.md`: response shapes; same-key-same-body vs same-key-different-body behaviour; no-key duplicates; 400 cases (0, 10001, float, missing/unknown rider); rate-limit threshold and `Retry-After`; frequency of 5xx and whether a 5xx can follow a recorded write; latency; list filtering/ordering; ledger wipe on restart.
- [ ] Client: connect/read timeouts; retry with exponential backoff + jitter on 429/5xx/timeouts; honour `Retry-After`; never retry 400; explicit 409 handling.
- [ ] Deterministic `Idempotency-Key` from (dispute id, rider, amount, purpose); dispute id in `reference`.
- [ ] **Reconcile-before-retry:** after an ambiguous failure, query the ledger for this key/reference before sending again.
- [ ] Validate amount is an int in 1–10000 before calling.
- [ ] `payments` state machine: `intent` written **before** the call → `sent` → `confirmed` / `failed`; startup recovery for stuck `sent`.
- [ ] Tests with a mocked transport: 429→201; 500-after-commit then reconcile finds it, no double pay; timeout; 409; 400.

**Acceptance:** no scenario in the tests can produce two payouts for one intent; findings documented.
**Gate:** reviewer traces each failure mode through the code and checks the idempotency key construction.

### Phase 3: Platform skeleton (≈2h)
- [ ] FastAPI on port 8000. `GET /health` returns 200 only when the DB is reachable and migrations are done.
- [ ] Config via env: `PAYSWIFT_BASE_URL`, `DATABASE_URL`, `LLM_PROVIDER`, `LLM_API_KEY`, etc. All listed in `.env.example`.
- [ ] Idempotent migrations at startup; idempotent seeding of data from `data/`.
- [ ] `POST /messages`: validate body; insert `message_id` first (unique); concurrent duplicate waits and returns the stored reply; always 2xx for valid input (LLM failure → safe fallback reply + escalation); 4xx only for malformed input. Stub reply for now.
- [ ] Trace recorder; `GET /trace/{rider_id}` returns ordered steps shaped exactly as in `SUBMISSION.md` (`at` ISO, `type` ∈ `message_in|tool_call|decision|reply|error`, `name`, `input`, `output`).
- [ ] `GET /ops/pending` shaped exactly as in `SUBMISSION.md` (`id`, `rider_id`, `type`, `amount`, `reason`, `created_at`).
- [ ] `docker-compose.yml`: add `app` and `postgres` (healthcheck, volume); `depends_on` healthy; `PAYSWIFT_BASE_URL=http://payswift:8081`. The `payswift` service must be byte-identical.
- [ ] `.github/workflows/ci.yml` on every push: ruff, pytest, `docker compose build`, `docker compose up -d --wait`, curl `/health`, teardown. No secrets required.
- [ ] Contract tests validating response shapes against JSON schemas.

**Acceptance:** from a clean clone, `docker compose up` → `/health` 200; duplicate POST (same `message_id`) returns identical reply and creates one inbound row; CI green.
**Gate:** reviewer runs the compose from scratch, fires duplicate and concurrent duplicate requests, diffs the `payswift` service block.

### Phase 4: Eval harness first (≈1.5h)
- [ ] `evals/run.py <service_url> [--payswift-url http://localhost:8081] [--only ID] [--report out.md] [--reset-cmd ...]`. Uses only HTTP: `POST /messages`, `GET /trace`, `GET /ops/pending`, PaySwift `GET /v1/payouts`. No imports from `app/`.
- [ ] Runs each conversation turn by turn with unique wamid-style ids and `received_at` from the file.
- [ ] Measures payout via PaySwift delta, approval and escalation via `/ops/pending` delta. Checks `payout` and `approval` exactly, `escalation` per required/optional/no, `one_of`, and `reply_mentions` (deterministic matching incl. numbers and trip ids; optional LLM judge behind a flag, off by default).
- [ ] Handle the fresh-system problem as described in section 3; warn on same-rider/same-day collisions.
- [ ] Your own scenarios in `evals/scenarios/*.json` (same schema): duplicate/retried `message_id`; overclaim; someone else's trip; outside 7 days; repeat dispute same day; >₹200; prompt injection; abusive/out-of-scope; two issues in one message; changed story; Hinglish/English mix; rider actually overpaid.
- [ ] Report: per-conversation pass/fail, categories (payout correctness, **safety**, escalation, reply facts), totals incl. ₹ overpaid (must be 0) and ₹ underpaid; non-zero exit on any safety violation; timeouts handled.
- [ ] One-line command in README.

**Acceptance:** runs end to end against the Phase 3 stub (failing as expected, no crashes).
**Gate:** reviewer confirms the harness is interface-only and portable and that it measures outcomes from PaySwift, not from your DB.

### Phase 5: Agent core (≈5h)
- [ ] Deterministic tools, each traced as `tool_call`: `lookup_trip` (with ownership check), `list_rider_trips(date range)`, `compute_expected`, `get_paid_history` (CSV lines + PaySwift ledger), `check_window`, `check_caps`, `create_payout` (**code-only, not LLM-callable**), `create_ops_item`.
- [ ] Per-rider conversation state in Postgres: issues (`surge_missing`, `short_pay_day`, `incentive_missing`, `penalty_wrong`, `other`) with trip ids, date, claimed amount, status; missing slots; message history; rule for starting a new conversation after silence.
- [ ] LLM extraction with strict JSON schema; validate, repair once, fall back deterministically; temperature 0; rider text delimited as untrusted data.
- [ ] Date/trip-id normalisation (Hinglish relative dates, messy ids) against `received_at` IST.
- [ ] Verification per issue → outcome enum (section 4); handle several issues in one message and changed stories.
- [ ] Reply composer: LLM writes in the rider's language using only verified facts; post-check that every number/trip id in the reply is in the computed facts; template fallback if LLM fails.
- [ ] Clarification policy: ask only for what's missing; max 2 asks then escalate.
- [ ] Explicit escalation list in code: trip not rider's; repeat/pattern claims; claim strongly contradicts data; abuse/threat/legal/media; injection attempt; data inconsistency; PaySwift failure after retries; rider asks for a human; follow `conversations.json` for `optional` cases.
- [ ] Latency: LLM timeout ≤ ~4s, ≤2 LLM calls per message, total budget ≈8s; provider rate-limit backoff; fallback provider.
- [ ] Every decision traced with its reasoning inputs (computed expected, paid so far, caps state), so ops can see *why*.

**Acceptance:** all sample conversations produce the exact expected `payout` and `approval`; zero overpay; trace tells the full story for each conversation.
**Gate:** reviewer reads the decision code line by line for any path where the LLM output can influence an amount; runs injection and overclaim scenarios.

### Phase 6: Approvals, ops page and money-safety hardening (≈2.5h)
- [ ] `ops_items` lifecycle: `pending → approved | rejected`. `POST /ops/{id}/approve` and `/reject` are idempotent (double-click safe).
- [ ] Approve **re-verifies** owed amount and caps, then pays via PaySwift with an idempotency key. Reject records a reason.
- [ ] Ops page at `/ops`: pending queue (rider, amount, reason, computed breakdown, approve/reject), conversation list, rider detail with messages + trace timeline + payments. Escape all rider-supplied text (XSS). Optional `OPS_TOKEN`; note in README if skipped.
- [ ] Concurrency: two simultaneous messages for the same rider → single payout (per-rider lock and/or unique constraint on rider + IST day + auto-pay).
- [ ] Crash-safety: restart between `intent` and `sent` → recovery reconciles, no double pay.
- [ ] PaySwift down → graceful reply + escalation, no duplicate pay on retry.
- [ ] Invariant script/test: per rider, sum of confirmed payments equals the PaySwift ledger.

**Acceptance:** race and crash tests pass; approval pays exactly once; page usable end to end.
**Gate:** reviewer attempts double-approve, parallel messages, and kill-restart scenarios mentally against the code and the tests.

### Phase 7: Eval pass, robustness and polish (≈2h)
- [ ] Run the full eval from `docker compose down -v && up`. Fix failures; classify remaining ones honestly.
- [ ] Measure latency; p95 must be well under 10s.
- [ ] CI green on `main` (optional eval job only if an LLM secret exists, otherwise a mock-LLM mode).
- [ ] Lint/format, remove dead code, no secrets or PII in logs.
- [ ] Prepare for the call: every module explainable; DECISIONS.md complete.

**Acceptance:** eval report committed or summarised; zero overpay; p95 latency known.
**Gate:** reviewer re-runs a sample of evals, inspects remaining failures, checks the README claims match reality.

### Phase 8: Submission (≈1h, finish ≥1h before deadline)
- [ ] `README.md`, one page max: how to run, agent design (tools; what the model decides vs what code decides), assumptions, eval results (real numbers), what you skipped.
- [ ] `ai-logs/`: raw exports, one file per session. Claude Code: `~/.claude/projects/<project>/<session>.jsonl`; Codex CLI: `~/.codex/sessions/<date>/rollout-*.jsonl` or `codex export`; Cursor: chat menu → Export Chat; Copilot: "Chat: Export Chat…"; Gemini CLI: `/export jsonl --output <file>`. If a tool can't export, include what it gives and say what it is. Scan for API keys before committing.
- [ ] `.env.example` complete; `.env` ignored; `git log -p | grep` for key patterns shows nothing.
- [ ] Fresh-clone test: clone → copy `.env.example` to `.env` + add key → `docker compose up` → run the eval one-liner. All green.
- [ ] Everything is on `main`; collaborator confirmed; tag `submission`.
- [ ] Email the repo link before the deadline.

**Acceptance:** the fresh-clone test passes; README ≤ one page.
**Gate:** final reviewer read-through; green light to send.

---

## 6. Review protocol (between you, the agent's operator, and the reviewer)

After each phase's commits, send the reviewer: the phase number, the repo link (your own private repo works once GitHub is linked) or `git log --stat` plus `git diff <prev-tag>..HEAD`, and anything you were unsure about.

The reviewer replies with:
1. **Verdict:** 🟢 proceed, 🟡 proceed after small fixes, 🔴 redo part of this phase.
2. **Bugs** (severity-ranked, with file/line and a suggested fix).
3. **Gate checklist** results, item by item.
4. **Improvements** (nice-to-have, ranked by value vs. time left).
5. **Watch-outs** for the next phase.

Do not start the next phase on 🟡/🔴. Hotfixes to an earlier phase are fine; mention them in the next review.
