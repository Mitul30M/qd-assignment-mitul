"""Loads data/exports as pure records for the policy/reconciliation engine."""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

from app.policy import (
    CANCELLATION_PENALTY_LINE,
    DAILY_INCENTIVE_LINE,
    TRIP_LINE,
    TripRecord,
)
from app.reconciliation import PayoutLineRecord

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

#: Historical rider-id spellings in trips.csv -> rider_id used elsewhere.
RIDER_ALIASES = {"R7": "R007", "r19": "R019"}

RECON_STATUS = {
    "completed": "completed",
    "cancelled_by_rider": "cancelled_by_rider",
    "cancelled_by_customer": "cancelled_by_customer",
}
STATUS_VALUES = set(RECON_STATUS)


def load_riders(path: Path | None = None) -> list[dict]:
    with open(path or Path("data/riders.csv"), newline="") as f:
        return list(csv.DictReader(f))


def load_trips(path: Path | None = None) -> tuple[list[TripRecord], dict[str, int]]:
    """Return deduplicated trips + counts of duplicate CSV rows per trip_id."""
    with open(path or Path("data/trips.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["trip_id"]] = counts.get(row["trip_id"], 0) + 1
    trips_by_id: dict[str, TripRecord] = {}
    for row in rows:
        tid = row["trip_id"]
        if tid in trips_by_id:
            continue
        status = row["status"]
        if status not in STATUS_VALUES:
            raise ValueError(f"{path or 'trips.csv'}: {tid}: unknown status {status!r}")
        raw_km = row["distance_km"].strip()
        km = Decimal(raw_km) if raw_km else None
        surge = Decimal(row["surge_multiplier"].strip() or "1")
        trips_by_id[tid] = TripRecord(
            trip_id=tid,
            rider_id=RIDER_ALIASES.get(row["rider_id"].strip(), row["rider_id"].strip()),
            status=status,
            started_at=row["started_at"].strip(),
            distance_km=km,
            surge_multiplier=surge,
        )
    return list(trips_by_id.values()), counts


def load_payout_lines(path: Path | None = None) -> list[PayoutLineRecord]:
    with open(path or Path("data/payout_lines.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    lines: list[PayoutLineRecord] = []
    seen: dict[str, PayoutLineRecord] = {}
    for row in rows:
        line = PayoutLineRecord(
            line_id=row["line_id"],
            payout_date=row["payout_date"],
            rider_id=row["rider_id"].strip(),
            line_type=row["line_type"],
            trip_id=row["trip_id"].strip(),
            amount=Decimal(row["amount"]),
        )
        if line.line_type not in (TRIP_LINE, DAILY_INCENTIVE_LINE, CANCELLATION_PENALTY_LINE):
            raise ValueError(f"{path or 'payout_lines.csv'}: unknown line_type {line.line_type!r}")
        # identical duplicate rows across files: keep first, count the rest
        key = (line.line_id, str(line.amount))
        if key in seen:
            continue
        seen[key] = line
        lines.append(line)
    return lines


def load_conversations(path: Path | None = None) -> list[dict]:
    with open(path or Path("data/conversations.json")) as f:
        return json.load(f)
