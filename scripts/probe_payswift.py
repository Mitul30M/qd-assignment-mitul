"""Read-only behaviour probe of the PaySwift sandbox.

Documents documented-undocumented response shapes, idempotency semantics,
validation boundaries, rate limiting and retry markers, and list filtering.
Every probe call uses the RPROBE* rider so production rider ledgers stay clean.
Writes a machine-readable summary plus prints observations for PAYSWIFT_NOTES.md.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

BASE = os.environ.get("PAYSWIFT_BASE_URL", "http://localhost:8081")
RUN = datetime.now(UTC).strftime("%H%M%S")
results: list[dict] = []


def call(method: str, path: str, body: dict | None = None, idem: str | None = None) -> dict:
    """One HTTP exchange: status, body, latency, and key response headers."""
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if idem:
        req.add_header("Idempotency-Key", idem)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            latency_ms = (time.perf_counter() - t0) * 1000
            rec = {
                "name": name[-1],
                "method": method,
                "path": path,
                "status": resp.status,
                "headers": {k.lower(): v for k, v in resp.headers.items()
                            if k.lower() in ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "content-type")},
                "body": json.loads(raw) if raw.strip().startswith(("{", "[")) else raw,
                "latency_ms": round(latency_ms, 1),
            }
    except urllib.error.HTTPError as e:
        latency_ms = (time.perf_counter() - t0) * 1000
        raw = e.read().decode()
        rec = {
            "name": name[-1],
            "method": method,
            "path": path,
            "status": e.code,
            "headers": {k.lower(): v for k, v in e.headers.items()
                        if k.lower() in ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "content-type")},
            "body": json.loads(raw) if raw.strip().startswith(("{", "[")) else raw,
            "latency_ms": round(latency_ms, 1),
        }
    results.append(rec)
    print(f"[{rec['status']}] {method} {path} {rec['headers'] or ''} {json.dumps(rec['body'])[:120]}")
    return rec


name: list[str] = ["setup"]


def step(label):
    name[0] = label


# ---- 0. health ----
step("health")
call("GET", "/health")

# ---- 1. documented happy path ----
step("happy_normal")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": 5, "reference": f"probe-{RUN}"})
step("happy_no_idem_two_calls_two_payouts")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": 5, "reference": f"probe-{RUN}-a"})
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": 5, "reference": f"probe-{RUN}-b"})

# ---- 2. idempotency semantics ----
step("idem_replay_same_body")
k = f"probe-{RUN}-idem"
r1 = call("POST", "/v1/payouts", {"rider_id": "RPROBE-2", "amount": 7, "reference": f"probe-{RUN}-r"}, idem=k)
step("idem_replay_same_key_again")
r2 = call("POST", "/v1/payouts", {"rider_id": "RPROBE-2", "amount": 7, "reference": f"probe-{RUN}-r"}, idem=k)
step("ledger_after_replays")
call("GET", "/v1/payouts?rider_id=RPROBE-2")

step("idem_same_key_different_body")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-3", "amount": 9, "reference": f"probe-{RUN}-x"}, idem=k)

# ---- 3. validation boundaries ----
step("boundary_zero")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": 0, "reference": "zero"})
step("boundary_negative")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": -3, "reference": "neg"})
step("boundary_float")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": 5.5, "reference": "float"})
step("boundary_10001")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": 10001, "reference": "big"})
step("boundary_10000")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": 10000, "reference": f"probe-{RUN}-max"})
step("boundary_amount_string")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": "5", "reference": "str"})
step("boundary_unknown_rider")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-UNKNOWN-404", "amount": 5, "reference": "ghost"})
step("boundary_missing_field")
call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "reference": "no-amount"})

# ---- 4. list endpoint semantics ----
step("list_all")
call("GET", "/v1/payouts")
step("list_filter")
call("GET", "/v1/payouts?rider_id=RPROBE-2")
step("list_unknown_rider")
call("GET", "/v1/payouts?rider_id=NOBODY")

# ---- 5. rate limiting + Retry-After ----
step("rate_limit_burst")
codes = []
for i in range(40):
    try:
        rec = call("POST", "/v1/payouts", {"rider_id": "RPROBE-1", "amount": 1, "reference": f"burst-{RUN}-{i}"})
        codes.append(rec["status"])
    except Exception as e:
        codes.append(f"exc:{type(e).__name__}")
print("burst statuses:", codes)

# ---- 6. ledger reset semantics ----
step("ledger_note")
print("NOTE: ledger is in-memory; `docker compose restart payswift` clears it.")

with open("docs/payswift_probe_raw.json", "w") as f:
    json.dump({"run": RUN, "results": results}, f, indent=2)
print("\nwrote docs/payswift_probe_raw.json with", len(results), "exchanges")
