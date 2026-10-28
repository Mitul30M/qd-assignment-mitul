"""FastAPI service: fixed contracts POST /messages, GET /trace, GET /ops/pending, GET /health."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import db
from app.config import settings


class IncomingMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=256)
    rider_id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=10_000)
    received_at: str = Field(min_length=1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(settings.database_url)
    db.run_migrations(conn)
    loaded = db.seed(conn, db.DATA_DIR)
    conn.close()
    app.state.initialized = loaded
    yield
    conn.close()


app = FastAPI(title="QuickDrop Rider Payout Dispute Desk", lifespan=lifespan)


def get_conn() -> psycopg.Connection:
    if not hasattr(app.state, "conn"):
        app.state.conn = db.connect(settings.database_url)
    return app.state.conn


@app.get("/health")
def health():
    try:
        conn = get_conn()
        ready = db.is_ready(conn, app.state.initialized)
    except Exception:
        raise HTTPException(status_code=503, detail="database not ready") from None
    if not ready:
        raise HTTPException(status_code=503, detail="migrations/seeding pending")
    return {"status": "ok"}


@app.post("/messages")
def messages(msg: IncomingMessage, request: Request):
    """Idempotency contract:
    - First delivery: process, store reply, trace.
    - Duplicate (same message_id): return stored reply; no new side effects.
    - Duplicate racing the first: wait briefly, else deterministic fallback.
    """
    try:
        received_at = _parse_received_at(msg.received_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="received_at not ISO-8601") from None

    conn = get_conn()
    claimed = _claim_inbound(conn, msg, received_at)
    if claimed == "exists":
        stored = conn.execute(
            "SELECT * FROM inbound_messages WHERE message_id = %s", (msg.message_id,)
        ).fetchone()
        if stored is None:
            return JSONResponse(status_code=200, content={"reply": _fallback_safe()})
        waited = _wait_for_reply(conn, msg.message_id)
        if waited:
            return {"reply": waited}
        return JSONResponse(status_code=200, content={"reply": _fallback_safe()})

    try:
        reply = _process_message(conn, msg, received_at)
    except Exception as exc:  # never 5xx: fail safe, escalate visible via ops page
        conn.execute(
            "UPDATE inbound_messages SET ok = false, error = %s,"
            "reply = %s, reply_at = now() WHERE message_id = %s",
            (str(exc)[:500], _fallback_safe(), msg.message_id),
        )
        _record_ops_failure(conn, msg.rider_id, str(exc))
        db.trace(
            conn,
            msg.rider_id,
            "error",
            "process_message",
            {"message_id": msg.message_id},
            {"error": str(exc)[:500]},
        )
        reply = _fallback_safe()
    return {"reply": reply}


def _claim_inbound(conn, msg: IncomingMessage, received_at: datetime) -> str:
    cur = conn.execute(
        "INSERT INTO inbound_messages (message_id, rider_id, text, received_at) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (message_id) DO NOTHING",
        (msg.message_id, msg.rider_id, msg.text, received_at),
    )
    return "claimed" if cur.rowcount else "exists"


def _wait_for_reply(conn, message_id: str, timeout_s: float = 8.0) -> str | None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(0.05)
        row = conn.execute(
            "SELECT reply, error, ok FROM inbound_messages WHERE message_id = %s", (message_id,)
        ).fetchone()
        if row and (row["reply"] is not None or row["ok"] is False):
            return row["reply"] if row["reply"] else _fallback_safe()
    return None


def _fallback_safe() -> str:
    return (
        "Aapki baat hum team tak pahuncha di hai. Punyarika/technical problem ke karan "
        "abhi confirm nahi kar pa rahe; humari ops team jald aapka case dekhegi."
    )


def _record_ops_failure(conn, rider_id: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO ops_items (id, rider_id, type, amount, reason) VALUES (%s,%s,%s,%s,%s)"
        "ON CONFLICT (id) DO NOTHING",
        (str(uuid.uuid4()), rider_id, "escalation", None, f"processing failure: {reason}"),
    )


def _process_message(conn, msg: IncomingMessage, received_at: datetime) -> str:
    """Phase-1 stub: deterministic acknowledgement; agent core arrives in Phase 3."""
    db.trace(
        conn,
        msg.rider_id,
        "message_in",
        "inbound_message",
        {"message_id": msg.message_id, "text": msg.text, "received_at": msg.received_at},
        None,
    )
    reply = (
        "Aapka message mil gaya (Phase 1 stub). "
        f"Ref: {msg.message_id} @ {received_at.isoformat()}"
    )
    db.trace(conn, msg.rider_id, "reply", "stub_reply", None, reply)
    conn.execute(
        "UPDATE inbound_messages SET reply = %s, reply_at = now(), ok = true "
        "WHERE message_id = %s",
        (reply, msg.message_id),
    )
    return reply


def _parse_received_at(value: str) -> datetime:
    from app.policy import parse_received_at

    return parse_received_at(value)


@app.get("/trace/{rider_id}")
def trace_for(rider_id: str):
    conn = get_conn()
    steps = conn.execute(
        "SELECT at, type, name, input, output "
        "FROM trace_steps WHERE rider_id = %s ORDER BY step_id",
        (rider_id,),
    ).fetchall()
    return [
        {
            "at": s["at"].isoformat(),
            "type": s["type"],
            "name": s["name"],
            "input": s["input"],
            "output": s["output"],
        }
        for s in steps
    ]


@app.get("/ops/pending")
def ops_pending():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, rider_id, type, amount, reason, created_at "
        "FROM ops_items WHERE status = 'pending' ORDER BY created_at",
    ).fetchall()
    return [
        {
            "id": r["id"],
            "rider_id": r["rider_id"],
            "type": r["type"],
            "amount": r["amount"],
            "reason": r["reason"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
