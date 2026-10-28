"""Dispute verification: rider claims -> verified shortfall from source evidence.

Pure decision layer (no I/O; callers fetch records and pass them in):
cash amounts stated by the rider or the model are never trusted; the verified
shortfall is always computed from trip rows and payout lines. Routing (auto-pay
≤ ₹200 once per rider-IST-day; larger/repeat -> ops approval) is deterministic
code, not model choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .policy import DAILY_INCENTIVE, TripRecord, expected_for_trip
from .reconciliation import (
    CANCELLATION_PENALTY_LINE,
    DAILY_INCENTIVE_LINE,
    TRIP_LINE,
    PayoutLineRecord,
    normalize_rider_id,
)

AUTO_PAY_LIMIT = Decimal(200)


@dataclass
class DisputeVerdict:
    """Everything the conversation layer needs: decision, amounts, evidence."""

    rider_id: str
    state: str = ""  # verified | no_shortfall | cannot_verify | unknown_rider | foreign_trip
    shortfalls: dict[str, str] = field(default_factory=dict)  # trip_id or incentive:day -> rs
    verified_amount: int = 0  # total positive shortfall in integer rupees
    route: str = "none"  # none | auto | approval
    route_reason: str = ""
    evidence: dict = field(default_factory=dict)


def verify_dispute(
    wanted_rider: str,
    claimed_trip_ids: list[str],
    rider_exists: bool,
    rider_trips: list[TripRecord],
    rider_lines: list[PayoutLineRecord],
    trip_lookup: dict[str, TripRecord],
    already_autopaid_today: bool,
    incentive_days: dict[str, int] | None = None,
) -> DisputeVerdict:
    """Verify a dispute against source records only.

    - unknown rider -> state unknown_rider, no payout (identity binds first)
    - claimed trip not in the data, or owned by someone else -> cannot_verify /
      foreign_trip; a number is never invented
    - shortfall per trip = max(0, expected - paid) from source rows; rider-stated
      amounts are ignored
    - optional incentive day check (12+ completed trips in an IST day worth ₹150)
    - route: auto iff 0 < amount <= 200 and not already auto-paid that rider-day
    """
    rider = normalize_rider_id(wanted_rider)
    verdict = DisputeVerdict(rider_id=rider)

    if not rider_exists:
        verdict.state = "unknown_rider"
        verdict.evidence["reason"] = f"rider {rider} not found in trips data"
        return verdict

    for tid in claimed_trip_ids:
        trip = trip_lookup.get(tid)
        if trip is None:
            verdict.state = "cannot_verify"
            verdict.evidence["reason"] = f"trip {tid} not found in source data"
            return verdict
        if trip.rider_id != rider:
            verdict.state = "foreign_trip"
            verdict.evidence["reason"] = f"trip {tid} exists but is not {rider}'s trip"
            return verdict

    lines_by_trip: dict[str, list[PayoutLineRecord]] = {}
    for line in rider_lines:
        if line.line_type in (TRIP_LINE, CANCELLATION_PENALTY_LINE) and line.rider_id == rider:
            lines_by_trip.setdefault(line.trip_id, []).append(line)

    candidate_trips = (
        rider_trips if not claimed_trip_ids else [trip_lookup[t] for t in claimed_trip_ids]
    )
    total = 0
    for trip in candidate_trips:
        paid = sum((l.amount for l in lines_by_trip.get(trip.trip_id, [])), Decimal(0))
        expected = expected_for_trip(trip)
        shortfall = max(Decimal(0), expected - paid)
        if shortfall > 0:
            verdict.shortfalls[trip.trip_id] = str(shortfall)
            total += int(shortfall)

    if incentive_days:
        inc_paid: dict[str, Decimal] = {}
        for line in rider_lines:
            if line.line_type == DAILY_INCENTIVE_LINE:
                inc_paid[line.payout_date] = inc_paid.get(line.payout_date, Decimal(0)) + line.amount
        for day, owned in sorted(incentive_days.items()):
            owed = DAILY_INCENTIVE if owned >= 12 else Decimal(0)
            short = owed - inc_paid.get(day, Decimal(0))
            if short > 0:
                verdict.shortfalls[f"incentive:{day}"] = str(short)
                total += int(short)

    verdict.verified_amount = total
    if total == 0:
        verdict.state = "no_shortfall"
        verdict.evidence["reason"] = "records show the rider was paid in full"
        return verdict
    verdict.state = "verified"
    if total <= int(AUTO_PAY_LIMIT) and not already_autopaid_today:
        verdict.route = "auto"
        verdict.route_reason = (
            f"₹{total} within the ₹{int(AUTO_PAY_LIMIT)} per-rider-per-day auto-pay limit"
        )
    else:
        verdict.route = "approval"
        verdict.route_reason = (
            f"₹{total} exceeds the ₹{int(AUTO_PAY_LIMIT)} auto-pay limit"
            if total > int(AUTO_PAY_LIMIT)
            else "already auto-paid once today; needs ops approval"
        )
    verdict.evidence["shortfalls"] = dict(verdict.shortfalls)
    verdict.evidence["route"] = verdict.route
    return verdict
