"""Notices queue: approve/reject decisions reach the rider on their NEXT contact.

The fixed interface contract has no outbound push — the only rider-visible channel
is the `reply` returned on POST /messages. So an ops decision is stored as an
undelivered notice and delivered (exactly once, atomically with the reply store)
as the first segment of the rider's next reply. If the rider never messages again,
the ops page shows the notice as still pending delivery.
"""

from __future__ import annotations

import uuid


def queue_notice(conn, rider_id: str, ops_item_id: str, decision: str,
                 category: str | None = None, amount: int | None = None) -> str:
    notice_id = f"ntc_{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO notices (notice_id, rider_id, ops_item_id, decision, category, amount) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        [notice_id, rider_id, ops_item_id, decision, category, amount],
    )
    return notice_id


def pending_notices(conn, rider_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM notices WHERE rider_id = %s AND delivered_at IS NULL "
        "ORDER BY created_at",
        [rider_id],
    ).fetchall()
    return [dict(r) for r in rows]


def mark_delivered(conn, notice_id: str) -> None:
    conn.execute(
        "UPDATE notices SET delivered_at = now() WHERE notice_id = %s "
        "AND delivered_at IS NULL",
        [notice_id],
    )


def undelivered_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM notices WHERE delivered_at IS NULL"
    ).fetchone()["c"]
