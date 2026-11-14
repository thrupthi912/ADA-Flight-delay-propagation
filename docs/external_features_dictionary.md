# External Features Dictionary

**Owned by:** Dhanush R (Person 3)
**Dataset:** `data/processed/external_features.csv`
**Generated from:** `data/processed/congestion_features.csv` + `data/processed/weather_features.csv`
**Study period:** January, June, July, August 2024
**Total rows:** 2,319,468 (one per rotation-eligible flight)
**Total columns:** 19

---

## 1. Unique Key

| Column | Type | Description |
|---|---|---|
| `flight_id` | int64 | 0-based integer row position in `flights_rotation_eligible.csv`. Unique, sequential (0 to 2,319,467). Use this to join back to any column from the original flight dataset. |

**Join instruction for Anagha:**

```python
import pandas as pd
flights = pd.read_csv("data/processed/flights_rotation_eligible.csv").reset_index()
flights = flights.rename(columns={"index": "flight_id"})
ext = pd.read_csv("data/processed/external_features.csv")
merged = flights.merge(ext, on="flight_id", how="left")
# merged has exactly len(flights) rows — no row multiplication
```

---

## 2. Identity Columns (join context, not features)

These columns are included for human readability and join verification. They are not features — do not use them as predictors without deliberate consideration.

| Column | Type | Description |
|---|---|---|
| `flight_date` | date (YYYY-MM-DD) | Scheduled departure date in the origin airport's local calendar. Matches `flight_date` in `flights_rotation_eligible.csv`. |
| `reporting_airline` | string | IATA carrier code (e.g., `AA`, `DL`). |
| `flight_number` | int | BTS flight number. |
| `origin` | string | 3-letter IATA origin airport code. All congestion features are computed at this airport. All weather features are sourced from this airport's NOAA station. |
| `dest` | string | 3-letter IATA destination airport code. Arrival congestion features are computed at this airport. |
| `crs_dep_dt_utc` | datetime (UTC) | Scheduled departure instant in UTC. Used as the reference time for all congestion features. Constructed by Thrupthi's preprocessing pipeline as `origin_local(flight_date, crs_dep_time) → UTC`. |
| `weather_date` | date (YYYY-MM-DD) | The GSOD observation date used for weather features. Always equals `flight_date − 1 day`. See §5 for the prediction-time rationale. |

---

## 3. Congestion Features

**Source:** `data/processed/congestion_features.csv` (computed by `src/congestion_features.py`)

All congestion features are computed from **scheduled (CRS) UTC timestamps only**. No actual departure or arrival times are used. The time window is a **trailing half-open interval** `[t − W, t)` — strictly before the reference time, never including the flight itself.

**Population note:** The congestion index is built from `flights_rotation_eligible.csv` (the strict rotation-eligible cohort). For the four months covered (Jan, Jun, Jul, Aug 2024), this cohort captures **~96%** of all BTS source records. The missing ~4% is cancelled flights (~3–4%) and confirmed diverted flights (~0.3%). Cancelled flights do not depart and do not consume runway capacity, so this is a small downward bias. Features are counts of *rotation-eligible-cohort* movements, not strictly total scheduled movements.

**Prediction-time safety:** ✅ All five congestion features are safe for use as predictors. They use only published airline schedule (CRS) times, which are available before departure.

### Window convention

```
trailing window = [t − W, t)    (closed on left, open on right)
```

The flight being evaluated is never counted in its own window. Any flight scheduled at or after `t` is outside the window. No future information enters any congestion feature.

---

### `dep_count_origin_1h`

| Property | Value |
|---|---|
| Description | Number of *other* flights scheduled to depart from the same origin airport in the 1-hour trailing window before this flight's scheduled departure |
| Reference timestamp | `crs_dep_dt_utc` |
| Airport | `origin` |
| Window | `[crs_dep_dt_utc − 1 h, crs_dep_dt_utc)` |
| Unit | integer count |
| Missing values | 0 (0.000%) |
| Mean / max / P95 | 20.5 / 105 / 61 |
| Leakage status | ✅ Safe — uses scheduled times only; the flight itself is excluded |

---

### `dep_count_origin_3h`

| Property | Value |
|---|---|
| Description | Same as `dep_count_origin_1h` but over a 3-hour trailing window |
| Reference timestamp | `crs_dep_dt_utc` |
| Airport | `origin` |
| Window | `[crs_dep_dt_utc − 3 h, crs_dep_dt_utc)` |
| Unit | integer count |
| Missing values | 0 (0.000%) |
| Mean / max / P95 | 57.8 / 239 / 167 |
| Invariant | `dep_count_origin_3h` ≥ `dep_count_origin_1h` always |
| Leakage status | ✅ Safe |

---

### `arr_count_dest_1h`

| Property | Value |
|---|---|
| Description | Number of *other* flights scheduled to arrive at the destination airport in the 1-hour trailing window before this flight's scheduled arrival |
| Reference timestamp | `crs_arr_dt_utc` (scheduled arrival = `crs_dep_dt_utc + crs_elapsed_time`) |
| Airport | `dest` |
| Window | `[crs_arr_dt_utc − 1 h, crs_arr_dt_utc)` |
| Unit | integer count |
| Missing values | 0 (0.000%) |
| Mean / max / P95 | 19.7 / 103 / 60 |
| Leakage status | ✅ Safe — `crs_arr_dt_utc` is derived from scheduled times only |

---

### `arr_count_dest_3h`

| Property | Value |
|---|---|
| Description | Same as `arr_count_dest_1h` but over a 3-hour trailing window |
| Reference timestamp | `crs_arr_dt_utc` |
| Airport | `dest` |
| Window | `[crs_arr_dt_utc − 3 h, crs_arr_dt_utc)` |
| Unit | integer count |
| Missing values | 0 (0.000%) |
| Mean / max / P95 | 55.8 / 230 / 164 |
| Invariant | `arr_count_dest_3h` ≥ `arr_count_dest_1h` always |
| Leakage status | ✅ Safe |

---

### `total_traffic_origin_1h`

| Property | Value |
|---|---|
| Description | Combined scheduled movements at the origin airport in the 1-hour trailing window: departures from origin + arrivals at origin, both in `[crs_dep_dt_utc − 1 h, crs_dep_dt_utc)` |
| Reference timestamp | `crs_dep_dt_utc` |
| Airport | `origin` (for both departures and arrivals) |
| Window | `[crs_dep_dt_utc − 1 h, crs_dep_dt_utc)` |
| Formula | `dep_count_origin_1h + arrivals_at_origin_1h` |
| Unit | integer count |
| Missing values | 0 (0.000%) |
| Mean / max / P95 | 39.4 / 197 / 110 |
| Invariant | `total_traffic_origin_1h` ≥ `dep_count_origin_1h` always |
| Leakage status | ✅ Safe — arrivals at origin also use `crs_arr_dt_utc` (scheduled) |

---

## 4. Weather Features

**Source:** `data/processed/weather_features.csv` (computed by `src/weather_features.py`)
**NOAA data source:** GSOD (Global Summary of the Day), NCEI
**URL pattern:** `https://www.ncei.noaa.gov/data/global-summary-of-the-day/access/{year}/{USAF:06d}{WBAN:05d}.csv`
**Station map:** `data/weather/airport_station_map.csv`

### ⚠️ Weather Features: Retrospective Analysis Only

All weather features are labelled `_prev` (prior day) because they use the GSOD summary for `flight_date − 1`.

**Why these are retrospective, not real-time predictors:**
Per the official NCEI GSOD documentation: *"The latest daily summary data are normally available a few days after the date-time of the observations used in the daily summaries."* This means the prior-day GSOD file would typically not be in the NCEI database until ~3 days after the flight. These features are valid for retrospective research (which this study is) but **must not** be used as real-time predictors in a production system.

**What they represent:** Yesterday's ambient weather regime at the origin airport. Valid for controlling for weather conditions in a retrospective regression model.

All weather features use the GSOD daily summary for **`weather_date = flight_date − 1 day`**.

**Why prior-day, not same-day:**

GSOD daily summaries are averages and totals over the entire GMT calendar day (00:00Z–23:59Z). A same-day GSOD row for `2024-08-01` at ATL includes observations from 14:00, 18:00, and 22:00 — all of which occur after morning departures. Using same-day GSOD for a 07:00 departure would silently include post-departure observations (future information). 42% of flights in this dataset depart before noon local time.

The prior day's summary was finalised at midnight before any departure on `flight_date` and is therefore unambiguously available at any reasonable prediction time.

**Limitation:** Daily averages cannot capture within-day weather variation (morning fog, afternoon thunderstorm). Hourly ISD data would provide higher temporal precision but was not used in this version. This is a documented tradeoff — see `docs/person_3_handoff.md` for the full methodology discussion.

---

### `temp_c_prev`

| Property | Value |
|---|---|
| Description | Mean air temperature at the origin airport on `weather_date` |
| GSOD source field | `TEMP` (°F, daily mean) |
| Unit | °C (converted: `(TEMP − 32) × 5/9`) |
| Weather date | `flight_date − 1 day` |
| Missing sentinel replaced | `9999.9 → NaN` (official GSOD convention) |
| Missing values | 1,886 (0.081%) |
| Mean / min / max / P5 / P95 | 21.1 / −41.5 / 41.7 / 0.2 / 32.4 °C |
| Missing cause | Genuine GSOD observation gaps in 10 airports (verified in source files) |

---

### `dewp_c_prev`

| Property | Value |
|---|---|
| Description | Mean dew point at the origin airport on `weather_date` |
| GSOD source field | `DEWP` (°F, daily mean) |
| Unit | °C (converted: `(DEWP − 32) × 5/9`) |
| Weather date | `flight_date − 1 day` |
| Missing sentinel replaced | `9999.9 → NaN` |
| Missing values | 2,020 (0.087%) |
| Mean / min / max / P5 / P95 | 12.7 / −38.9 / 27.8 / −6.5 / 23.9 °C |

---

### `visibility_km_prev`

| Property | Value |
|---|---|
| Description | Mean visibility at the origin airport on `weather_date` |
| GSOD source field | `VISIB` (statute miles, daily mean) |
| Unit | km (converted: `VISIB × 1.60934`) |
| Weather date | `flight_date − 1 day` |
| Missing sentinel replaced | `999.9 → NaN` |
| Missing values | 1,889 (0.081%) |
| Mean / min / max / P5 / P95 | 15.3 / 0.3 / 24.6 / 11.4 / 16.1 km |
| Note | Values cluster near 16.1 km (10 statute miles); NOAA stations stop reporting above this threshold |

---

### `wind_ms_prev`

| Property | Value |
|---|---|
| Description | Mean wind speed at the origin airport on `weather_date` |
| GSOD source field | `WDSP` (knots, daily mean) |
| Unit | m/s (converted: `WDSP × 0.514444`) |
| Weather date | `flight_date − 1 day` |
| Missing sentinel replaced | `999.9 → NaN` |
| Missing values | 1,904 (0.082%) |
| Mean / min / max / P5 / P95 | 3.6 / 0.0 / 18.2 / 1.6 / 6.4 m/s |

---

### `gust_ms_prev`

| Property | Value |
|---|---|
| Description | Maximum wind gust reported at the origin airport on `weather_date` |
| GSOD source field | `GUST` (knots) |
| Unit | m/s (converted: `GUST × 0.514444`) |
| Weather date | `flight_date − 1 day` |
| Missing sentinel replaced | `999.9 → NaN` |
| Missing values | **724,562 (31.2%)** |
| Mean / min / max / P5 / P95 | 12.4 / 5.1 / 40.2 / 8.2 / 19.6 m/s (non-missing only) |

**⚠️ Important — GUST missing value interpretation:**

Per the official NCEI GSOD documentation (readme.txt):
> *"GUST — Maximum wind gust reported for the day in knots to tenths. **Missing = 999.9**"*
> *"The daily extremes and totals — maximum wind gust, precipitation amount, and snow depth — will only appear **if the station reports the data sufficiently to provide a valid value**. Therefore, these three elements will appear less frequently than other values."*

`999.9` means **not reported / insufficient data** — not "no gust occurred." A calm day with no gust may produce 999.9, but so may a day with a gust that was not reported. The two cases cannot be distinguished in GSOD.

**Do not** impute 999.9 days as zero wind gust. If a model requires a complete `gust_ms_prev` column, use it as a separate binary indicator (`gust_was_reported`) or apply model-aware imputation with explicit documentation.

---

### `prcp_mm_prev`

| Property | Value |
|---|---|
| Description | Total precipitation at the origin airport on `weather_date` |
| GSOD source field | `PRCP` (inches, daily total) |
| Unit | mm (converted: `PRCP × 25.4`) |
| Weather date | `flight_date − 1 day` |
| Missing sentinel replaced | `99.99 → NaN` |
| Missing values | 7,345 (0.317%) |
| Mean / min / max / P5 / P95 | 3.0 / 0.0 / 280.9 / 0.0 / 17.8 mm |
| Note (from NCEI docs) | `0.00` = no measurable precipitation (including trace). `99.99` = missing or insufficient reports. Many stations do not report `0` on dry days, so `99.99` often appears on genuinely dry days. This is a GSOD documentation issue, not a pipeline error. |

---

## 5. Timestamp and Timezone Summary

| Feature group | Reference field | Timezone | Calendar |
|---|---|---|---|
| Congestion | `crs_dep_dt_utc` (departure), `crs_arr_dt_utc` (arrival) | **UTC** | UTC calendar date |
| Weather | `weather_date` = `flight_date − 1` | **GMT (0000Z–2359Z)** | Local departure date − 1 |
| `flight_date` | origin local clock | Origin airport's local time | Local calendar |

**Note on GSOD timing:** The official GSOD documentation states summaries are based on GMT (0000Z–2359Z). The `flight_date` in BTS data is the local departure date at the origin airport. For flights departing in US time zones (UTC−4 to UTC−10), the prior-day GSOD summary (`flight_date − 1`) covers:
- A minimum of `flight_date − 1 00:00 UTC` to `flight_date − 1 23:59 UTC`
- This window ends before the earliest possible departure on `flight_date` in any US mainland time zone
- The prior-day lag is therefore safe across all time zones in the dataset

---

## 6. Coverage Limitations

- **4 months only:** January, June, July, August 2024. The remaining 8 months (Feb–May, Sep–Dec) are in the sensitivity cohort (`flights_sensitivity_eligible.csv`) which requires re-downloading raw BTS data.
- **343 airports covered** by the station map. All are matched via NOAA ISD.
- **10 airports with sparse weather** (FOD, HYS, LBL, LNK, MAF, MOT, MYR, PIB, RSW, TOL): genuine observation gaps in source GSOD files. These gaps affect 0.08% of flights.
- **XWA** (Williston Basin, ND): matched geographically to Watford City ASOS station, 63 km away. Weather observations at this station may not perfectly represent conditions at XWA.

---

## 7. Reproducibility

The complete external_features.csv can be regenerated by running:

```bash
# 1. Station matching (downloads isd-history.csv once, cached)
python -m scripts.build_station_map

# 2. Weather download + preprocess + features
python -m scripts.run_weather

# 3. Congestion features
python -m scripts.run_congestion

# 4. Integration (external_features.csv)
python -m scripts.build_external_features
```

All scripts use configurable paths via CLI arguments. No machine-specific paths are hardcoded. The output is deterministic for the same NOAA source data.
