# Data Audit — `data/` exports (Phase 1)

Profiled with `scripts/recon_report.py` and ad-hoc pandas-free scripts. All findings below
are reproducible from `scripts/recon_report.py`.

## Shape

| File | Rows | Notes |
|---|---:|---|
| `riders.csv` | 40 | R001–R040, no nulls |
| `trips.csv` | 3314 rows | **3304 distinct trips**; 10 duplicate rows (identical copies, benign) |
| `payout_lines.csv` | 3222 rows | unique `line_id`s; no duplicates |
| `conversations.json` | 24 conversations | 39 rider turns across 23 distinct riders |

## Trips

- Statuses: `completed` 3030 (row-count; 3022 distinct), `cancelled_by_rider` 146, `cancelled_by_customer` 138.
- `started_at` is UTC (`...Z`); policy "day" is IST. Example: `T` started `19:20Z` lands on the *next* IST day. All day-window math IST via `app/policy.ist_day`/`parse_trip_started_at`.
- Dates: 12–21 Sep 2026 (UTC), i.e. 12–22 Sep IST days. No leading-zero/format oddities.
- **Distances**: min 0.8 km, max **11100**. 222 trips have `distance_km > 100`, all on riders R031–R040. Their paid history only reconciles when these values are read as **meters** (`/1000`) — see Reconciliation. 46 trips are < 1 km (paid = base ₹25).
- **Rider-id typos**: 38 trips rows carry `R7` (→`R007`) and 26 rows `r19` (→`R019`) instead of canonical ids. Normalization (`app.reconciliation.normalize_rider_id`) verified safe because canonical ids otherwise fail paid-history reconciliation for those riders.
- 10 duplicate `trip_id` rows, byte-identical duplicates (dedupe on `trip_id` is safe).
- Surge values: only `1.0` (2861 rows), `1.2` (291), `1.5` (162). No nulls; 0/unusual values absent.

## Payout lines

- Line types: `trip` 3004, `cancellation_penalty` 150, `daily_incentive` 68. Incentive lines always have a **blank `trip_id`**; penalty lines always carry the trip id.
- Amounts: penalties always `-10`, incentives always `150`, trip lines positive ₹.
- **No payout line references a non-existent trip** (orphan trip_ids: 0) and no line pays a non-completed/cancelled trip. Payout date always equals the trip's **IST** day (loop check: 3004/3004 trip lines, penalty dates all match too) — so `payout_date` does not actually lag for trip/penalty lines in this export.
- Duplicate penalty lines exist: **4 trips** have two `-10` lines each (`T429673/R001`, `T637155/R014`, `T936237/R027`, `T815454/R030`) → riders overcharged ₹10 (dispute class "penalty do baar kata").
- **300 rows of `trip` lines skipped for `trip_id` blank?** none; blank trip ids only on incentive lines.

## Cross-file

- Trips with no pay line: 16 completed trips (all 10–13 km, riders R039/R034/R016/R033/R017; the "payment nahi aaya" dispute class) — every rider-cancel has its penalty line.
- The 222 meter-scale trips: no correctness story — their paid amounts match the meter reading (e.g. 11100 → 11.1 km → ₹80, consistent with `25 + 6×(11.1−2)`).
- One **overpaid incentive**: `R036 2026-09-18` had 11 completed trips but received ₹150 (the 12-trip line exists; maybe counted a cancelled trip at source).
- Six rider-days hit ≥12 completed trips without an incentive line: R017 (Sep 14), R024 (Sep 18), R023 (Sep 20), R002 (Sep 20), R006 (Sep 21), R005 (Sep 21) — the "incentive nahi mila" dispute class (two of them, R024 and R005, appear with that exact claim in the fixtures).

## Ambiguities resolved by data evidence

1. **₹6/km: prorated vs whole km** → **prorated**. At whole-km, zero trips reconcile; prorated reconciles 2986/3004 trip lines exactly (the 18 mismatches are explained anomalies, not rounding).
2. **Surge scope** → surge multiplies base + distance (e.g. 3.5 km × 1.25 → 34 × 1.25 → 43); distance-only surge gives 36 and would not reconcile.
3. **Incentive counting: completed only** → every paid/day incentive corresponds to ≥12 *completed* trips in that IST day; the one overpaid incentive (R036 18 Sep) has 11 completed + a cancelled-by-customer trip. Also verified on R011 (10 completed, claim rejected in fixture).
4. **Distance unit** → >100 = meters (see above); this quirk must not leak into fare disagreement with paid history (meter trips are otherwise consistent with the same fare curve).

## Data traps recorded

Rider-id typos, meters-as-km distances, duplicate trip rows, duplicate penalty lines, incentive overpayment, missing payout lines (fraud-adjacent: could inflate shortfall if recomputed wrongly elsewhere), UTC/IST boundary.
