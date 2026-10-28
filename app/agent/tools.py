"""Deterministic agent tools (each call is traced). Money authorization stays here.

- lookup_trip: ownership-checked single-trip view
- list_rider_trips: rider's trips for IST day(s)
- compute_expected: pure policy wrappers
- get_paid_history: payout lines for rider/trips (our DB rows)
- check_window: 7-day dispute window vs received_at
- check_caps: auto-pay eligibility (₹200, once per rider-IST-day)

create_payout is NOT a tool: it lives in app.payments and only the flow code calls it.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from ..claims import verify_dispute
from ..db import trace
from ..payments import list_for_rider_day
from ..policy import TripRecord, expected_for_trip, parse_received_at, trip_ist_day, within_window
from ..reconciliation import PayoutLineRecord


def _trips_for_rider(conn, rider_id: str, day: str | None = None) -> list[TripRecord]:
    if day is None:
        rows = conn.execute(
            "SELECT trip_id, rider_id, status, started_at, distance_km, surge_multiplier "
            "FROM trips WHERE rider_id = %s",
            [rider_id],
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT trip_id, rider_id, status, started_at, distance_km, surge_multiplier
               FROM trips WHERE rider_id = %s
                 AND (started_at AT TIME ZONE 'Asia/Kolkata')::date = %s::date""",
            [rider_id, day],
        ).fetchall()
    return [_row_to_trip(r) for r in rows]


def trips_for(conn, rider_id: str) -> list[TripRecord]:
    """Public wrapper for other agent modules."""
    return _trips_for_rider(conn, rider_id)


def _row_to_trip(row) -> TripRecord:
    return TripRecord(
        trip_id=row["trip_id"], rider_id=row["rider_id"], status=row["status"],
        started_at=row["started_at"].isoformat() if isinstance(row["started_at"], datetime.datetime)
        else str(row["started_at"]),
        distance_km=Decimal(str(row["distance_km"])) if row["distance_km"] is not None else None,
        surge_multiplier=Decimal(str(row["surge_multiplier"])),
    )


def _trip_by_id(conn, trip_id: str) -> TripRecord | None:
    row = conn.execute(
        "SELECT trip_id, rider_id, status, started_at, distance_km, surge_multiplier "
        "FROM trips WHERE trip_id = %s",
        [trip_id],
    ).fetchone()
    return _row_to_trip(row) if row else None


def _lines_for_rider(conn, rider_id: str) -> list[PayoutLineRecord]:
    rows = conn.execute(
        "SELECT line_id, payout_date, rider_id, line_type, trip_id, amount "
        "FROM payout_lines WHERE rider_id = %s",
        [rider_id],
    ).fetchall()
    return [PayoutLineRecord(
        line_id=r["line_id"], payout_date=r["payout_date"], rider_id=r["rider_id"],
        line_type=r["line_type"], trip_id=r["trip_id"] or "", amount=Decimal(str(r["amount"])),
    ) for r in rows]


def trip_lookup_for(conn, trip_ids: list[str]) -> dict[str, TripRecord]:
    found = {}
    for tid in trip_ids:
        trip = _trip_by_id(conn, tid)
        if trip:
            found[tid] = trip
    return found


def lookup_trip(conn, rider_id: str, trip_id: str, received_at: str) -> dict:
    trip = _trip_by_id(conn, trip_id)
    detail = {"trip_id": trip_id}
    if trip is None:
        detail["exists"] = False
    elif trip.rider_id != rider_id:
        detail.update(exists=True, owned_by_rider=False, owner=trip.rider_id)
    else:
        detail.update(exists=True, owned_by_rider=True,
                      status=trip.status, expected=str(expected_for_trip(trip)),
                      ist_day=trip_ist_day(trip).isoformat(),
                      window_ok=within_window(parse_received_at(received_at),
                                              trip_ist_day(trip)))
    trace(conn, rider_id, "tool_call", "lookup_trip", {"trip_id": trip_id}, detail)
    return detail


def list_rider_trips(conn, rider_id: str, day: str, received_at: str) -> list[dict]:
    trips = _trips_for_rider(conn, rider_id, day)
    lines = _lines_for_rider(conn, rider_id)
    by_trip: dict[str, Decimal] = {}
    for l in lines:
        by_trip[l.trip_id] = by_trip.get(l.trip_id, Decimal(0)) + l.amount
    out = [{"trip_id": t.trip_id, "status": t.status, "ist_day": trip_ist_day(t).isoformat(),
            "expected": str(expected_for_trip(t)), "paid": str(by_trip.get(t.trip_id, 0))}
           for t in trips]
    trace(conn, rider_id, "tool_call", "list_rider_trips", {"day": day},
          {"count": len(out), "trips": out})
    return out


def compute_expected(conn, rider_id: str, trip_id: str) -> dict:
    trip = _trip_by_id(conn, trip_id)
    detail = {"trip_id": trip_id}
    if trip and trip.rider_id == rider_id:
        detail.update(expected=str(expected_for_trip(trip)))
    else:
        detail["error"] = "trip not found or not rider's"
    trace(conn, rider_id, "tool_call", "compute_expected", {"trip_id": trip_id}, detail)
    return detail


def get_paid_history(conn, rider_id: str) -> dict:
    rows = conn.execute(
        "SELECT payout_date, line_type, trip_id, amount FROM payout_lines "
        "WHERE rider_id = %s ORDER BY payout_date desc LIMIT 100",
        [rider_id],
    ).fetchall()
    out = [dict(r) for r in rows]
    trace(conn, rider_id, "tool_call", "get_paid_history", {"rider": rider_id},
          {"lines": len(out)})
    return {"lines": out}


def check_caps(conn, rider_id: str, received_at_ist: datetime.date) -> dict:
    today = received_at_ist.isoformat()
    paid = list_for_rider_day(conn, rider_id, today)
    confirmed_total = sum(p["amount"] for p in paid if p["state"] == "confirmed")
    pending = [p for p in paid if p["state"] in ("intent", "sent")]
    detail = {"ist_day": today, "confirmed_total": confirmed_total,
              "already_autopaid_today": bool(confirmed_total or pending)}
    trace(conn, rider_id, "tool_call", "check_caps", {"rider": rider_id}, detail)
    return detail


def verify(conn, rider_id: str, claimed_trip_ids: list[str], days: list[str],
           received_at: str, inc_days: dict[str, int] | None = None) -> dict:
    """Run claims verification for the mentioned trips and claimed days.

    rider_trips passed to claims.verify_dispute are the candidate trips whose
    shortfalls are computed: the explicitly claimed trips when given, else all
    of the rider's trips on the first claimed IST day.
    """
    received_day = received_day_of(received_at)
    rider_trips_all = _trips_for_rider(conn, rider_id)
    lines = _lines_for_rider(conn, rider_id)
    lookup = trip_lookup_for(conn, claimed_trip_ids) if claimed_trip_ids else {
        t.trip_id: t for t in rider_trips_all}
    day = days[0] if days else None
    caps = check_caps(conn, rider_id, received_day)

    if claimed_trip_ids:
        rider_trips = [lookup[t] for t in claimed_trip_ids if t in lookup]
    elif day:
        rider_trips = [t for t in rider_trips_all if trip_ist_day(t).isoformat() == day]
    else:
        rider_trips = rider_trips_all

    verdict = verify_dispute(
        wanted_rider=rider_id,
        claimed_trip_ids=claimed_trip_ids,
        rider_exists=bool(rider_trips_all) or bool(conn.execute(
            "SELECT 1 FROM payout_lines WHERE rider_id=%s LIMIT 1", [rider_id]).fetchone()),
        rider_trips=rider_trips,
        rider_lines=lines,
        trip_lookup=lookup,
        already_autopaid_today=caps["already_autopaid_today"],
        incentive_days=inc_days or {},
    )
    trace(conn, rider_id, "tool_call", "verify", {"days": days, "trips": claimed_trip_ids},
          {"state": verdict.state, "verified_amount": verdict.verified_amount,
           "shortfalls": verdict.shortfalls, "route": verdict.route,
           "reason": verdict.route_reason})
    return {"state": verdict.state, "verified_amount": verdict.verified_amount,
            "shortfalls": verdict.shortfalls, "route": verdict.route,
            "reason": verdict.route_reason,
            "window_ok": all(within_window(parse_received_at(received_at),
                                           datetime.date.fromisoformat(d))
                             for d in days) if days else True,
            "caps": caps}



IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def received_day_of(received_at: str) -> datetime.date:
    dt = parse_received_at(received_at)
    return dt.astimezone(IST_TZ).date() if dt.tzinfo else dt.date()
