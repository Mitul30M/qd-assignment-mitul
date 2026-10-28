"""CLI: run reconciliation over data/ exports and print a summary report."""

from __future__ import annotations

import sys
from collections import Counter

from app.data_loader import DATA_DIR, load_payout_lines, load_riders, load_trips
from app.reconciliation import reconcile


def main() -> int:
    riders = load_riders(DATA_DIR / "riders.csv")
    trips, dup_counts = load_trips(DATA_DIR / "trips.csv")
    lines = load_payout_lines(DATA_DIR / "payout_lines.csv")
    rec = reconcile(trips, lines, dup_counts)

    print(f"riders={len(riders)} trips={len(trips)} payout_lines={len(lines)}")
    print("anomaly categories:")
    for cat, n in sorted(rec.totals.items(), key=lambda kv: -kv[1]):
        print(f"  {cat}: {n}")
    print(f"total_overpaid_to_riders={rec.total_overpaid}")
    print(f"total_underpaid_to_riders={rec.total_underpaid}")

    print("\nsample anomalies:")
    shown = Counter()
    for trip_id, d in rec.per_trip.items():
        cat = d["category"]
        if cat == "ok" or shown[cat] >= 2:
            continue
        shown[cat] += 1
        print(f"  {cat} {trip_id}: rider={d['rider_id']} exp={d['expected']} paid={d['paid']} short={d.get('shortfall', 0)}")

    by_cat: Counter = Counter(d["category"] for d in rec.per_trip.values())
    print("\ntrip categories:", dict(by_cat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
