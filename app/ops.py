"""Ops-console actions: approve / reject pending items.

Human approval is an authorization TRIGGER, never an amount authority: approve
re-verifies the owed amount from source rows (the same verify path the agent used,
plus the coverage guard) before paying, with the same deterministic dispute
idempotency key — so double-clicks, concurrent approvers, and provider retries can
never pay twice. The status flip is CAS on `status='pending'`; the payout itself
dedupes on the dispute key, so a lost CAS race still cannot double-pay.
"""

from __future__ import annotations

import datetime

from app import notices
from app.agent import issues, tools
from app.agent.flow import _incentive_days, deterministic_dispute_id
from app.config import settings
from app.db import trace
from app.payswift import ClientConfig, PaySwiftClient, idempotency_key

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _item(conn, ops_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM ops_items WHERE id = %s", [ops_id]).fetchone()
    return dict(row) if row else None


def _cas_resolve(conn, ops_id: str, status: str, note: str) -> bool:
    cur = conn.execute(
        "UPDATE ops_items SET status = %s, resolved_at = now(), resolution_note = %s "
        "WHERE id = %s AND status = 'pending'",
        [status, note, ops_id],
    )
    return cur.rowcount == 1


def _reverify(conn, item: dict) -> dict:
    """Recompute what is owed NOW from source rows (never trust the queue amount)."""
    ctx = item["claim"] or {}
    days = [str(d) for d in (ctx.get("days") or [])]
    received_at = ctx.get("received_at") or datetime.datetime.now(IST_TZ).isoformat()
    inc_days = None
    if ctx.get("kind") == "incentive" and days:
        inc_days = _incentive_days(conn, item["rider_id"], days)
    verdict = tools.verify(conn, item["rider_id"], ctx.get("trip_ids") or [], days,
                           received_at, inc_days=inc_days)
    covered: dict[str, int] = {}
    prior = conn.execute(
        "SELECT details FROM payments WHERE rider_id = %s AND state = 'confirmed'",
        [item["rider_id"]],
    ).fetchall()
    for row in prior:
        for key, val in ((row["details"] or {}).get("shortfalls") or {}).items():
            covered[key] = covered.get(key, 0) + int(val)
    effective: dict[str, int] = {}
    for key, val in verdict["shortfalls"].items():
        remainder = max(0, int(val) - covered.get(key, 0))
        if remainder:
            effective[key] = remainder
    return {"verdict": verdict, "effective": effective, "ctx": ctx}


def approve_item(conn, ops_id: str, client: PaySwiftClient | None = None) -> dict:
    item = _item(conn, ops_id)
    if item is None:
        return {"state": "not_found"}
    if item["type"] != "approval" or item["claim"] is None:
        return {"state": "not_approvable",
                "reason": f"type={item['type']}, no stored claim context"}
    try:
        detail = _reverify(conn, item)
    except Exception as exc:  # item stays pending; ops sees the failure and can retry
        conn.execute("UPDATE ops_items SET resolution_note = %s WHERE id = %s",
                     [f"re-verify failed: {str(exc)[:200]}", ops_id])
        return {"state": "reverify_failed", "error": str(exc)[:200]}
    owed = sum(detail["effective"].values())
    if owed <= 0:
        # CAS guards against a concurrent resolver; nothing owed either way.
        if not _cas_resolve(conn, ops_id, "approved",
                            f"re-verified: nothing owed ({detail['verdict']['reason']})"):
            return {"state": "already_resolved", "status": _item(conn, ops_id)["status"]}
        trace(conn, item["rider_id"], "decision", "ops_approve", {"ops_id": ops_id},
              {"owed": 0, "outcome": "NOTHING_OWED"})
        notices.queue_notice(conn, item["rider_id"], ops_id, "approved",
                             category="already_autopaid", amount=0)
        if item["issue_id"]:
            issues.set_outcome(conn, item["issue_id"], "ALREADY_PAID", "resolved")
        return {"state": "approved", "paid": 0}

    ctx = detail["ctx"]
    dispute_id = deterministic_dispute_id(item["rider_id"], ctx["kind"], ctx["days"])
    if client is None:
        client = PaySwiftClient(ClientConfig(base_url=settings.payswift_base_url))
    from app.payments import send_payout

    result = send_payout(conn, client, item["rider_id"], dispute_id, owed, "approval",
                         idempotency_key(dispute_id, item["rider_id"], owed, "approval"),
                         claim=detail["effective"],
                         ist_day=datetime.datetime.now(IST_TZ).date().isoformat())
    trace(conn, item["rider_id"], "decision", "ops_approve", {"ops_id": ops_id},
          {"owed": owed, "payment_state": result["state"]})
    if result["state"] != "confirmed":
        conn.execute("UPDATE ops_items SET resolution_note = %s WHERE id = %s",
                     [f"payment {result['state']}: {(result.get('error') or 'unconfirmed')[:150]}",
                      ops_id])
        return {"state": f"payment_{result['state']}", "amount": owed}
    if not _cas_resolve(conn, ops_id, "approved",
                        f"approved and paid ₹{owed} ({result['payment_id']})"):
        return {"state": "already_resolved", "status": _item(conn, ops_id)["status"]}
    notices.queue_notice(conn, item["rider_id"], ops_id, "approved", amount=owed)
    if item["issue_id"]:
        issues.set_outcome(conn, item["issue_id"], "PAY", "resolved")
    return {"state": "approved", "paid": owed, "payment_id": result["payment_id"]}


def reject_item(conn, ops_id: str, reason: str) -> dict:
    item = _item(conn, ops_id)
    if item is None:
        return {"state": "not_found"}
    if not _cas_resolve(conn, ops_id, "rejected", reason[:500]):
        return {"state": "already_resolved", "status": _item(conn, ops_id)["status"]}
    trace(conn, item["rider_id"], "decision", "ops_reject", {"ops_id": ops_id},
          {"category": item["category"]})
    notices.queue_notice(conn, item["rider_id"], ops_id, "rejected",
                         category=item["category"])
    if item["issue_id"]:
        issues.set_outcome(conn, item["issue_id"], "REJECTED", "resolved")
    return {"state": "rejected"}
