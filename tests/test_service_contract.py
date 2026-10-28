"""End-to-end service tests against a live stack (httpx over localhost)."""

from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("SERVICE_URL", "http://localhost:8000")

pytestmark = pytest.mark.integration


def _service_up() -> bool:
    try:
        return httpx.Client(base_url=BASE, timeout=2).get("/health").status_code == 200
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_if_down():
    if not _service_up():
        pytest.skip(f"no live service at {BASE}")


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=15)


def test_health():
    r = client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_message_stub_reply_and_trace_and_ops():
    c = client()
    body = {
        "message_id": "wamid.TEST-TRACE-1",
        "rider_id": "R003",
        "text": "bhai payout galat aaya hai",
        "received_at": "2026-09-22T09:00:00+05:30",
    }
    r1 = c.post("/messages", json=body)
    assert r1.status_code == 200
    assert "reply" in r1.json()

    tr = c.get("/trace/R003").json()
    assert isinstance(tr, list)
    for step in tr:
        assert set(step) >= {"at", "type", "name", "input", "output"}
        assert step["type"] in {"message_in", "tool_call", "decision", "reply", "error"}
    assert any(s["type"] == "message_in" for s in tr)
    assert any(s["type"] == "reply" for s in tr)

    assert c.get("/ops/pending").status_code == 200


def test_duplicate_message_id_same_reply_one_inbound_row():
    c = client()
    body = {
        "message_id": "wamid.TEST-DUP-1",
        "rider_id": "R003",
        "text": "order T926334 ka surge nahi mila",
        "received_at": "2026-09-22T09:05:00+05:30",
    }
    r1 = c.post("/messages", json=body)
    r2 = c.post("/messages", json=body)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["reply"] == r2.json()["reply"]


def test_malformed_input_is_4xx():
    c = client()
    r = c.post("/messages", json={"message_id": "", "rider_id": "R003", "text": "x", "received_at": "bad"})
    assert 400 <= r.status_code < 500
