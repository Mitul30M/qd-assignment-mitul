"""Conversation flow: one rider message -> verified outcomes + reply (deterministic).

Stage functions double as LangGraph nodes; the LLM only parses text, and its
output is validated/ignored. Money decisions come from claims.py + payments.py.
Each extracted claim is verified and acted on in order; the once-per-rider-day
auto cap and the repeat-dispute guard make double payment impossible.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import uuid
from decimal import Decimal

from app.config import settings
from app.db import trace
from app.payswift import ClientConfig, PaySwiftClient, idempotency_key

from . import issues, replies, tools

_INJECT_RE = re.compile(
    r"ignore (all )?(previous|prior) (rules|instructions|prompts)|SYSTEM:", re.IGNORECASE)

# Rider pushes back / asks for status on an existing issue, adding no new evidence.
_PUSHBACK_RE = re.compile(
    r"\b(dobara|phir se|fir se|recheck|check karo|abhi de do|kab tak|kab aayega|"
    r"thik hai|theek hai)\b", re.IGNORECASE)

# Identity-binding must not depend on the model: direct rider-id claims in text.
_IDENTITY_RE = re.compile(r"\b(?:i am|this is|mera id|main hoon|main)\s+(R\d{1,3})\b",
                          re.IGNORECASE)

EMPTY_CLAIM = {"kind": "other", "trip_ids": [], "days": [], "claimed_amount": None}


def handle_message(conn, rider_id: str, message_id: str, text: str, received_at: str) -> str:
    """One full turn: run the LangGraph flow with a fresh (checkpoint-less) graph.

    main.py builds ONE graph with a PostgresSaver checkpointer and calls
    handle_message_graph instead; this entry keeps tests and scripts simple.
    """
    from .graph import build_graph, handle_message_graph

    return handle_message_graph(conn, build_graph(conn), rider_id, message_id,
                                text, received_at)


def _handle_claim_verdict(conn, rider_id, received_at, received_day, convo, claim, paid_today) \
        -> tuple[str, str, bool]:
    """Verify + act for one claim; returns (reply_segment, outcome, did_pay)."""
    kind = claim["kind"]
    trip_ids, days = [], list(dict.fromkeys(claim["days"]))[:1]

    foreign = None
    for tid in claim["trip_ids"]:
        info = tools.lookup_trip(conn, rider_id, tid, received_at)
        if info.get("owned_by_rider") is False:
            foreign = info
            break
        if info.get("owned_by_rider"):
            trip_ids.append(tid)
    if foreign:
        _record_ops(conn, rider_id, "escalation", None,
                    f"{rider_id} disputes trip {foreign['trip_id']} owned by {foreign['owner']}",
                    category="identity")
        return replies.identity_bound(), "NOT_RIDERS_TRIP", False

    out_of_window = [d for d in days if not _window_ok(d, received_at)]
    if trip_ids and not days:
        from app.policy import trip_ist_day

        trips_map = {t.trip_id: t for t in tools.trips_for(conn, rider_id)}
        out_of_window = [d for t in trip_ids if (d := trip_ist_day(trips_map[t]).isoformat())
                         and not _window_ok(d, received_at)]
    if out_of_window:
        # a day the rider has no rows for is a model hallucination, not a dispute:
        # drop it silently (real out-of-window claims always have evidence)
        evidence_days = [d for d in out_of_window
                         if tools.list_rider_trips(conn, rider_id, d, received_at)]
        if evidence_days:
            issue_id = issues.upsert_issue(conn, convo["conversation_id"], rider_id, kind,
                                           evidence_days[0], [], claim.get("claimed_amount"),
                                           [])
            issues.set_outcome(conn, issue_id, "OUT_OF_WINDOW", "resolved")
            _record_ops(conn, rider_id, "escalation", None,
                        f"out-of-window dispute {issue_id} (informational)",
                        category="out_of_window", issue_id=issue_id)
            return replies.out_of_window(evidence_days[0]), "OUT_OF_WINDOW", False
        trace(conn, rider_id, "decision", "drop_unverifiable_day",
              {"out_of_window": out_of_window}, {"note": "no rider rows for that day"})
        return "", "NO_CLAIM", False

    if not trip_ids and not days:
        trace(conn, rider_id, "decision", "missing_claim_evidence",
              {"kind": kind, "claimed_amount": claim.get("claimed_amount")},
              {"outcome": "NEED_INFO"})
        return replies.need_info(True), "NEED_INFO", False

    facts: dict = {"day": days[0] if days else None, "day_details": {},
                   "trip_shortfalls": {}}
    for day in days:
        _day_facts(conn, rider_id, day, received_at, facts)

    inc_days = _incentive_days(conn, rider_id, days) if kind == "incentive" else None
    verdict = tools.verify(conn, rider_id, trip_ids, days, received_at, inc_days=inc_days)
    facts["verified_amount"] = verdict["verified_amount"]
    facts["route"] = verdict["route"]
    facts["route_reason"] = verdict["reason"]
    for key, val in verdict["shortfalls"].items():
        if key.startswith("incentive:"):
            facts["incentive_shortfall"] = int(val)
        else:
            facts["trip_shortfalls"][key] = int(val)

    outcome = _decide_outcome(claim, verdict)
    issue_id = issues.upsert_issue(conn, convo["conversation_id"], rider_id, kind,
                                   days[0] if days else None, trip_ids,
                                   claim.get("claimed_amount"), [])
    trace(conn, rider_id, "decision", "outcome", {"issue_id": issue_id},
          {"outcome": outcome, "kind": kind, "days": days, "trips": trip_ids})
    segment, did_pay = _act(conn, rider_id, outcome, verdict, facts, claim, issue_id,
                            days, received_day, received_at)
    return segment, outcome, did_pay


def _no_claim_reply(conn, rider_id: str, received_day) -> tuple[str, str]:
    """Vague message or status query: ask once; escalate after the ask limit."""
    from app.payments import list_for_rider_day

    paid = [p for p in list_for_rider_day(conn, rider_id, received_day.isoformat())
            if p["state"] == "confirmed"]
    if paid:
        amount = sum(p["amount"] for p in paid)
        trace(conn, rider_id, "decision", "status_query", {"day": received_day.isoformat()},
              {"amount": amount})
        return replies.status_update(amount), "STATUS", False
    convo = issues.current_conversation(conn, rider_id, received_day)
    issue_id = issues.upsert_issue(conn, convo["conversation_id"], rider_id, "other",
                                   None, [], None, [])
    asks = issues.ask_count_of(conn, issue_id)
    if asks < 1:
        issues.increment_ask(conn, issue_id)
        trace(conn, rider_id, "decision", "need_info", {"issue_id": issue_id},
              {"ask": asks + 1, "policy": "one clarifying ask, then route out"})
        return replies.need_info(True), "NEED_INFO", False
    _record_ops(conn, rider_id, "escalation", None,
                "unverifiable: clarification beyond first ask", category="unverifiable",
                issue_id=issue_id)
    trace(conn, rider_id, "decision", "escalate", {"issue_id": issue_id},
          {"reason": "clarify limit"})
    issues.set_outcome(conn, issue_id, "ESC", "open")
    return replies.escalation("unverifiable"), "ESCALATE", False


# ---------------- stages ----------------


def _day_facts(conn, rider_id: str, day: str, received_at: str, facts: dict) -> None:
    rows = tools.list_rider_trips(conn, rider_id, day, received_at)
    completed = [r for r in rows if r["status"] == "completed"]
    expected = sum(Decimal(r["expected"]) for r in completed)
    paid = sum(Decimal(r["paid"]) for r in rows)
    facts["day_details"][day] = {
        "completed": len(completed), "expected": expected, "paid": paid,
        "gap": max(Decimal(0), expected - paid),
        "trip_ids": [r["trip_id"] for r in rows],
    }


def _incentive_days(conn, rider_id: str, days: list[str]) -> dict[str, int]:
    """Count completed trips per IST day; only days with >=12 qualify for the ₹150."""
    if not days:
        return {}
    from app.policy import trip_ist_day

    counts: dict[str, int] = {}
    for trip in tools.trips_for(conn, rider_id):
        if trip.status != "completed":
            continue
        d = trip_ist_day(trip).isoformat()
        counts[d] = counts.get(d, 0) + 1
    return {d: counts[d] for d in days if counts.get(d, 0) >= 12}


def _window_ok(day: str, received_at: str) -> bool:
    from app.policy import parse_received_at, within_window

    return within_window(parse_received_at(received_at),
                         datetime.date.fromisoformat(day))


def _last_issue(conn, rider_id: str, conversation_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM issues WHERE rider_id = %s AND conversation_id = %s "
        "ORDER BY updated_at DESC LIMIT 1",
        [rider_id, conversation_id],
    ).fetchone()
    return dict(row) if row else None


def _decide_outcome(claim, verdict) -> str:
    state = verdict["state"]
    if state == "unknown_rider":
        return "ESCALATE"
    if state == "cannot_verify":
        return "NEED_INFO"
    if state == "verified" and verdict["verified_amount"] > 0:
        return "PAY-DECIDED"
    if state in ("no_shortfall", "verified"):  # verified-with-zero == records full
        return "NO_DISPUTE_EXPLAIN"
    return "NEED_INFO"


def _act(conn, rider_id, outcome, verdict, facts, claim, issue_id, days, received_day,
         received_at) -> tuple[str, bool]:
    if outcome == "PAY-DECIDED":
        from app.payments import list_for_rider_day, send_payout

        # repeat-dispute guard: subtract shortfalls already covered by confirmed
        # payments (any day) — the verified shortfall is never paid twice.
        covered: dict[str, int] = {}
        prior = conn.execute(
            "SELECT details FROM payments WHERE rider_id = %s AND state = 'confirmed'",
            [rider_id],
        ).fetchall()
        for row in prior:
            for key, val in ((row["details"] or {}).get("shortfalls") or {}).items():
                covered[key] = covered.get(key, 0) + int(val)
        effective: dict[str, int] = {}
        for key, val in verdict["shortfalls"].items():
            remainder = max(0, int(val) - covered.get(key, 0))
            if remainder:
                effective[key] = remainder
        amount = sum(effective.values())
        claim_ctx = {"kind": claim["kind"], "days": days, "trip_ids": claim.get("trip_ids") or [],
                     "received_at": received_at, "shortfalls": effective}
        if not amount:
            trace(conn, rider_id, "decision", "repeat_dispute_guard",
                  {"covered": covered}, {"outcome": "ALREADY_PAID"})
            issues.set_outcome(conn, issue_id, "ALREADY_PAID", "resolved")
            return replies.repeat_explained(
                sum(covered.values()) or int(verdict["verified_amount"]), facts), False

        dispute_id = deterministic_dispute_id(rider_id, claim["kind"], days)
        caps_paid = list_for_rider_day(conn, rider_id, received_day.isoformat())
        pending = [p for p in caps_paid if p["state"] in ("intent", "sent", "confirmed")]
        if verdict["route"] == "auto" and not pending:
            client = PaySwiftClient(ClientConfig(base_url=settings.payswift_base_url))
            result = send_payout(conn, client, rider_id, dispute_id, amount, "auto",
                                 idempotency_key(dispute_id, rider_id, amount, "auto"),
                                 claim=effective, ist_day=received_day.isoformat())
            if result["state"] == "confirmed":
                trace(conn, rider_id, "decision", "payout_sent",
                      {"dispute_id": dispute_id, "amount": amount}, result)
                issues.set_outcome(conn, issue_id, "PAY", "resolved")
                return replies.pay_confirmed(amount, "auto", facts), True
            trace(conn, rider_id, "decision", "payout_pending",
                  {"dispute_id": dispute_id, "amount": amount}, result)
            _record_ops(conn, rider_id, "approval", amount,
                        f"payout {result['state']}: "
                        f"{(result.get('error') or '')[:120]}", dispute_id,
                        category="provider_unconfirmed", claim=claim_ctx, issue_id=issue_id)
            issues.set_outcome(conn, issue_id, "PAY", "pending_approval")
            return replies.approval_logged(
                amount, "PaySwift se confirm nahi hua; ops approval ke baad jayega", facts), False
        reason = verdict["reason"] or "routes outside auto at the moment"
        category = "over_cap" if "exceeds" in (verdict["reason"] or "") else "already_autopaid"
        _record_ops(conn, rider_id, "approval", amount, reason, dispute_id,
                    category=category, claim=claim_ctx, issue_id=issue_id)
        trace(conn, rider_id, "decision", "approval_logged",
              {"dispute_id": dispute_id, "amount": amount},
              {"reason": reason, "route": verdict["route"]})
        issues.set_outcome(conn, issue_id, "PAY", "pending_approval")
        return replies.approval_logged(amount, reason, facts), False

    if outcome == "NO_DISPUTE_EXPLAIN":
        reply = _explain(claim, facts, days)
        issues.set_outcome(conn, issue_id, "NO_DISPUTE_EXPLAIN", "resolved")
        return reply, False

    if outcome == "OUT_OF_WINDOW":
        return replies.out_of_window(days[0] if days else ""), False

    if outcome == "ESCALATE":
        _record_ops(conn, rider_id, "escalation", None,
                    claim.get("tone_flag") or claim["kind"] or "unverifiable",
                    category="tone" if claim.get("tone_flag") else "unverifiable",
                    issue_id=issue_id)
        issues.set_outcome(conn, issue_id, "ESC", "open")
        return replies.escalation("human"), False

    # NEED_INFO within a claim: ask once, then route out
    asks = issues.ask_count_of(conn, issue_id)
    if asks < 1:
        issues.increment_ask(conn, issue_id)
        trace(conn, rider_id, "decision", "need_info", {"issue_id": issue_id},
              {"ask": asks + 1})
        return replies.need_info(True), False
    _record_ops(conn, rider_id, "escalation", None,
                "unverifiable: clarification beyond first ask", category="unverifiable",
                issue_id=issue_id)
    trace(conn, rider_id, "decision", "escalate", {"issue_id": issue_id},
          {"reason": "clarify limit"})
    issues.set_outcome(conn, issue_id, "ESC", "open")
    return replies.escalation("unverifiable"), False


def _explain(claim, facts, days) -> str:
    day = days[0] if days else None
    if claim["kind"] == "incentive" and day:
        d = facts["day_details"].get(day)
        return replies.incentive_not_earned(day, d["completed"] if d else 0)
    tid = claim["trip_ids"][0] if claim["trip_ids"] else None
    if tid:
        dd = facts["day_details"].get(day)
        if dd and tid in dd["trip_ids"] and dd["paid"] >= dd["expected"]:
            return replies.already_paid({"trip_id": tid, "expected": dd["expected"],
                                         "paid": dd["paid"]})
        return ("Check kiya: us order ke records ke mutabik payment me koi farak "
                "nahi mila.")
    return ("Check kiya: us din ke records ke mutabik aapko poora payment mila hai "
            "(payment me farak nahi mila).")


def deterministic_dispute_id(rider_id: str, kind: str, days: list[str]) -> str:
    digest = hashlib.sha1(
        "|".join([rider_id, kind, ",".join(sorted(days))]).encode()).hexdigest()[:10]
    return f"dsp_{digest}"


def _record_ops(conn, rider_id: str, type_: str, amount: int | None, reason: str,
                dispute_id: str | None = None, category: str | None = None,
                claim: dict | None = None, issue_id: str | None = None) -> str:
    ops_id = f"ops_{dispute_id}" if dispute_id else f"ops_{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO ops_items (id, rider_id, type, amount, reason, category, claim, "
        "issue_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO UPDATE SET reason = EXCLUDED.reason",
        [ops_id, rider_id, type_, amount, reason, category,
         json.dumps(claim) if claim else None, issue_id],
    )
    return ops_id


def _finish(conn, rider_id, message_id, reply, outcome, note) -> str:
    trace(conn, rider_id, "reply", "template", {"outcome": outcome, "note": note}, reply)
    conn.execute(
        "UPDATE inbound_messages SET reply = %s, reply_at = now(), ok = true "
        "WHERE message_id = %s",
        [reply, message_id],
    )
    return reply


def _clean(triage: dict) -> dict:
    return {k: v for k, v in triage.items() if v is not None}
