# External Features — Data Quality and Coverage Report

**Owned by:** Dhanush R (Person 3)
**Dataset:** `data/processed/external_features.csv`
**Generated:** November 7, 2026
**Study period:** January, June, July, August 2024

---

## 1. Dataset Overview

| Metric | Value |
|---|---|
| Total rows | **2,319,468** |
| Total columns | **19** |
| Unique `flight_id` values | **2,319,468** (no duplicates) |
| `flight_id` range | 0 – 2,319,467 (sequential, no gaps) |
| Source flight dataset | `data/processed/flights_rotation_eligible.csv` |
| Join cardinality | 1:1 confirmed — left join on `flight_id`, zero row multiplication |
| File size | 388.3 MB |

---

## 2. Construction Method

`external_features.csv` is a **1:1 left join** of:
- `data/processed/congestion_features.csv` (left table)
- `data/processed/weather_features.csv` (right table)

Join key: `flight_id`

The join was validated at every 200,000-row chunk:
- Sequential `flight_id` order preserved throughout
- `weather_date + 1 day == flight_date` confirmed for all 2,319,468 rows
- Zero identity mismatches across both source files

The original congestion and weather files are **preserved unchanged**.

---

## 3. Row Count Reconciliation

| Dataset | Rows | Matches source |
|---|---|---|
| `flights_rotation_eligible.csv` | 2,319,468 | — |
| `congestion_features.csv` | 2,319,468 | ✅ |
| `weather_features.csv` | 2,319,468 | ✅ |
| **`external_features.csv`** | **2,319,468** | ✅ |

---

## 4. Monthly Coverage

| Month | Flights | % of total |
|---|---|---|
| January 2024 | 518,378 | 22.3% |
| June 2024 | 594,973 | 25.6% |
| July 2024 | 607,467 | 26.2% |
| August 2024 | 598,650 | 25.8% |
| **Total** | **2,319,468** | **100%** |

**⚠️ Coverage limitation:** Only the strict rotation-eligible cohort is included. The other 8 months (February–May, September–December) are in `flights_sensitivity_eligible.csv` which is not on disk. Regenerating requires re-downloading 12 months of raw BTS data and rerunning Thrupthi's pipeline.

---

## 5. Airport Coverage

- **Origin airports:** 335 unique IATA codes
- **Destination airports:** 343 unique IATA codes (includes 8 US-territory airports absent from the timezone CSV)
- **Station map coverage:** 343/343 airports matched (334 ICAO primary, 8 extended ICAO, 1 geographic)

---

## 6. Congestion Feature Completeness

All five congestion features are complete for 100% of flights.

| Feature | Missing | % Missing | Verified non-negative | 3h ≥ 1h invariant |
|---|---|---|---|---|
| `dep_count_origin_1h` | 0 | 0.000% | ✅ | — |
| `dep_count_origin_3h` | 0 | 0.000% | ✅ | ✅ |
| `arr_count_dest_1h` | 0 | 0.000% | ✅ | — |
| `arr_count_dest_3h` | 0 | 0.000% | ✅ | ✅ |
| `total_traffic_origin_1h` | 0 | 0.000% | ✅ | ≥ dep_count ✅ |

---

## 7. Weather Feature Completeness

### Overall

| Feature | Missing | % Missing | Source |
|---|---|---|---|
| `temp_c_prev` | 1,886 | 0.081% | Genuine GSOD gaps |
| `dewp_c_prev` | 2,020 | 0.087% | Genuine GSOD gaps |
| `visibility_km_prev` | 1,889 | 0.081% | Genuine GSOD gaps |
| `wind_ms_prev` | 1,904 | 0.082% | Genuine GSOD gaps |
| `gust_ms_prev` | **724,562** | **31.238%** | See §8 below |
| `prcp_mm_prev` | 7,345 | 0.317% | Genuine GSOD gaps + reporting convention |

Flights missing ALL weather features: **1,886** (0.081%) — same 10 airports, specific date gaps.

### By month

| Month | miss `temp_c_prev` | miss `gust_ms_prev` | miss `prcp_mm_prev` |
|---|---|---|---|
| January | 1,583 (0.31%) | 222,440 (42.9%) | 3,190 (0.62%) |
| June | 20 (0.003%) | 128,227 (21.6%) | 820 (0.14%) |
| July | 177 (0.03%) | 180,887 (29.8%) | 2,283 (0.38%) |
| August | 106 (0.02%) | 193,008 (32.2%) | 1,052 (0.18%) |

Higher January missingness reflects increased station outages during winter.

### Airports with sparse weather (temp_c_prev)

These 10 airports have genuine gaps in their GSOD source files (verified in raw downloads):

| Airport | Missing flights | Cause |
|---|---|---|
| FOD (Fort Dodge, IA) | subset | GSOD source gaps |
| HYS (Hays, KS) | subset | GSOD source gaps |
| LBL (Liberal, KS) | 38 | 47 missing days in 2024 source |
| LNK (Lincoln, NE) | subset | GSOD source gaps |
| MAF (Midland, TX) | subset | GSOD source gaps |
| MOT (Minot, ND) | subset | GSOD source gaps |
| MYR (Myrtle Beach, SC) | subset | GSOD source gaps |
| PIB (Laurel/Hattiesburg, MS) | subset | GSOD source gaps |
| RSW (Fort Myers, FL) | subset | GSOD source gaps |
| TOL (Toledo, OH) | subset | GSOD source gaps |

These gaps are **not pipeline errors**. The raw GSOD source files contain no observation for those dates. No imputation has been performed.

---

## 8. GSOD GUST Field — Official Definition and Missing Value Handling

**Official NCEI GSOD documentation (readme.txt) states:**

> *"GUST — Maximum wind gust reported for the day in knots to tenths. Missing = 999.9"*
>
> *"The daily extremes and totals — maximum wind gust, precipitation amount, and snow depth — will only appear if the station reports the data sufficiently to provide a valid value. Therefore, these three elements will appear less frequently than other values."*

**Interpretation:** `999.9` in the raw GUST field means **not reported / insufficient data**. It does **not** specifically mean "no gust occurred." A calm day may produce 999.9 just as a day with an unreported gust would. The two cases cannot be distinguished from GSOD alone.

**Pipeline handling:** `GUST == 999.9` is replaced with `NaN` in `gust_ms_prev`. No value is imputed. The column is 31.2% null in the final dataset.

**Recommendation for Anagha:** Do not treat `gust_ms_prev = NaN` as zero wind gust. Options:
1. Use `gust_ms_prev.notna().astype(int)` as a binary "gust was reported" indicator.
2. Use `wind_ms_prev` (which is 99.9% complete) as a wind intensity proxy.
3. Apply explicit model-aware imputation and document it separately.

---

## 9. Feature Statistics

### Congestion

| Feature | Mean | P5 | Median | P95 | Max |
|---|---|---|---|---|---|
| `dep_count_origin_1h` | 20.5 | 0 | 16 | 61 | 105 |
| `dep_count_origin_3h` | 57.8 | 1 | 46 | 167 | 239 |
| `arr_count_dest_1h` | 19.7 | 0 | 14 | 60 | 103 |
| `arr_count_dest_3h` | 55.8 | 1 | 43 | 164 | 230 |
| `total_traffic_origin_1h` | 39.4 | 1 | 32 | 110 | 197 |

### Weather (non-missing rows only)

| Feature | Unit | Mean | P5 | Median | P95 | Max |
|---|---|---|---|---|---|---|
| `temp_c_prev` | °C | 21.1 | 0.2 | 23.2 | 32.4 | 41.7 |
| `dewp_c_prev` | °C | 12.7 | −6.5 | 15.0 | 23.9 | 27.8 |
| `visibility_km_prev` | km | 15.3 | 11.4 | 16.1 | 16.1 | 24.6 |
| `wind_ms_prev` | m/s | 3.6 | 1.6 | 3.3 | 6.4 | 18.2 |
| `gust_ms_prev` | m/s | 12.4 | 8.2 | 11.8 | 19.6 | 40.2 |
| `prcp_mm_prev` | mm | 3.0 | 0.0 | 0.0 | 17.8 | 280.9 |

---

## 10. Leakage Assessment

### Congestion features
- Reference timestamps: `crs_dep_dt_utc` and `crs_arr_dt_utc` — both **scheduled** (CRS) times, not actual
- Trailing window is open on the right — the target flight never counts itself
- Actual departure delay (`dep_delay`, `arr_delay`) is **not** used anywhere
- Verified by test `test_features_use_only_scheduled_times` in `tests/test_congestion_features.py`

### Weather features
- Reference: `weather_date = flight_date − 1 day` — yesterday's GSOD, not today's
- GSOD summaries are GMT-based and finalized at midnight
- `dep_dt_utc` and `arr_dt_utc` (actual times) are **not read** by the weather pipeline
- The prior-day lag ensures the summary was complete before any departure on `flight_date`
- Verified by test `test_features_use_only_prior_day_not_same_day` in `tests/test_weather_features.py`

### Identity columns
- `crs_dep_dt_utc` is the scheduled departure — available before any flight departs
- `flight_date` and `origin` are known before departure
- No actual delay, actual elapsed time, or post-departure information enters the feature table

---

## 11. Methodology Deviation Notice

The original Person 3 implementation plan specified:
> *"weather observations at or before actual arrival with a 120-minute lookback"*

The implemented approach uses **prior-day GSOD daily summaries** instead.

**Reason for change:** The original specification used `arr_dt_utc` (actual arrival time) as the weather reference. This is problematic for two reasons:
1. At the time of predicting the outbound departure delay, the actual arrival of the inbound flight is not yet known
2. `arr_dt_utc` is derived from `dep_dt_utc + actual_elapsed_time` — both of which embed the actual delay outcome

The prior-day lag avoids both issues and is appropriate for the project's research question (does yesterday's weather/congestion regime predict today's departure delay?).

**This change requires acknowledgement by Anagha before she uses these features in modelling.** See `docs/person_3_handoff.md` for the full discussion.

---

## 12. Duplicate and Integrity Checks

| Check | Result |
|---|---|
| Duplicate `flight_id` in external_features | 0 |
| Duplicate `flight_id` in congestion_features | 0 |
| Duplicate `flight_id` in weather_features | 0 |
| `weather_date = flight_date − 1` for all rows | ✅ True |
| Identity columns consistent across sources | ✅ True (verified over all 2.3M rows) |
| Unexpected feature column overlap | ✅ None |
| Feature values in expected physical ranges | ✅ (see §9) |
| Negative congestion counts | 0 |
| Negative wind or visibility | 0 |
