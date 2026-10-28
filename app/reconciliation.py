"""Reconciliation: compare what the policy owes vs what payout_lines.csv paid.

Pure module: callers supply trip/payout records; no I/O here.
"""

from __future__ import annotations

import collections.abc
from dataclasses import dataclass, field
from decimal import Decimal

from .policy import (
    CANCELLATION_PENALTY_LINE,
    DAILY_INCENTIVE,
    DAILY_INCENTIVE_LINE,
    PENALTY,
    STATUS_CANCELLED_BY_RIDER,
    STATUS_COMPLETED,
    TRIP_LINE,
    TripRecord,
    expected_for_trip,
    fare_of_no_surge,
    trip_ist_day,
)

PENALTY_AMOUNT = -PENALTY


@dataclass(frozen=True)
class PayoutLineRecord:
    line_id: str
    payout_date: str
    rider_id: str
    line_type: str
    trip_id: str
    amount: Decimal


@dataclass
class Reconciliation:
    """Per-trip and per-rider-day owed vs paid, with categorized anomalies."""

    per_trip: dict[str, dict] = field(default_factory=dict)
    per_rider_day: dict[tuple[str, str], dict] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    total_overpaid: int = 0
    total_underpaid: int = 0
    duplicate_trip_rows: list[str] = field(default_factory=list)


def normalize_rider_id(rider_id: str) -> str:
    """Historical rider-id spellings in trips.csv."""
    return {"R7": "R007", "r19": "R019"}.get(rider_id, rider_id)


def reconcile(
    trips: list[TripRecord],
    lines: list[PayoutLineRecord],
    trip_row_counts: dict[str, int] | None = None,
) -> Reconciliation:
    trip_by_id = {trip.trip_id: trip for trip in trips}

    lines_by_trip: dict[str, list[PayoutLineRecord]] = collections.defaultdict(list)
    for line in lines:
        lines_by_trip[line.trip_id].append(line)

    result = Reconciliation()
    totals: dict[str, int] = collections.Counter()
    total_overpaid = 0
    total_underpaid = 0

    for trip_id, trip in trip_by_id.items():
        expected = expected_for_trip(trip)
        trip_lines = [l for l in lines_by_trip[trip_id] if l.line_type == TRIP_LINE]
        pen_lines = [l for l in lines_by_trip[trip_id] if l.line_type == CANCELLATION_PENALTY_LINE]
        paid = sum((l.amount for l in trip_lines), Decimal(0)) + sum(
            (l.amount for l in pen_lines), Decimal(0)
        )

        detail = {
            "rider_id": trip.rider_id,
            "status": trip.status,
            "started_at": trip.started_at,
            "distance_km": str(trip.distance_km),
            "surge": str(trip.surge_multiplier),
            "expected": str(expected),
            "paid": str(paid),
            "category": "ok",
        }

        if trip.status == STATUS_COMPLETED:
            if not trip_lines and not pen_lines:
                totals["missing_pay_line"] += 1
                detail["category"] = "missing_pay_line"
                detail["shortfall"] = str(expected)  # paid 0; rider is owed the fare
            elif len(trip_lines) > 1:
                totals["duplicate_trip_line"] += 1
                detail["category"] = "duplicate_trip_line"
            elif abs(paid) != abs(expected):
                if paid < expected:
                    if trip.surge_multiplier != 1 and paid == fare_of_no_surge(trip.distance_km):
                        totals["surge_not_applied"] += 1
                        detail["category"] = "surge_not_applied"
                    else:
                        totals["underpaid"] += 1
                        detail["category"] = "underpaid"
                else:
                    totals["overpaid"] += 1
                    detail["category"] = "overpaid"
                detail["shortfall"] = str(expected - paid)
        elif trip.status == STATUS_CANCELLED_BY_RIDER:
            if len(pen_lines) == 0:
                totals["missing_penalty"] += 1
                detail["category"] = "missing_penalty"
                detail["shortfall"] = str(expected - paid)  # negative: rider keeps the 10
            elif len(pen_lines) > 1:
                extras = len(pen_lines) - 1
                totals["duplicate_penalty"] += 1
                detail["category"] = "duplicate_penalty"
                detail["shortfall"] = str(extras * PENALTY)  # rider is owed the extra back
        else:  # cancelled_by_customer earns nothing
            if trip_lines:
                totals["paid_non_earning_trip"] += 1
                detail["category"] = "paid_non_earning_trip"
            elif pen_lines:
                totals["penalty_for_customer_cancel"] += 1
                detail["category"] = "penalty_for_customer_cancel"

        shortfall = int(detail.get("shortfall", 0))
        if shortfall > 0:
            total_underpaid += shortfall
        elif shortfall < 0:
            total_overpaid += -shortfall
        result.per_trip[trip_id] = detail

    for line in lines:
        if line.line_type not in (TRIP_LINE, DAILY_INCENTIVE_LINE, CANCELLATION_PENALTY_LINE):
            totals["unknown_line_type"] += 1
        if line.trip_id and line.trip_id not in trip_by_id:
            totals["unexpected_pay_line"] += 1

    # ---- rider-day level (completed counts + incentive) ----
    comp_counts: collections.Counter = collections.Counter()
    for trip in trip_by_id.values():
        if trip.status == STATUS_COMPLETED:
            key = (trip.rider_id, trip_ist_day(trip).isoformat())
            comp_counts[key] += 1

    inc_by_rd: dict[tuple[str, str], list[PayoutLineRecord]] = collections.defaultdict(list)
    for line in lines:
        if line.line_type == DAILY_INCENTIVE_LINE:
            inc_by_rd[(line.rider_id, line.payout_date)].append(line)

    days_seen = set(comp_counts) | set(inc_by_rd)
    for key in sorted(days_seen):
        rider, day = key
        n = comp_counts.get(key, 0)
        owed_inc = DAILY_INCENTIVE if n >= 12 else Decimal(0)
        paid_inc = sum((l.amount for l in inc_by_rd.get(key, [])), Decimal(0))
        result.per_rider_day[key] = {
            "rider_id": rider,
            "ist_day": day,
            "completed": n,
            "incentive_owed": str(owed_inc),
            "incentive_paid": str(paid_inc),
        }
        if owed_inc > 0 and paid_inc < owed_inc:
            totals["incentive_missing"] += 1
            total_underpaid += int(owed_inc - paid_inc)
        if paid_inc > owed_inc:
            totals["incentive_overpaid"] += 1
            total_overpaid += int(paid_inc - owed_inc)

    if trip_row_counts:
        result.duplicate_trip_rows = [k for k, c in trip_row_counts.items() if c > 1]
        totals["duplicate_trip_rows"] = len(result.duplicate_trip_rows)

    result.total_overpaid = total_overpaid
    result.total_underpaid = total_underpaid

    return Reconciliation(
        per_trip=result.per_trip,
        per_rider_day=dict(result.per_rider_day),
        totals=dict(totals),
        total_overpaid=total_overpaid,
        total_underpaid=total_underpaid,
        duplicate_trip_rows=result.duplicate_trip_rows,
    )
