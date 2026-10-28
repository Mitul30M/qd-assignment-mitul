"""Policy engine unit tests: rounding, day boundaries, incentives, penalties, window."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.policy import (
    DISPUTE_WINDOW_DAYS,
    IST,
    TripRecord,
    cancellation_penalty,
    daily_incentive,
    effective_distance_km,
    expected_for_rider_day,
    expected_for_trip,
    ist_day,
    normalize_claimed_trip_id,
    parse_received_at,
    parse_trip_started_at,
    to_rupees,
    trip_fare,
    within_window,
)


def fare_str(km: str, surge: str):
    return trip_fare(Decimal(km), Decimal(surge))


class TestRounding:
    def test_canonical_case_3_5km_25_surge(self):
        # 25 + 9 = 34; x1.25 = 42.5 -> 43 (banker's rounding would give 42)
        assert fare_str("3.5", "1.25") == Decimal(43)

    def test_exactly_two_km_base_only(self):
        assert fare_str("2", "1.0") == Decimal(25)

    def test_lt_two_km_base_only(self):
        assert fare_str("0.8", "1.0") == Decimal(25)

    def test_half_up_rounds_half_cent_up(self):
        assert to_rupees(Decimal("0.5")) == Decimal(1)
        assert to_rupees(Decimal("1.5")) == Decimal(2)
        assert to_rupees(Decimal("42.5")) == Decimal(43)
        assert to_rupees(Decimal("43.5")) == Decimal(44)
        assert to_rupees(Decimal("42.4")) == Decimal(42)
        assert to_rupees(Decimal("-0.5")) == Decimal(-1)  # half-up away from zero

    def test_to_rupees_no_floats(self):
        # 0.145 * 1 = .145 -> 0; float round() would be banker's
        assert to_rupees(Decimal("2.5")) == Decimal(3)


class TestFare:
    def test_10_7km_no_surge(self):
        assert fare_str("10.7", "1.0") == Decimal(77)  # 25 + 6*8.7 = 77.2 -> 77

    def test_6km_15_surge(self):
        assert fare_str("6.0", "1.5") == Decimal(74)  # (25+24)*1.5=73.5 -> 74

    def test_surge_applies_to_whole_fare(self):
        # 5.0 km, 1.2 -> (25+18)*1.2 = 51.6 -> 52 (not 18*1.2=21.6 dist-only -> 47/46)
        assert fare_str("5.0", "1.2") == Decimal(52)

    def test_distance_below_free_km(self):
        assert fare_str("1.6", "1.2") == Decimal(30)  # 25*1.2

    def test_zero_distance(self):
        assert fare_str("0", "1.0") == Decimal(25)

    def test_effective_distance_meters(self):
        # export quirk: >100 values are meters
        assert effective_distance_km(Decimal(2100)) == Decimal("2.1")
        assert effective_distance_km(Decimal("26.5")) == Decimal("26.5")


class TestIncentive:
    def test_11_trips_no_incentive(self):
        assert daily_incentive(11) == Decimal(0)

    def test_12_trips_incentive(self):
        assert daily_incentive(12) == Decimal(150)

    def test_13_trips_incentive_once(self):
        assert daily_incentive(13) == Decimal(150)


class TestPenalty:
    def test_rider_cancel_pay_10(self):
        assert cancellation_penalty("cancelled_by_rider") == Decimal(-10)

    def test_customer_cancel_earn_nothing(self):
        assert cancellation_penalty("cancelled_by_customer") == Decimal(0)

    def test_port_completed_no_penalty(self):
        assert cancellation_penalty("completed") == Decimal(0)


class TestExpected:
    def _trip(self, **kw):
        base = {
            "trip_id": "T1",
            "rider_id": "R001",
            "status": "completed",
            "started_at": "2026-09-20T10:00:00Z",
            "distance_km": Decimal(6),
            "surge_multiplier": Decimal("1.5"),
        }
        base.update(kw)
        return TripRecord(**base)

    def test_completed_fare(self):
        assert expected_for_trip(self._trip()) == Decimal(74)

    def test_rider_cancel(self):
        assert expected_for_trip(self._trip(status="cancelled_by_rider")) == Decimal(-10)

    def test_customer_cancel(self):
        assert expected_for_trip(self._trip(status="cancelled_by_customer")) == Decimal(0)

    def test_rider_day_with_incentive(self):
        trips = [self._trip() for _ in range(12)]
        assert expected_for_rider_day(trips) == Decimal(74) * 12 + 150

    def test_rider_day_with_penalties(self):
        trips = [self._trip(status="cancelled_by_rider", surge_multiplier=Decimal(1)) for _ in range(2)]
        assert expected_for_rider_day(trips) == -20


class TestISTDay:
    def test_utc_to_ist(self):
        dt = datetime(2026, 9, 20, 20, 0, tzinfo=UTC)  # 20 Sep 20:00 UTC
        d = ist_day(dt)
        assert d == date(2026, 9, 21)  # 01:30 IST on 21 Sep

    def test_2350_ist_same_day(self):
        dt = datetime(2026, 9, 20, 23, 50, tzinfo=IST)
        assert ist_day(dt) == date(2026, 9, 20)

    def test_0030_ist_next_day(self):
        dt = datetime(2026, 9, 20, 19, 0, tzinfo=UTC)  # 00:30 IST 21 Sep
        assert ist_day(dt) == date(2026, 9, 21)

    def test_trip_csv_utc_parse(self):
        dt = parse_trip_started_at("2026-09-12T19:20:00Z")  # 00:50 IST 13 Sep
        assert ist_day(dt) == date(2026, 9, 13)

    def test_received_at_naive_assumed_ist(self):
        assert ist_day(parse_received_at("2026-09-22T09:05:00")) == date(2026, 9, 22)


class TestWindow:
    def test_same_day_in_window(self):
        at = parse_received_at("2026-09-22T09:00:00+05:30")
        assert within_window(at, date(2026, 9, 22))

    def test_7th_day_inclusive(self):
        at = parse_received_at("2026-09-22T09:00:00+05:30")
        assert within_window(at, date(2026, 9, 16))
        assert DISPUTE_WINDOW_DAYS == 7

    def test_8th_day_out(self):
        at = parse_received_at("2026-09-22T09:00:00+05:30")
        assert not within_window(at, date(2026, 9, 15))

    def test_future_day_in_window(self):
        at = parse_received_at("2026-09-22T09:00:00+05:30")
        assert within_window(at, date(2026, 9, 21))


def test_trip_id_normalization():
    assert normalize_claimed_trip_id("order T926334 ka surge") == "T926334"
    assert normalize_claimed_trip_id("t88213") == "T88213"
    assert normalize_claimed_trip_id("T-88213") == "T88213"
    assert normalize_claimed_trip_id("order 88213") == "T88213"
    assert normalize_claimed_trip_id("no id here") is None
