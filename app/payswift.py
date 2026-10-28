"""Failure-aware PaySwift Payouts client.

Contract (from our black-box probe, docs/PAYSWIFT_NOTES.md):
- amount validated locally as integer 1..10000 before any call;
- every POST sends a deterministic Idempotency-Key; dispute id goes in `reference`;
- timeouts (connect + read) are mandatory;
- bounded exponential backoff + jitter for 429/5xx/timeouts, honoring Retry-After;
- 400 and 409 raise immediately (never retried);
- after exhausted ambiguous failures the caller reconciles via the ledger before
  declaring `failed`, because the sandbox can commit a payout and still answer 5xx.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from .config import settings


class PaySwiftError(Exception):
    """Any PaySwift exchange concern the caller must handle explicitly."""


class PaySwiftInvalidRequest(PaySwiftError):
    """400 — our payload was rejected; retrying the same body cannot help."""


class PaySwiftConflict(PaySwiftError):
    """409 — idempotency key reused with a different body; our keying is broken."""


class PaySwiftUnavailable(PaySwiftError):
    """Ambiguous transport failure after retries: the write may or may not exist."""


@dataclass(frozen=True)
class PayoutResult:
    status: str            # "confirmed" | "ambiguous"
    payout_id: str | None
    amount: int
    reference: str
    attempts: int


@dataclass
class ClientConfig:
    base_url: str = ""
    connect_timeout: float = 3.0
    read_timeout: float = 5.0
    max_attempts: int = 4
    backoff_base: float = 0.25
    sleep: Callable[[float], None] = field(default=time.sleep)


def _default_config() -> ClientConfig:
    if settings and settings.payswift_base_url:
        return ClientConfig(base_url=settings.payswift_base_url)
    return ClientConfig(base_url="http://localhost:8081")


def validate_amount(amount: int) -> int:
    """PaySwift whole-rupee validation, enforced in our code before the wire."""
    if isinstance(amount, int) and not isinstance(amount, bool) and 1 <= amount <= 10000:
        return amount
    raise PaySwiftInvalidRequest(f"amount must be integer 1..10000, got {amount!r}")


def idempotency_key(dispute_id: str, rider_id: str, amount: int, purpose: str) -> str:
    """Deterministic and collision-free; the key binds to the exact money action."""
    parts = (dispute_id or "none", rider_id, str(amount), purpose)
    return "qd-" + "|".join(parts)


class PaySwiftClient:
    def __init__(self, config: ClientConfig | None = None, transport: httpx.BaseTransport | None = None):
        cfg = config or _default_config()
        self.base_url = cfg.base_url.rstrip("/")
        self.connect_timeout = cfg.connect_timeout
        self.read_timeout = cfg.read_timeout
        self.max_attempts = max(1, cfg.max_attempts)
        self.backoff_base = cfg.backoff_base
        self.sleep = cfg.sleep
        self.transport = transport
        self.stats = {"attempts": 0, "retries": 0}

    def _post_once(self, payload: dict, idem: str) -> httpx.Response:
        self.stats["attempts"] += 1
        headers = {"Content-Type": "application/json", "Idempotency-Key": idem}
        try:
            resp = self._open().post("/v1/payouts", json=payload, headers=headers,
                                     timeout=httpx.Timeout(self.read_timeout, connect=self.connect_timeout))
        except httpx.TimeoutException as exc:
            raise PaySwiftUnavailable(f"payswift timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise PaySwiftUnavailable(f"payswift transport failure: {exc}") from exc
        return resp

    def _open(self) -> httpx.Client:
        if self.transport is not None or getattr(self, "_injected", False):
            return httpx.Client(base_url=self.base_url, transport=self.transport)
        self._injected = True
        return httpx.Client(base_url=self.base_url)

    def create_payout(self, rider_id: str, amount: int, reference: str, idem_key: str) -> PayoutResult:
        """Send one payout. Never retries after the provider may have written twice
        incompatibly: retries only OBEY idempotent semantics (same key + body)."""
        validate_amount(amount)
        payload = {"rider_id": rider_id, "amount": int(amount), "reference": reference}
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self._post_once(payload, idem_key)
            except PaySwiftUnavailable as exc:
                last = exc
                if attempt < self.max_attempts:
                    self.stats["retries"] += 1
                    self.sleep(self.backoff_base * (2 ** (attempt - 1)) + random.random() * self.backoff_base)
                continue
            if resp.status_code == 201:
                body = resp.json()
                return PayoutResult("confirmed", body.get("payout_id"), int(amount),
                                    payload["reference"], attempt)
            if resp.status_code == 400:
                raise PaySwiftInvalidRequest(f"payswift 400: {resp.text}")
            if resp.status_code == 409:
                raise PaySwiftConflict(f"payswift 409: {resp.text}")
            # 429/5xx: honor Retry-After when present.
            if attempt < self.max_attempts:
                self.stats["retries"] += 1
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if retry_after and str(retry_after).strip().isdigit() else \
                    self.backoff_base * (2 ** (attempt - 1)) + random.random() * self.backoff_base
                self.sleep(max(0.2, wait))
            else:
                last = PaySwiftUnavailable(f"payswift {resp.status_code} persisted after retries")
        raise PaySwiftUnavailable(f"payswift unavailable after {self.max_attempts} attempts: {last}")

    def ledger_for(self, rider_id: str) -> list[dict]:
        """Reconcile against PaySwift's ledger (the finance rule: reconcile vs PaySwift,
        not vs our DB). Returns its payout rows newest-first."""
        try:
            resp = self._open().get("/v1/payouts", params={"rider_id": rider_id},
                                    timeout=httpx.Timeout(self.read_timeout, connect=self.connect_timeout))
            if resp.status_code == 200:
                return resp.json().get("data", [])
            raise PaySwiftUnavailable(f"payswift ledger listing failed ({resp.status_code})")
        except httpx.HTTPError as exc:
            raise PaySwiftUnavailable(f"payswift ledger unreachable: {exc}") from exc
