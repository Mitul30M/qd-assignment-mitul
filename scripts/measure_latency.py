#!/usr/bin/env python3
"""Measure public message latency and enforce the webhook budget."""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("service_url")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--max-p95-ms", type=float, default=10_000)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")

    url = f"{args.service_url.rstrip('/')}/messages"
    samples: list[float] = []
    for index in range(args.count):
        body = json.dumps({
            "message_id": f"wamid.LATENCY-{time.time_ns()}-{index}",
            "rider_id": "R003",
            "text": "Mera payout ka update batao",
            "received_at": "2026-09-22T09:00:00+05:30",
        }).encode()
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=12) as response:
            if response.status != 200 or "reply" not in json.loads(response.read()):
                raise RuntimeError(f"unexpected message response: HTTP {response.status}")
        samples.append((time.perf_counter() - started) * 1000)

    samples.sort()
    p95 = samples[max(0, math.ceil(len(samples) * 0.95) - 1)]
    print(f"latency_ms: min={samples[0]:.1f} p50={samples[len(samples) // 2]:.1f} "
          f"p95={p95:.1f} max={samples[-1]:.1f} n={len(samples)}")
    if p95 > args.max_p95_ms:
        raise SystemExit(f"p95 {p95:.1f}ms exceeds {args.max_p95_ms:.1f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
