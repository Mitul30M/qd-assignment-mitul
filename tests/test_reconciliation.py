"""Golden tests: the policy engine reproduces expected outcomes from the fixtures."""

from __future__ import annotations

import pytest

from app.data_loader import DATA_DIR, load_payout_lines, load_trips
from app.reconciliation import DAILY_INCENTIVE, reconcile


@pytest.fixture(scope="module")
def loaded():
    trips, dup_counts = load_trips(DATA_DIR / "trips.csv")
    lines = load_payout_lines(DATA_DIR / "payout_lines.csv")
    return trips, dup_counts, lines, reconcile(trips, lines, dup_counts)


class TestGoldenClaims:
    """For each fixture conversation with a derivable payout, insist the engine matches."""

    # (rider, trip_id or rider-day, expected rupees reaching the rider, approval, window check)
    @pytest.mark.parametrize(
        "rider,trip_id,expected_payout",
        [
            ("R003", "T926334", 25),   # conv 1 & 5: surge miss, one auto payout
            ("R027", "T604546", 15),   # conv 2: surge miss part of two-problem message
            ("R007", "T840677", 34),   # conv 15
            ("R031", "T312538", 22),   # conv 16
            ("R026", "T672899", 32),   # conv 18: engine math beats rider claim
            ("R035", "T980582", 25),   # conv 21: claim Rs 300, verified shortfall only
        ],
    )
    def test_surge_and_underpaid_trip_fixtures(self, loaded, rider, trip_id, expected_payout):
        _, _, _, rec = loaded
        assert rec.per_trip[trip_id]["shortfall"] == str(expected_payout)

    def test_double_penalty_refund(self, loaded):
        _, _, _, rec = loaded
        # conv 9 R014 duplicate penalty trip: owed the extra Rs 10 back
        assert rec.per_trip["T637155"]["shortfall"] == "10"

    def test_missing_lines_approval_totals(self, loaded):
        _, _, _, rec = loaded
        # conv 10 R016 "5 orders 19 sept ka paisa nahi aaya" -> approval Rs 425
        total = sum(
            int(d["shortfall"])
            for tid, d in rec.per_trip.items()
            if d["rider_id"] == "R016" and d["category"] == "missing_pay_line"
        )
        assert total == 425
        # conv 20 R034 "3 orders 19 sept" -> approval Rs 249
        total = sum(
            int(d["shortfall"])
            for tid, d in rec.per_trip.items()
            if d["rider_id"] == "R034" and d["category"] == "missing_pay_line"
        )
        assert total == 249
        # conv 19 R033 "2 orders 20 tarikh" -> auto payout Rs 176 (<= 200 cap)
        total = sum(
            int(d["shortfall"])
            for tid, d in rec.per_trip.items()
            if d["rider_id"] == "R033" and d["category"] == "missing_pay_line"
        )
        assert total == 176

    def test_incentive_fixtures(self, loaded):
        _, _, _, rec = loaded
        rd = rec.per_rider_day
        assert rd[("R005", "2026-09-21")]["incentive_owed"] == str(DAILY_INCENTIVE)  # conv 6
        assert rd[("R024", "2026-09-18")]["incentive_owed"] == str(DAILY_INCENTIVE)  # conv 13
        # conv 14/17/3: claims that the data says no -> owed 0, nothing paid
        assert rd[("R025", "2026-09-19")]["incentive_owed"] == "0"
        assert rd[("R009", "2026-09-17")]["incentive_owed"] == "0"
        assert rd[("R011", "2026-09-19")]["incentive_owed"] == "0"

    def test_wrong_claims_feature_ok_records(self, loaded):
        _, _, _, rec = loaded
        # conv 7: claim surge on T252921 but it was fully paid
        assert rec.per_trip["T252921"]["category"] == "ok"
        # conv 11: T482410 belongs to R030, not R021 (also fully paid)
        assert rec.per_trip["T482410"]["category"] == "ok"
        assert rec.per_trip["T482410"]["rider_id"] == "R030"
        # conv 22: distance dispute T990831 fully consistent with the record
        assert rec.per_trip["T990831"]["category"] == "ok"
        # conv 23: penalty legit (rider cancel), no refund owed
        assert rec.per_trip["T849302"]["category"] == "ok"
        assert rec.per_trip["T849302"]["expected"] == "-10"

    def test_out_of_window_case(self, loaded):
        # conv 12: whiledriving T502951 shortfall is real, the claim on 23 Sep is outside window
        _, _, _, rec = loaded
        assert rec.per_trip["T502951"]["shortfall"] == "22"


class TestReconInvariants:
    def test_zero_overpaid_trips_after_meter_rule(self, loaded):
        _, _, _, rec = loaded
        assert not any(d["category"] == "overpaid" for d in rec.per_trip.values())

    def test_negative_shortfall_never_paid(self, loaded):
        # missing_penalty rows have negative shortfall: engine must not refund those
        _, _, _, rec = loaded
        neg = [
            d["shortfall"]
            for d in rec.per_trip.values()
            if d.get("shortfall", "0").startswith("-")
        ]
        assert all(int(s) < 0 for s in neg)

    def test_counts_match_audit_expectations(self, loaded):
        _, _, _, rec = loaded
        assert rec.totals["surge_not_applied"] == 17
        assert rec.totals["missing_pay_line"] == 16
        assert rec.totals["incentive_missing"] == 6
        assert rec.totals["incentive_overpaid"] == 1
        assert rec.totals["duplicate_penalty"] == 4
        assert rec.totals["underpaid"] == 1
        assert rec.totals.get("overpaid", 0) == 0
        assert rec.duplicate_trip_rows.__len__() == 10
        assert rec.total_underpaid > 0
        assert rec.total_overpaid >= 150  # R036 got an incentive with 11 completed trips
