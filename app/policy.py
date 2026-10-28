"""Pure payout-policy engine. No I/O, no floats, no wall-clock now().

Reference for time is always the message's received_at (IST). All money uses
Decimal with ROUND_HALF_UP, matching docs/policy.md.

Policy:
- Completed trip fare = 25 + 6 * max(0, km - 2). Surge multiplier applies to
  the whole fare (base + distance). Round per trip, half up.
- Daily incentive of 150 for >= 12 completed trips in an IST calendar day.
- Rider cancellations cost the rider 10 each; customer cancellations earn 0.
- Day = IST calendar day; disputes valid for the last 7 days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

IST = timezone(timedelta(hours=5, minutes=30))

BASE_FARE = Decimal(25)
PER_KM = Decimal(6)
FREE_KM = Decimal(2)
DAILY_INCENTIVE = Decimal(150)
INCENTIVE_MIN_TRIPS = 12
PENALTY = Decimal(10)
DISPUTE_WINDOW_DAYS = 7

RUPEE = Decimal(1)

TRIP_LINE = "trip"
DAILY_INCENTIVE_LINE = "daily_incentive"
CANCELLATION_PENALTY_LINE = "cancellation_penalty"

STATUS_COMPLETED = "completed"
STATUS_CANCELLED_BY_RIDER = "cancelled_by_rider"
STATUS_CANCELLED_BY_CUSTOMER = "cancelled_by_customer"


def to_rupees(value: Decimal) -> Decimal:
    """Round a money value to whole rupees, halfway cases up (policy)."""
    return value.quantize(RUPEE, rounding=ROUND_HALF_UP)


def effective_distance_km(distance_km: Decimal) -> Decimal:
    """Normalize the distance unit quirk in the trips export.

    trips.csv mixes km and meter values: a minority of rows (222 of 3304
    distinct trips) carry distances > 100 whose paid history only reconciles
    when interpreted as meters (value / 1000). See docs/DATA_AUDIT.md.
    Distances <= 2 km and beyond are read as km unchanged.
    """
    if distance_km > 100:
        return distance_km / 1000
    return distance_km


def trip_fare(distance_km: Decimal, surge_multiplier: Decimal) -> Decimal:
    """Fare (rupees) owed for one completed trip. Surge applies to the whole fare."""
    chargeable = max(Decimal(0), effective_distance_km(distance_km) - FREE_KM)
    raw = (BASE_FARE + PER_KM * chargeable) * surge_multiplier
    return to_rupees(raw)


def fare_of_no_surge(distance_km: Decimal) -> Decimal:
    """What the trip would pay without any surge multiplier."""
    return trip_fare(distance_km, Decimal(1))


def ist_day(ts: datetime) -> date:
    """IST calendar day for a timestamp (naive values treated as IST)."""
    if ts.tzinfo is None:
        return ts.astimezone(IST).date()
    return ts.astimezone(IST).date()


def parse_received_at(value: str) -> datetime:
    """Parse the vendor received_at (ISO-8601). Naive values are treated as IST."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def parse_trip_started_at(value: str) -> datetime:
    """Parse trips.csv started_at (UTC 'Z' values typical; naive treated as UTC)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def daily_incentive(n_completed: int) -> Decimal:
    """150 for >= 12 completed trips in the day, else 0."""
    return DAILY_INCENTIVE if n_completed >= INCENTIVE_MIN_TRIPS else Decimal(0)


def cancellation_penalty(status: str) -> Decimal:
    """Earn amount delta for a trip (negative for rider cancels, 0 otherwise)."""
    if status == STATUS_CANCELLED_BY_RIDER:
        return -PENALTY
    return Decimal(0)


@dataclass(frozen=True)
class TripRecord:
    trip_id: str
    rider_id: str
    status: str
    started_at: str
    distance_km: Decimal | None
    surge_multiplier: Decimal


def expected_for_trip(trip: TripRecord) -> Decimal:
    """ Rupees owed for one trip per policy, independent of what was paid."""
    if trip.status == STATUS_COMPLETED:
        if trip.distance_km is None:
            raise ValueError(f"trip {trip.trip_id}: completed but distance unknown")
        return trip_fare(trip.distance_km, trip.surge_multiplier)
    return cancellation_penalty(trip.status)


def expected_for_rider_day(trips: list[TripRecord]) -> Decimal:
    """Rupees owed for all of a rider's trips on one IST day, incl. incentive."""
    total = Decimal(0)
    completed = 0
    for trip in trips:
        total += expected_for_trip(trip)
        if trip.status == STATUS_COMPLETED:
            completed += 1
    return total + daily_incentive(completed)


def trip_ist_day(trip: TripRecord) -> date:
    return ist_day(parse_trip_started_at(trip.started_at))


def within_window(received_at: datetime, trip_day: date) -> bool:
    """True if an IST trip day falls in the last 7 calendar days vs received_at.

    Window = [received_day - (DISPUTE_WINDOW_DAYS - 1), received_day], both
    inclusive (day-granular, per the ops wiki "last 7 days").
    """
    day = ist_day(received_at)
    return (day - timedelta(days=DISPUTE_WINDOW_DAYS - 1)) <= trip_day <= day


def normalize_claimed_trip_id(text: str) -> str | None:
    """Best-effort deterministic trip id normalization from a chunk of text.

    The LLM extracts candidates; this checker keeps the canonical form
    stable for lookups (T + digits). Returns None if no plausible id.
    """
    import re

    m = re.search(r"(?i)\bt[-_ ]?(\d{1,7})\b", text)
    if m:
        return f"T{m.group(1)}"
    m = re.search(r"(?i)\border[-_ ]?(\d{1,7})\b", text)
    if m:
        return f"T{m.group(1)}"
    return None
