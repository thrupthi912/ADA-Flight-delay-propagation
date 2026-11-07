# Person 3 → Person 4 Handoff Document

**From:** Dhanush R (Person 3) — Weather/Congestion Integration
**To:** Anagha Kaushik (Person 4) — Statistical Modelling and Recovery Analysis
**Date:** November 7, 2026
**Branch:** `feature/person-3-external-features`

---

## 1. What Was Delivered

Person 3's deliverables are complete and ready for modelling. The primary output is a single, analysis-ready external features dataset:

| File | Description | Size | Rows | Cols |
|---|---|---|---|---|
| `data/processed/external_features.csv` | **Primary deliverable** — 11 features (5 congestion + 6 weather) per flight | 388 MB | 2,319,468 | 19 |
| `data/processed/congestion_features.csv` | Congestion features only (preserved separately) | 245 MB | 2,319,468 | 13 |
| `data/processed/weather_features.csv` | Weather features only (preserved separately) | 317 MB | 2,319,468 | 14 |
| `data/weather/airport_station_map.csv` | Airport → NOAA station mapping (343 airports) | 31 KB | 343 | 10 |
| `data/weather/weather_obs.csv` | Raw GSOD observations (343 stations × 2 years) | 26 MB | 249,698 | 9 |

---

## 2. How to Use the Dataset

### 2a. Joining to the flight dataset

```python
import pandas as pd

# Load flight dataset (Thrupthi's output)
flights = pd.read_csv("data/processed/flights_rotation_eligible.csv")
flights = flights.reset_index()                      # creates 'index' column 0..N-1
flights = flights.rename(columns={"index": "flight_id"})

# Load external features
ext = pd.read_csv("data/processed/external_features.csv")

# 1:1 left join — preserves all 2,319,468 flights
merged = flights.merge(ext, on="flight_id", how="left")

assert len(merged) == len(flights), "Row multiplication — something is wrong"
assert merged["flight_id"].duplicated().sum() == 0
```

### 2b. Key join column

```
flight_id  — 0-based integer row position in flights_rotation_eligible.csv
             Range: 0 to 2,319,467, sequential, no gaps
```

### 2c. Columns available after join

After merging, `merged` will contain all 93 original flight columns plus:

**Congestion (5 features):**
- `dep_count_origin_1h` — departures from origin in trailing 1h before scheduled departure
- `dep_count_origin_3h` — departures from origin in trailing 3h
- `arr_count_dest_1h` — arrivals at destination in trailing 1h before scheduled arrival
- `arr_count_dest_3h` — arrivals at destination in trailing 3h
- `total_traffic_origin_1h` — total movements (dep + arr) at origin in trailing 1h

**Weather (6 features):**
- `temp_c_prev` — mean temperature at origin airport, prior day (°C)
- `dewp_c_prev` — mean dew point, prior day (°C)
- `visibility_km_prev` — mean visibility, prior day (km)
- `wind_ms_prev` — mean wind speed, prior day (m/s)
- `gust_ms_prev` — max wind gust, prior day (m/s) — **31% missing, see §4**
- `prcp_mm_prev` — total precipitation, prior day (mm)

**Context columns:**
- `weather_date` — the GSOD observation date used (= `flight_date − 1 day`)

---

## 3. Flight Coverage Limitation

**Only 4 of 12 months are available in `flights_rotation_eligible.csv`:**

| Month | Flights |
|---|---|
| January 2024 | 518,378 |
| June 2024 | 594,973 |
| July 2024 | 607,467 |
| August 2024 | 598,650 |
| **Total** | **2,319,468** |

The other 8 months (February–May, September–December) are in `flights_sensitivity_eligible.csv` which is not on disk. Those months were excluded from the BTS strict rotation-eligible cohort because the `DIVERTED` column is absent in the source data.

**Implication for modelling:** Summer months (June–August) account for 78% of the dataset. January is the only winter month. Seasonal analysis is possible but limited. Winter weather effects are underrepresented. Treat cross-month generalisations with caution.

---

## 4. ⚠️ Critical: Weather Methodology Deviation

### What the original plan said

Person 3's implementation plan specified:
> *"weather observations at or before actual arrival with a 120-minute lookback"*

This described hourly NOAA ISD observations linked to the actual arrival time (`arr_dt_utc`).

### What was implemented instead

**Prior-day GSOD daily summaries.** Weather features reflect the full-day averages/totals for `flight_date − 1 day` at the origin airport.

### Why the change was necessary

**The original specification had a fundamental prediction-time problem.** The research question is: *does an inbound flight's arrival delay cause the subsequent outbound departure delay?*

At the time of predicting the outbound flight's delay (just before departure), we do not know:
- The actual arrival time of the inbound aircraft
- Post-departure weather conditions on the same day

`arr_dt_utc` (actual arrival) is computed as `dep_dt_utc + actual_elapsed_time`. Both quantities embed the actual delay outcome. Using them as weather reference times would create a subtle leakage path: the reference time shifts with the delay, and the observations selected shift accordingly.

The prior-day daily summary avoids this entirely. Yesterday's GSOD record was finalised at midnight before any departure today.

### What you need to decide

**This is a methodological decision that affects the interpretation of your model.**

The weather features as implemented represent *yesterday's weather regime* — a proxy for the ambient weather conditions in which today's operations occur. They capture:
- Whether the previous day was cold/warm, wet/dry, calm/windy
- Whether conditions were likely to have caused pre-positioning issues (crew, aircraft, equipment)

They do **not** capture:
- The precise weather at departure time (use hourly ISD for that — more work)
- Whether a specific flight was affected by a weather event (use `weather_delay` from BTS if available)

**If your model requires same-day weather precision**, this dataset is not suitable as-is. In that case, discuss with the team whether to use the BTS `weather_delay` field (self-reported, incomplete) or commission an ISD hourly extraction for a future version.

**If the prior-day regime interpretation is acceptable** (which it is for a baseline adjustment model), proceed with these features and note the lag in your methods section.

### Summary of tradeoffs

| Property | Prior-day GSOD (implemented) | Hourly ISD at arrival (original plan) |
|---|---|---|
| Leakage risk | ✅ None — prior day is always available | ⚠️ Uses actual arrival time, which embeds delay outcome |
| Temporal precision | Daily average | Hourly (last 2h before arrival) |
| Completeness | 99.9% (except gust) | Unknown — ISD availability varies |
| Implementation effort | Completed | ~2–3 days additional work |
| Suitable for baseline models | ✅ Yes | Depends on leakage handling |

---

## 5. GUST Feature: Important Usage Guidance

`gust_ms_prev` is **31.2% null** (724,562 rows). This is not a pipeline error.

Per the official NCEI GSOD documentation:
> *"GUST — Missing = 999.9. The daily extremes will only appear if the station reports the data sufficiently to provide a valid value."*

`NaN` in `gust_ms_prev` means the gust was **not reported**, not that no gust occurred. A calm day and a day with an unreported gust both produce `NaN`.

**Do not impute as zero.** Recommended options:
1. Drop the column and use `wind_ms_prev` as a wind proxy (99.9% complete)
2. Add a binary feature: `gust_reported = gust_ms_prev.notna().astype(int)`
3. Apply explicit imputation and document it in your methods section

---

## 6. Precipitation Feature: Usage Note

`prcp_mm_prev` is 0.317% null. However, the GSOD documentation notes:
> *"Many stations do not report `0` on days with no precipitation, therefore `99.99` will often appear on these days."*

We convert `99.99 → NaN` per the official sentinel rule. The 7,345 null values include both genuinely missing observations **and** dry days at stations that do not report zero. If precipitation completeness matters for your model, consider treating `prcp_mm_prev = 0` and `prcp_mm_prev = NaN` differently, or use a binary rain indicator (`prcp_mm_prev > 0`).

---

## 7. Reproducibility Commands

```bash
# Activate virtual environment
source .venv/bin/activate

# Check station map (already built — re-run only if map needs updating)
python -m scripts.build_station_map

# Re-download all weather data (343 stations × 2 years; ~22 MB total; uses cache)
python -m scripts.run_weather --skip-download   # uses existing raw files
# or full re-download:
python -m scripts.run_weather

# Rebuild congestion features
python -m scripts.run_congestion

# Rebuild external_features.csv
python -m scripts.build_external_features

# Run all tests
python -m pytest tests/ -v
```

All commands are idempotent. Re-running with existing raw files skips network downloads.

---

## 8. Data Dictionary

See `docs/external_features_dictionary.md` for complete field-level documentation including:
- Exact units and conversion formulas
- Missing-value conventions and sentinels
- Window boundaries for every congestion feature
- Prediction-time safety analysis per feature
- GSOD official definitions

---

## 9. Excluded Columns — Why

The following columns from `congestion_features.csv` are **not** in `external_features.csv`:
- `crs_arr_dt_utc` — scheduled arrival timestamp. This is an input to the congestion computation but is not itself a feature. If you need it, join from the flight dataset.

The following columns from `weather_features.csv` are **not** in `external_features.csv`:
- Duplicate identity columns (`flight_date`, `origin`, etc.) — already present from the congestion join

The original flight dataset columns (`dep_delay`, `arr_delay`, `tail_number`, etc.) are intentionally absent from this file. Join on `flight_id` to access them.

---

## 10. Version Information

| Component | Version / Commit |
|---|---|
| Branch | `feature/person-3-external-features` |
| Source flight data | `data/processed/flights_rotation_eligible.csv` (Git LFS, commit `ae1c81a`) |
| NOAA GSOD download | November 7, 2026 |
| NOAA ISD catalog | November 7, 2026 |
| Python | 3.14.6 |
| pandas | 3.1.1 |
| numpy | 2.5.4 |
