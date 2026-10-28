"""Payments state machine against the real compose Postgres (skips without it)."""

import os

import psycopg
import pytest

from app.payments import (
    get,
    list_for_rider_day,
    mark_sent,
    persist_intent,
    reconcile_stuck_sent,
    send_payout,
)
from app.payswift import ClientConfig, PaySwiftClient, PaySwiftUnavailable, idempotency_key

DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://qd:qd@localhost:5432/qd")


def _conn():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(DSN, row_factory=dict_row, autocommit=True)


@pytest.fixture()
def db_conn():
    try:
        conn = _conn()
        conn.execute("SELECT 1")
    except psycopg.OperationalError:
        pytest.skip("test database not available")
    conn.execute("DELETE FROM payments WHERE rider_id LIKE 'RTEST%'")
    return conn


def _cleanup():
    try:
        conn = _conn()
    except psycopg.OperationalError:
        return
    conn.execute("DELETE FROM payments WHERE rider_id LIKE 'RTEST%'")
    conn.close()


class ScriptedClient(PaySwiftClient):
    """HTTP-free client stub with a controllable ledger."""

    def __init__(self, outcomes: list[str]):
        super().__init__(ClientConfig(base_url="http://x", sleep=lambda s: None))
        self.outcomes = list(outcomes)
        self.ledger_rows: list[dict] = []

    def create_payout(self, rider_id, amount, reference, idem_key):
        outcome = self.outcomes.pop(0)
        if outcome == "201":
            self.ledger_rows.append({"payout_id": f"pout_{len(self.ledger_rows)+1}",
                                     "rider_id": rider_id, "amount": amount,
                                     "reference": reference})
            from app.payswift import PayoutResult
            return PayoutResult("confirmed", self.ledger_rows[-1]["payout_id"], amount, reference, 1)
        if outcome == "503-committed":
            self.ledger_rows.append({"payout_id": f"pout_{len(self.ledger_rows)+1}",
                                     "rider_id": rider_id, "amount": amount,
                                     "reference": reference})
            raise PaySwiftUnavailable("503 after retries")
        raise PaySwiftUnavailable(f"timeout after retries ({outcome})")

    def ledger_for(self, rider_id):
        return [r for r in self.ledger_rows if r["rider_id"] == rider_id]


def test_ambiguous_write_is_polled_before_marking_failed(db_conn):
    class DelayedLedgerClient(ScriptedClient):
        def __init__(self):
            super().__init__(["timeout"])
            self.reads = 0

        def ledger_for(self, rider_id):
            self.reads += 1
            if self.reads == 3:
                return [{"payout_id": "pout_delayed", "rider_id": rider_id,
                         "amount": 5, "reference": "disp-delayed"}]
            return []

    client = DelayedLedgerClient()
    payment = send_payout(db_conn, client, "RTESTDELAY", "disp-delayed", 5, "surge",
                          idempotency_key("disp-delayed", "RTESTDELAY", 5, "surge"))
    assert payment["state"] == "confirmed"
    assert payment["provider_payout_id"] == "pout_delayed"


def test_intent_persisted_before_call_and_confirmed(db_conn):
    before = get(db_conn, "nonexistent") is None
    assert before
    payment = send_payout(db_conn, ScriptedClient(["201"]), "RTESTA", "disp-1", 5, "surge",
                          idempotency_key("disp-1", "RTESTA", 5, "surge"))
    assert payment["state"] == "confirmed" and payment["provider_payout_id"].startswith("pout_")
    assert payment["created_at"] is not None
    intentified = get(db_conn, payment["payment_id"])
    assert intentified["idempotency_key"] == idempotency_key("disp-1", "RTESTA", 5, "surge")


def test_failed_intents_do_not_block_retry_after_new_day(db_conn):
    payment = send_payout(db_conn, ScriptedClient(["504"]), "RTESTB", "disp-2", 5, "surge",
                          idempotency_key("disp-2", "RTESTB", 5, "surge"))
    assert payment["state"] == "failed"


def test_dispute_unique_purpose_constraint_blocks_second_auto_intent(db_conn):
    from app.payments import persist_intent
    key = idempotency_key("disp-3", "RTESTC", 5, "auto")
    persist_intent(db_conn, "RTESTC", 5, "auto", "disp-3", key)
    with pytest.raises(psycopg.errors.UniqueViolation):
        persist_intent(db_conn, "RTESTC", 5, "auto", "disp-3", key)


def test_startup_recovery_resolves_stuck_sent(db_conn):
    intent = persist_intent(db_conn, "RTESTD", 5, "surge", "disp-4",
                            idempotency_key("disp-4", "RTESTD", 5, "surge"))
    mark_sent(db_conn, intent["payment_id"])
    stuck = db_conn.execute(
        "SELECT state FROM payments WHERE payment_id=%s", [intent["payment_id"]]).fetchone()
    assert stuck["state"] == "sent"

    committed = ScriptedClient([])   # nothing left to send; ledger has the committed write
    committed.ledger_rows = [{"payout_id": "pout_rec", "rider_id": "RTESTD", "amount": 5,
                              "reference": "disp-4"}]
    resolved = reconcile_stuck_sent(db_conn, committed)
    assert resolved and resolved[0]["state"] == "confirmed"
    assert get(db_conn, intent["payment_id"])["state"] == "confirmed"

    absent = ScriptedClient([])
    missing = persist_intent(db_conn, "RTESTE", 5, "surge", "disp-5",
                             idempotency_key("disp-5", "RTESTE", 5, "surge"))
    mark_sent(db_conn, missing["payment_id"])
    resolved_missing = reconcile_stuck_sent(db_conn, absent)
    assert resolved_missing and resolved_missing[0]["state"] == "failed"


def assert_one_confirmed_per_dispute(db_conn):
    rows = list_for_rider_day(db_conn, "RTESTF", "2026-09-22")
    assert all(r["state"] != "failed" for r in rows)


def test_list_for_rider_day_filters_failed(db_conn):
    import datetime

    today = datetime.datetime.now(datetime.UTC).astimezone(
        datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date().isoformat()
    payments = [
        (ScriptedClient(["504"]), "disp-6a"),
        (ScriptedClient(["201"]), "disp-6b"),
    ]
    for client, disp in payments:
        send_payout(db_conn, client, "RTESTF", disp, 5, "surge",
                    idempotency_key(disp, "RTESTF", 5, "surge"))
    rows = list_for_rider_day(db_conn, "RTESTF", today)
    assert len(rows) == 1 and rows[0]["state"] == "confirmed"
    assert_one_confirmed_per_dispute(db_conn)
