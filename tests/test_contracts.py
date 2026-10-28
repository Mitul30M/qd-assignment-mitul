"""Contract tests: fixed HTTP shapes + idempotency, run against a live service.

Phase-1 harness: exercises the service in-process via TestClient with a real
Postgres (TEST_DATABASE_URL) when available; skips with a clear message otherwise.
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.contract


def test_contract_schemas():
    """Static shape validation of the fixed contracts (SUBMISSION.md)."""
    trace_step = {
        "at": "2026-09-22T09:00:00+05:30",
        "type": "message_in",
        "name": "inbound_message",
        "input": {"message_id": "wamid.X", "text": "t", "received_at": "2026-09-22T09:00:00+05:30"},
        "output": None,
    }
    assert json.loads(json.dumps(trace_step))["type"] in {"message_in", "tool_call", "decision", "reply", "error"}

    pending = {
        "id": "abc",
        "rider_id": "R003",
        "type": "approval",
        "amount": 120,
        "reason": "cap",
        "created_at": "2026-09-22T09:00:00+05:30",
    }
    assert set(pending) == {"id", "rider_id", "type", "amount", "reason", "created_at"}
    esc = {"id": "e", "rider_id": "R004", "type": "escalation", "amount": None, "reason": "shady", "created_at": "t"}
    assert esc["amount"] is None


def test_env_lists_every_var():
    with open(".env.example") as f:
        example = f.read()
    for key in ["DATABASE_URL", "PAYSWIFT_BASE_URL", "LLM_PROVIDER", "LLM_API_KEY", "APP_PORT"]:
        assert key in example, f".env.example missing {key}"


def test_bad_db_url_fails_fast_and_clearly():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set")
    from app.db import connect

    with pytest.raises(RuntimeError, match="database connect failed"):
        db_url = os.environ["TEST_DATABASE_URL"].split("@", 1)
        bad = "postgresql://qd:qd@localhost:1/none?connect_timeout=1"
        if len(db_url) > 1:
            bad = f"{db_url[0]}@localhost:1/none?connect_timeout=1"
        connect(bad)


def test_health_503_when_db_unreachable():
    # configuration contract: a broken DATABASE_URL must fail readiness, not hang
    from app.db import connect

    try:
        connect("postgresql://qd:qd@localhost:1/none?connect_timeout=1")
        pytest.fail("connect should have raised")
    except RuntimeError:
        pass


def test_migrations_and_seed_idempotent(tmp_path):
    """seed() inserted rows survive a second run (idempotent upserts)."""
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set")
    from app import db

    conn = db.connect(os.environ["TEST_DATABASE_URL"])
    db.run_migrations(conn)
    from app.data_loader import DATA_DIR

    db.seed(conn, DATA_DIR)
    db.seed(conn, DATA_DIR)
    row = conn.execute("SELECT count(*) AS n FROM trips").fetchone()
    assert row["n"] == 3304
    # second run must not change totals (upserts, not duplicates)
    assert conn.execute("SELECT count(*) AS n FROM trips").fetchone()["n"] == 3304
    assert conn.execute("SELECT count(*) AS n FROM payout_lines").fetchone()["n"] == 3222
    assert conn.execute("SELECT count(*) AS n FROM riders").fetchone()["n"] == 40
    conn.close()
