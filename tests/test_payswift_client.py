"""PaySwift client behaviour with a mocked HTTP transport (no network)."""

import json

import httpx
import pytest

from app.payswift import (
    ClientConfig,
    PaySwiftClient,
    PaySwiftConflict,
    PaySwiftInvalidRequest,
    PaySwiftUnavailable,
    idempotency_key,
    validate_amount,
)


class FakeProvider(httpx.BaseTransport):
    """Scripted PaySwift sandbox.

    script: list of actions per POST:
      {"status": 201|400|409|429|500|503|504,
       "body": {...} | None (None -> transport TimeoutException),
       "reply_after": str or None,
       "commits": bool  -- probes proved 5xx can still carry a committed write
    Ledger lines for GET /v1/payouts record every successful write.
    """

    def __init__(self, script: list[dict], timeout_exhaust: bool = False):
        self.script = list(script)
        self.posts: list[dict] = []
        self.ledger: list[dict] = []
        self.timeout_exhaust = timeout_exhaust

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            rider = request.url.params.get("rider_id")
            data = [p for p in self.ledger if p["rider_id"] == rider] if rider else self.ledger
            return httpx.Response(200, json={"data": data, "count": len(data)})
        self.posts.append({
            "body": request.content.decode(),
            "idem": request.headers.get("Idempotency-Key"),
        })
        if self.timeout_exhaust:
            raise httpx.ConnectTimeout("connect timeout")
        if not self.script:
            return httpx.Response(500, json={"error": "unexpected_extra_call"})
        action = self.script.pop(0)
        body_in = {
            "rider_id": "R001", "amount": 5, "reference": "d1",
        }
        if action.get("commits"):
            self.ledger.append({"payout_id": f"pout_{len(self.ledger)+1}",
                                "rider_id": "R001", "amount": 5,
                                "reference": body_in["reference"], "status": "processed"})
        if action["status"] == 201:
            rid, amount, ref = "R001", action.get("amount", 5), "d1"
            self.ledger.append({"payout_id": f"pout_{len(self.ledger)+1}",
                                "rider_id": rid, "amount": amount,
                                "reference": ref, "status": "processed"})
            return httpx.Response(201, json={"payout_id": self.ledger[-1]["payout_id"],
                                             "rider_id": rid, "amount": amount,
                                             "reference": ref, "status": "processed"})
        headers = {}
        if action.get("retry_after"):
            headers["Retry-After"] = str(action["retry_after"])
        return httpx.Response(action["status"],
                              json=action.get("body", {"error": "err"}), headers=headers)


def make_client(provider: FakeProvider, max_attempts: int = 3) -> PaySwiftClient:
    cfg = ClientConfig(base_url="http://payswift.test", max_attempts=max_attempts,
                       backoff_base=0, sleep=lambda s: sleeps.append(s))
    return PaySwiftClient(cfg, transport=provider)


sleeps: list[float] = []


@pytest.fixture(autouse=True)
def _clear_sleeps():
    sleeps.clear()
    yield


# ---------------- unit level ----------------

def test_validate_amount_boundaries():
    assert validate_amount(1) == 1
    assert validate_amount(10000) == 10000
    for bad in (0, -3, 5.5, "5", 10001, None):
        with pytest.raises(PaySwiftInvalidRequest):
            validate_amount(bad)


def test_idem_key_deterministic_differs_when_action_differs():
    a = idempotency_key("d1", "R001", 5, "surge")
    assert a == idempotency_key("d1", "R001", 5, "surge")
    for variant in (idempotency_key("d1", "R001", 6, "surge"),
                    idempotency_key("d1", "R001", 5, "penalty"),
                    idempotency_key("d2", "R001", 5, "surge"),
                    idempotency_key("d1", "R002", 5, "surge")):
        assert a != variant


# ---------------- transport scenarios ----------------

def test_201_confirms_and_carries_idem_key():
    provider = FakeProvider([{"status": 201}])
    out = make_client(provider).create_payout("R001", 5, "d1", "key-1")
    assert out.status == "confirmed" and out.payout_id.startswith("pout_")
    assert provider.posts[0]["idem"] == "key-1"
    assert json.loads(provider.posts[0]["body"])["amount"] == 5


def test_429_retries_then_confirms():
    provider = FakeProvider([{"status": 429, "retry_after": "2"}, {"status": 201}])
    c = make_client(provider)
    out = c.create_payout("R001", 5, "d1", "key-2")
    assert out.status == "confirmed" and out.attempts == 2
    assert c.stats["retries"] == 1 and sleeps == [2.0]


def test_400_never_retried():
    provider = FakeProvider([{"status": 400, "body": {"error": "invalid_amount"}}])
    with pytest.raises(PaySwiftInvalidRequest):
        make_client(provider).create_payout("R001", 5, "d1", "key-3")
    assert len(provider.posts) == 1


def test_409_never_retried():
    provider = FakeProvider([{"status": 409, "body": {"error": "idempotency_key_reused"}}])
    with pytest.raises(PaySwiftConflict):
        make_client(provider).create_payout("R001", 5, "d1", "key-4")
    assert len(provider.posts) == 1


def test_mixed_5xx_and_timeout_bounded_retries_then_actual_write_reaches_ledger():
    """500-after-commit + timeout + 503: the request finally lands via ledger."""
    provider = FakeProvider([
        {"status": 503, "commits": True},   # wrote, lied to us
    ])
    provider.script = [{"status": 503, "commits": True}, {"status": 201}]
    provider.timeout_exhaust = False
    c = make_client(provider)
    out = c.create_payout("R001", 5, "d1", "key-5")
    assert out.status == "confirmed"
    assert len([p for p in provider.ledger if p["reference"] == "d1"]) >= 1


def test_timeout_always_raises_after_bounded_attempts():
    provider = FakeProvider([], timeout_exhaust=True)
    c = make_client(provider, max_attempts=4)
    with pytest.raises(PaySwiftUnavailable):
        c.create_payout("R001", 5, "d1", "key-6")
    assert c.stats["attempts"] == 4


def test_exhausted_5xx_bounded_retries_raises_unavailable():
    provider = FakeProvider([{"status": 504}] * 3)
    c = make_client(provider, max_attempts=3)
    with pytest.raises(PaySwiftUnavailable):
        c.create_payout("R001", 5, "d1", "key-7")
    assert c.stats["attempts"] == 3
