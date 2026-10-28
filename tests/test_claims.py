"""Verified-dispute decision tests: rider claims never move money by themselves.

Hand-worked arithmetic (policy = 25 base + 6/km after 2 km, surge on whole fare, half-up):
- 5 km, surge 1.5: fare 25 + 3*6 = 43; *1.5 = 64.5 -> 65. Paid 24 -> shortfall 41.
- 40 km, no surge: 25 + 38*6 = 253. Paid 25 -> shortfall 228 -> approval route.
"""

from decimal import Decimal

from app.claims import verify_dispute
from app.policy import TripRecord
from app.reconciliation import PayoutLineRecord


def trip(tid="T1", rider="R003", km=5.0, surge=1.5, status="completed",
         started="2026-09-21T12:30:00+00:00"):
    return TripRecord(
        trip_id=tid, rider_id=rider, status=status, started_at=started,
        distance_km=Decimal(str(km)), surge_multiplier=Decimal(str(surge)),
    )


def line(lid="L1", rtype="trip", trip="T1", rider="R003", amount=40, date="2026-09-22"):
    return PayoutLineRecord(line_id=lid, payout_date=date, rider_id=rider,
                            line_type=rtype, trip_id=trip, amount=Decimal(amount))


def verdict(**kw):
    defaults = {
        "wanted_rider": "R003",
        "claimed_trip_ids": ["T1"],
        "rider_exists": True,
        "rider_trips": [trip()],
        "rider_lines": [line()],
        "trip_lookup": {"T1": trip()},
        "already_autopaid_today": False,
        "incentive_days": None,
    }
    defaults.update(kw)
    return verify_dispute(**defaults)


def test_rider_underpaid_by_surge_routes_auto():
    v = verdict(rider_lines=[line(amount=24)])
    assert v.state == "verified" and v.verified_amount == 41
    assert v.route == "auto"
    assert v.shortfalls == {"T1": "41"}


def test_rider_paid_in_full_explains_no_shortfall():
    v = verdict(rider_lines=[line(amount=65)])
    assert v.state == "no_shortfall" and v.route == "none"
    assert v.evidence["reason"], "the explanation must be there for the rider"


def test_rider_claim_of_120_or_300_is_not_trusted():
    v = verdict(rider_lines=[line(amount=24)])
    assert v.verified_amount == 41, "verified from records, never from rider claim"


def test_unknown_rider_is_never_paid():
    v = verdict(rider_exists=False)
    assert v.state == "unknown_rider" and v.verified_amount == 0 and v.route == "none"


def test_claimed_trip_missing_from_data_cannot_verify():
    v = verdict(trip_lookup={})
    assert v.state == "cannot_verify" and v.verified_amount == 0


def test_claimed_trip_owned_by_someone_else_is_rejected():
    v = verdict(trip_lookup={"T1": trip(rider="R009")}, rider_trips=[trip(rider="R009")])
    assert v.state == "foreign_trip" and v.verified_amount == 0
    # even when the foreign trip "looks underpaid" (41 vs 41), we never pay on it
    assert v.route != "auto"


def test_no_trip_claimed_checks_all_rider_shortfalls():
    v = verdict(claimed_trip_ids=[],  # no ids: rider_trips are checked directly
                rider_trips=[trip("T1"), trip("T2", km=40, surge=1.0)],
                rider_lines=[line(amount=24), line(lid="L2", trip="T2", amount=25)])
    assert v.verified_amount == 41 + 228
    assert v.shortfalls == {"T1": "41", "T2": "228"}


def test_overpay_generates_no_negative_owed():
    v = verdict(rider_lines=[line(amount=1000)])
    assert v.verified_amount == 0 and v.state == "no_shortfall"
    assert all(int(s) > 0 for s in v.shortfalls.values())


def test_amount_over_200_routes_to_approval():
    big = trip(km=40, surge=1.0)
    v = verdict(rider_trips=[big], rider_lines=[line(amount=25)],
                trip_lookup={"T1": big})
    assert v.verified_amount == 228 and v.route == "approval"


def test_repeat_auto_pay_same_day_needs_approval():
    v = verdict(rider_lines=[line(amount=24)], already_autopaid_today=True)
    assert v.state == "verified" and v.verified_amount == 41 and v.route == "approval"
    assert "already auto-paid" in v.route_reason


def test_incentive_shortfall_added_and_routes_auto():
    v = verdict(incentive_days={"2026-09-21": 13}, rider_lines=[line(amount=65)],
                claimed_trip_ids=[])
    assert v.state == "verified" and v.verified_amount == 150
    assert v.shortfalls.get("incentive:2026-09-21") == "150"
