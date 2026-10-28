# Reconciliation — expected vs paid, all trips/days (Phase 1)

Run: `PYTHONPATH=. uv run python scripts/recon_report.py`
Engine: `docs/DECISIONS.md` D2 boundary; implementation `app/policy.py` (pure) +
`app/reconciliation.py` (pure), fed by `app/data_loader.py`.

## Headline numbers

- 3304 distinct trips, 3222 payout lines, 40 riders.
- **Trip-level exact reconcile: 3244/3304** distinct trips; every mismatch is one of the
  categories below.
- **Rider is owed the verified shortfall** in these classes: `surge_not_applied` 17 trips,
  `missing_pay_line` 16 trips, `underpaid` 1 trip (₹32), `duplicate_penalty` 4 trips (₹10 each).
- **Paid history total (CSV)**: trip ₹? / penalties −₹1500 / incentives ₹10,200.
- Total underpaid-to-riders across the export: **₹2,698** (sum of verified shortfalls).
- Total overpaid-to-riders: **₹150** (R036's ineligible incentive line) — never a payout,
  money is only explained if a rider overclaims after being overpaid.

## Categories (counts)

| Category | Count | Meaning |
|---|---:|---|
| `surge_not_applied` | 17 | paid exactly the no-surge fare (e.g. T926334: ₹74 due, ₹49 paid) |
| `missing_pay_line` | 16 | completed trip, no line (R039 2, R034 3, R016 5, R033 3, R017 3) |
| `duplicate_penalty` | 4 | -10 charged twice for one trip (T429673, T637155, T936237, T815454) |
| `incentive_missing` | 6 | ≥12 completed trips that IST day, no incentive line |
| `incentive_overpaid` | 1 | R036 Sep 18: 11 completed, paid ₹150 |
| `underpaid` | 1 | T672899 (R026): 7.4 km ₹57 due, ₹25 paid |
| `missing_penalty` | 0 | every rider-cancel has its line |
| `paid_non_earning_trip` | 0 | no customer-cancel was ever paid |
| `duplicate_trip_rows` | 10 | byte-identical rows in trips.csv (benign, deduped) |
| overpaid trips after meter rule | **0** | meter-unit quirk resolves everything else |

## Reconciliation verification samples (hand-worked)

- `T926334` R003, 6.0 km, surge 1.5 → (25+24)×1.5 = 73.5 → **74**; paid 49 → shortfall **₹25** (fixture conv 1/5 payout).
- `T840677` R007, 9.0 km, surge 1.5 → (25+42)×1.5 = 100.5 → **101**; paid 67 → **₹34** (conv 15).
- `T312538` R031, distance "5000" (meters) → 5.0 km, surge 1.5 → **65**; paid 43 → **₹22** (conv 16).
- `T604546` R027, 10.0 km, surge 1.2 → 88; paid 73 → **₹15** (conv 2, first issue).
- `T936237` R027, 21 Sep: double penalty → **₹10** refund owed (conv 2, second issue). 15+10 split across auto-pay once/rider/day → fixture `one_of`: 15 auto + 10 approval.
- `T672899` R026, 7.4 km → 25+6×5.4 = 57.4 → **57**; paid 25 → **₹32** (conv 18).
- `T980582` R035, 6.0 km × 1.5 → 74; paid 49 → **₹25**; rider claims ₹300 → pay verified 25 only (conv 21).
- R016 19 Sep missing lines: 91+73+85+91+85 = **₹425** → approval (conv 10).
- R034 19 Sep missing lines: 91+85+73 = **₹249** → approval (conv 20).
- R033 20 Sep missing lines: 91+85 = **₹176** → auto-pay (≤200) (conv 19).
- R005 21 Sep: 13 completed, incentive missing → **₹150** (conv 6). R024 18 Sep: 12 → **₹150** (conv 13).
- R009 17 Sep: 11 completed → not owed; R025 19 Sep: 11 → not owed; R011 19 Sep: 10 → not owed (conv 17/14/3 explanations).
- `T482410` belongs to **R030** not R021 (conv 11: another rider's trip → escalate).
- `T502951` claim on 23 Sep about a 13-Sep trip → **outside 7-day window** → explain, no payout (conv 12).

## Payment-date guarantee

`payout_date` equals the trip's IST day for all 3004 trip lines and penalty lines, so
payout-day attribution uses the trip's IST day everywhere; matching to trips is still done
**by `trip_id`** (defensive, per data README note about lag).
