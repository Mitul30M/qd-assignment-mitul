"""Payment state machine — intent is persisted BEFORE any wire call.

Payment row states:
  intent    — persisted before the PaySwift call (exists even if we crash first)
  sent      — request is on the wire
  confirmed — PaySwift answered 201 (or ledger reconciliation proved the write)
  failed    — provably no money moved (reconciled: absent from ledger)

Recovery: stuck `sent` rows (crash mid-call) are reconciled from PaySwift's ledger
at startup — reconcile-before-retry, never a blind double-send.
"""

from __future__ import annotations

import json
import uuid

import psycopg

from .payswift import PayoutResult, PaySwiftClient, PaySwiftError


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()


def persist_intent(conn, rider_id: str, amount: int, purpose: str, dispute_id: str,
                   idem_key: str, ist_day: str | None = None) -> dict:
    """State 1: write `intent` and its deterministic key before any API call."""
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO payments (payment_id, rider_id, dispute_id, amount, purpose, state,
                                 idempotency_key, reference, created_at, details, ist_day)
           VALUES (%s, %s, %s, %s, %s, 'intent', %s, %s, %s, %s, %s)""",
        [payment_id, rider_id, dispute_id, amount, purpose, idem_key, dispute_id, _now(),
         json.dumps({"purpose": purpose}), ist_day],
    )
    return {"payment_id": payment_id, "state": "intent", "idempotency_key": idem_key,
            "amount": amount}


def mark_sent(conn, payment_id: str) -> None:
    conn.execute(
        "UPDATE payments SET state='sent', updated_at=%s WHERE payment_id=%s AND state='intent'",
        [_now(), payment_id],
    )


def mark_confirmed(conn, payment_id: str, result: PayoutResult | None, payout_id: str | None) -> None:
    resolved_id = payout_id or (result.payout_id if result else None)
    conn.execute(
        """UPDATE payments
              SET state='confirmed', provider_payout_id=%s, confirmed_at=%s, updated_at=%s
            WHERE payment_id=%s""",
        [resolved_id, _now(), _now(), payment_id],
    )


def mark_failed(conn, payment_id: str, reason: str) -> None:
    conn.execute(
        "UPDATE payments SET state='failed', error=%s, updated_at=%s WHERE payment_id=%s",
        [reason, _now(), payment_id],
    )


def get(conn, payment_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM payments WHERE payment_id=%s", [payment_id]).fetchone()
    return dict(row) if row else None


def list_for_rider_day(conn, rider_id: str, ist_day: str) -> list[dict]:
    rows = conn.execute(
        """SELECT p.* FROM payments p
           WHERE p.rider_id=%s AND state <> 'failed'
             AND COALESCE(p.ist_day,
                 (p.created_at AT TIME ZONE 'Asia/Kolkata')::date)::text = %s""",
        [rider_id, ist_day],
    ).fetchall()
    return [dict(r) for r in rows]


def reconcile_stuck_sent(conn, client: PaySwiftClient) -> list[dict]:
    """Startup recovery: `sent` rows with unknown outcome are resolved by reconciling
    against PaySwift's ledger — never by a blind duplicate send."""
    rows = conn.execute("SELECT * FROM payments WHERE state='sent' ORDER BY created_at").fetchall()
    resolved: list[dict] = []
    by_rider: dict[str, list[dict]] = {}
    for row in rows:
        rider_id = row["rider_id"]
        if rider_id not in by_rider:
            by_rider[rider_id] = client.ledger_for(rider_id)
        ledger = by_rider[rider_id]
        matches = [p for p in ledger
                   if p.get("reference") == row.get("reference")]
        if matches:
            mark_confirmed(conn, row["payment_id"], None, matches[0].get("payout_id"))
            resolved.append({"payment_id": row["payment_id"], "state": "confirmed",
                             "payout_id": matches[0].get("payout_id")})
        else:
            mark_failed(conn, row["payment_id"], "reconciled: absent from payswift ledger")
            resolved.append({"payment_id": row["payment_id"], "state": "failed"})
    return resolved


def send_payout(conn, client: PaySwiftClient, rider_id: str, dispute_id: str, amount: int,
                purpose: str, idem_key: str, claim: dict | None = None,
                ist_day: str | None = None) -> dict:
    """Ordered flow: intent -> sent -> (confirmed | failed). On provider ambiguity the
    ledger is reconciled before any outcome is declared (no blind retry at this layer).
    `claim` (verified shortfall map) is stored on the row so repeats can be detected
    and the verified shortfall is never paid twice.

    Idempotent per dispute: an existing active payment for the same dispute key is
    returned as-is (concurrent approvals/messages converge on one payout)."""
    existing = conn.execute(
        "SELECT * FROM payments WHERE rider_id = %s AND dispute_id = %s "
        "AND state <> 'failed' ORDER BY created_at DESC LIMIT 1",
        [rider_id, dispute_id],
    ).fetchone()
    if existing:
        return dict(existing)
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"
    try:
        intent = persist_intent(conn, rider_id, amount, purpose, dispute_id, idem_key,
                                ist_day)
    except psycopg.errors.UniqueViolation:
        # lost a concurrent insert race (same idem key or one-auto-per-day cap):
        # re-read; if no active row for this dispute, the cap index blocked us.
        row = conn.execute(
            "SELECT * FROM payments WHERE rider_id = %s AND dispute_id = %s "
            "AND state <> 'failed' LIMIT 1", [rider_id, dispute_id],
        ).fetchone()
        if row:
            return dict(row)
        return {"payment_id": "", "state": "failed", "error": "auto cap raced",
                "amount": amount, "idempotency_key": idem_key}
    payment_id = intent["payment_id"]
    if claim:
        conn.execute(
            "UPDATE payments SET details = details || %s::jsonb WHERE payment_id = %s",
            [json.dumps({"shortfalls": claim}), payment_id],
        )
    mark_sent(conn, payment_id)
    try:
        result = client.create_payout(rider_id, amount, dispute_id, idem_key)
    except PaySwiftError as exc:
        # No answer is not proof of no write: reconcile, don't blindly resend.
        try:
            ledger = client.ledger_for(rider_id)
        except PaySwiftError as ledger_exc:
            mark_failed(conn, payment_id, f"payswift unreachable ({ledger_exc}); outcome unknown")
        else:
            if any(p.get("reference") == dispute_id for p in ledger):
                mark_confirmed(conn, payment_id, None,
                               next(p.get("payout_id") for p in ledger if p.get("reference") == dispute_id))
            else:
                mark_failed(conn, payment_id, f"reconciled absent: {exc}")
    else:
        mark_confirmed(conn, payment_id, result, result.payout_id)
    payment = get(conn, payment_id)
    assert payment is not None, "payment row vanished after send_payout"
    return payment
