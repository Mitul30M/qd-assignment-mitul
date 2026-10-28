"""Conversation + issue state in Postgres (authoritative for the flow code).

- conversations: one per rider "session"; silence rule closes the old one when the
  rider returns after SILENCE_DAYS and starts a fresh one.
- issues: discovered disputes, with kind, trip ids, IST day, claimed amount,
  missing slots, ask_count, outcome, status.
"""

from __future__ import annotations

import datetime
import json
import uuid

SILENCE_DAYS = 2  # documented rule: >2 days silence starts a new conversation

KINDS = ("surge", "short_pay_day", "incentive", "penalty", "other")


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def current_conversation(conn, rider_id: str, received_at_ist: datetime.date) -> dict:
    """Open the latest conversation for the rider (new one if silence exceeded)."""
    row = conn.execute(
        "SELECT * FROM conversations WHERE rider_id = %s AND open = true "
        "ORDER BY last_active DESC LIMIT 1",
        [rider_id],
    ).fetchone()
    received_ts = datetime.datetime.fromisoformat(
        f"{received_at_ist.isoformat()}T12:00:00+05:30")
    if row:
        age = received_ts - row["last_active"]
        if age <= datetime.timedelta(days=SILENCE_DAYS):
            conn.execute(
                "UPDATE conversations SET last_active = %s WHERE conversation_id = %s",
                [received_ts.isoformat(), row["conversation_id"]],
            )
            return {"conversation_id": row["conversation_id"], "continued": True}
        conn.execute(
            "UPDATE conversations SET open = false WHERE conversation_id = %s",
            [row["conversation_id"]],
        )
    cid = f"conv_{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO conversations (conversation_id, rider_id, started_at, last_active) "
        "VALUES (%s, %s, %s, %s)",
        [cid, rider_id, received_ts.isoformat(), received_ts.isoformat()],
    )
    return {"conversation_id": cid, "continued": False}


def open_issues(conn, rider_id: str, conversation_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM issues WHERE rider_id = %s AND conversation_id = %s "
        "AND status IN ('open', 'need_info', 'pending_approval')",
        [rider_id, conversation_id],
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_issue(conn, conversation_id: str, rider_id: str, kind: str, ist_day: str | None,
                 trip_ids: list[str], claimed_amount: int | None,
                 missing_slots: list[str]) -> str:
    """One open issue per (rider, kind, day) per conversation."""
    existing = conn.execute(
        "SELECT * FROM issues WHERE rider_id = %s AND conversation_id = %s AND kind = %s "
        "AND status <> 'resolved' ORDER BY created_at LIMIT 1",
        [rider_id, conversation_id, kind],
    ).fetchone()
    if existing:
        prev = existing["trip_ids"] or []
        if isinstance(prev, str):
            prev = json.loads(prev)
        merged_trips = list({*prev, *trip_ids})
        conn.execute(
            """UPDATE issues SET trip_ids=%s, ist_day=COALESCE(%s, ist_day),
               claimed_amount=COALESCE(%s, claimed_amount),
               missing_slots=%s, updated_at=now() WHERE issue_id=%s""",
            [json.dumps(merged_trips), ist_day, claimed_amount,
             json.dumps(missing_slots), existing["issue_id"]],
        )
        return existing["issue_id"]
    issue_id = f"iss_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO issues (issue_id, conversation_id, rider_id, kind, ist_day,
                               trip_ids, claimed_amount, missing_slots)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        [issue_id, conversation_id, rider_id, kind, ist_day, json.dumps(trip_ids),
         claimed_amount, json.dumps(missing_slots)],
    )
    return issue_id


def increment_ask(conn, issue_id: str) -> int:
    row = conn.execute(
        "UPDATE issues SET ask_count = ask_count + 1, status = 'need_info', "
        "updated_at = now() WHERE issue_id = %s RETURNING ask_count",
        [issue_id],
    ).fetchone()
    return row["ask_count"]


def ask_count_of(conn, issue_id: str) -> int:
    row = conn.execute("SELECT ask_count FROM issues WHERE issue_id = %s", [issue_id]).fetchone()
    return row["ask_count"] if row else 0


def set_outcome(conn, issue_id: str, outcome: str, status: str) -> None:
    conn.execute(
        "UPDATE issues SET outcome=%s, status=%s, updated_at=now() WHERE issue_id=%s",
        [outcome, status, issue_id],
    )
