# Data Dictionary — BTS On-Time Performance

This dictionary documents the columns of the **cleaned** flight-level dataset
(`data/processed/flights_clean.csv`) produced by `src/preprocessing.py`, and the
rules used to validate them. It is owned by Thrupthi G D.

The original column names and definitions were verified against the official
BTS "Fields" reference page:
<https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ>

---

## 1. Source and global conventions

- **Source:** BTS "Reporting Carrier On-Time Performance (1987-present)" table.
  <https://www.transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ>
- **Where to put raw files:** download a month (or year) from BTS as a CSV and
  place it in `data/raw/` (e.g. `data/raw/ONTIME_2024-01.csv`). Keep raw files out
  of Git (see `.gitignore`); they are large.
- **Time units:** all delay, elapsed-time, taxi, and air-time fields are in
  **minutes**. Distance is in **miles**.
- **Timezone assumption (critical):** all BTS time fields are **LOCAL airport
  time** in `hhmm` 24-hour format. They are NOT UTC and carry no timezone offset.
  The cleaned datetimes (`crs_dep_dt`, `dep_dt`, `crs_arr_dt`, `arr_dt`) are
  **timezone-naive local** datetimes. Converting them to a common timezone-aware
  reference (UTC) so flights at different airports can be compared and sorted is
  the responsibility of the aircraft-rotation module (Amulya), which needs a
  per-airport timezone map and daylight-saving handling.
- **Missing values:** blank cells in the raw CSV become `NaN`/`<NA>`. The
  conventions column below notes where missing is expected versus a data error.

---

## 2. Column-name standardization

The cleaned file uses snake_case names. The mapping from the original BTS name
to the standardized name is written to `data/processed/column_mapping.json`
every run, so every cleaned column is traceable to its original field. Older
BTS downloads (pre-2018) used different names for a few fields; these aliases
are resolved automatically before standardization:

| Old name (alias) | Current BTS name | Standardized name |
|---|---|---|
| `UniqueCarrier` | `Reporting_Airline` | `reporting_airline` |
| `TailNum` | `Tail_Number` | `tail_number` |
| `FlightNum` | `Flight_Number_Reporting_Airline` | `flight_number` |

---

## 3. Core flight-identity fields (needed to reconstruct rotations)

These are the fields the aircraft-rotation module relies on most. They are
verified during cleaning and feed the `rotation_eligible` flag.

| Original BTS field | Standardized name | Meaning & units | Type | Missing convention | Validation rule |
|---|---|---|---|---|---|
| `FlightDate` | `flight_date` | Scheduled departure date (yyyymmdd) | datetime (parsed) | never missing for a valid record | parsed to a date; unparseable -> flagged `invalid_scheduled_departure_time` |
| `Tail_Number` | `tail_number` | Aircraft registration (N-number) | string | blank if unreported | must be alphanumeric, 1–6 chars; blank/invalid -> `valid_tail_number=False` |
| `Reporting_Airline` | `reporting_airline` | Unique carrier code | string | rare | kept as-is |
| `Flight_Number_Reporting_Airline` | `flight_number` | Carrier flight number | string/numeric | rare | kept as-is |
| `Origin` | `origin` | Origin airport IATA code | string | never missing for valid record | kept as-is |
| `Dest` | `dest` | Destination airport IATA code | string | never missing for valid record | kept as-is |

---

## 4. Scheduled and actual timestamps

BTS stores times as local `hhmm` integers (e.g. `735` = 07:35, `1430` = 14:30,
`2400` = midnight at end of day). Subtracting these clock strings directly is
wrong across midnight (a flight departing 23:50 and arriving 00:30). Instead the
pipeline reconstructs datetimes from the **scheduled departure plus pre-computed
minute deltas**, which BTS guarantees are correct:

```
crs_dep_dt = flight_date + crs_dep_time          # local hhmm; 2400 -> next day
dep_dt     = crs_dep_dt + dep_delay (minutes)    # signed delay handles midnight
crs_arr_dt = crs_dep_dt + crs_elapsed_time        # minutes
arr_dt     = dep_dt + actual_elapsed_time          # minutes
```

| Original BTS field | Standardized name | Meaning & units | Missing convention |
|---|---|---|---|
| `CRSDepTime` | `crs_dep_time` | Scheduled departure, local hhmm | never missing; unparseable -> record invalid |
| `DepTime` | `dep_time` | Actual departure, local hhmm | blank when **cancelled** |
| `DepDelay` | `dep_delay` | Actual − scheduled departure, signed minutes | blank when cancelled |
| `CRSArrTime` | `crs_arr_time` | Scheduled arrival, local hhmm | never missing |
| `ArrTime` | `arr_time` | Actual arrival, local hhmm | blank when **cancelled or diverted** |
| `ArrDelay` | `arr_delay` | Actual − scheduled arrival, signed minutes | blank when cancelled or diverted |
| `CRSElapsedTime` | `crs_elapsed_time` | Scheduled gate-to-gate time, minutes | never missing |
| `ActualElapsedTime` | `actual_elapsed_time` | Actual gate-to-gate time, minutes | NULL for **all diverted flights** (per BTS) |

**Reconstructed datetime columns (added by the pipeline, all local & timezone-naive):**

| Column | Meaning | Missing when |
|---|---|---|
| `crs_dep_dt` | Scheduled departure datetime | `crs_dep_time` unparseable |
| `dep_dt` | Actual departure datetime | cancelled (no `dep_delay`) |
| `crs_arr_dt` | Scheduled arrival datetime | `crs_elapsed_time` missing |
| `arr_dt` | Actual arrival datetime | cancelled or diverted |
| `timestamp_inconsistent` | True if `dep_dt + actual_elapsed_time` disagrees with `crs_arr_dt + arr_delay` by > 1 min | a data-quality warning |

---

## 5. Cancellation / diversion indicators

| Original BTS field | Standardized name | Meaning | Values | Missing convention |
|---|---|---|---|---|
| `Cancelled` | `cancelled` | Flight cancelled | 0 / 1 | treated 0 if blank |
| `CancellationCode` | `cancellation_code` | Reason cancelled | A=carrier, B=weather, C=NAS, D=security | blank unless cancelled |
| `Diverted` | `diverted` | Flight diverted | 0 / 1 | treated 0 if blank |

For cancelled flights `dep_dt`/`arr_dt` are NaT (no actual times) but
`crs_dep_dt`/`crs_arr_dt` are still reconstructed. For diverted flights
`dep_dt` is reconstructed (the flight did depart) but `arr_dt` is NaT (it did
not arrive at the scheduled destination).

---

## 6. Delay-cause fields (supplementary)

These are self-reported by carriers, present only from June 2003, and may be
blank for many carriers/months. They are **supplementary** — the weather/feature
team (Dhanush R) uses observed NOAA weather as the primary source, not these.

| Original BTS field | Standardized name | Meaning |
|---|---|---|
| `CarrierDelay` | `carrier_delay` | Minutes of delay attributed to carrier |
| `WeatherDelay` | `weather_delay` | Minutes attributed to weather |
| `NASDelay` | `nas_delay` | Minutes attributed to National Aviation System |
| `SecurityDelay` | `security_delay` | Minutes attributed to security |
| `LateAircraftDelay` | `late_aircraft_delay` | Minutes attributed to a late inbound aircraft |

---

## 7. Other retained fields

Distance (`distance`, miles), taxi/air times and flight counts are kept and
type-coerced. Airport identifiers (`origin_airport_id`, `dest_airport_id`,
`*_seq_id`, `*_city_market_id`, `*_wac`, `*_state_fips`) are retained as
integers so the team can join external reference tables consistently across
years. The full standardized column list is in the cleaned CSV header.

---

## 8. Quality and exclusion flags (added by the pipeline)

These columns are added to `flights_clean.csv`. Records are flagged, **not
silently deleted** (except exact duplicate rows, which are removed and logged to
`data/processed/duplicate_records.csv`).

| Flag column | Meaning |
|---|---|
| `is_cancelled` | `cancelled == 1` |
| `is_diverted` | `diverted == 1` |
| `has_tail_number` | `tail_number` is non-blank |
| `valid_tail_number` | `tail_number` is alphanumeric, 1–6 chars |
| `valid_crs_dep_time` | scheduled departure time parsed successfully |
| `valid_dep_time` | `dep_dt` reconstructed (not cancelled) |
| `valid_arr_time` | `arr_dt` reconstructed (not cancelled/diverted) |
| `invalid_timestamp` | scheduled departure time could not be parsed |
| `timestamp_inconsistent` | arrival reconstruction cross-check failed |
| `implausible_record` | e.g. distance ≤ 0 or > 6000 mi, elapsed ≤ 0 or > 24 h, delay magnitude > 24 h |
| `rotation_eligible` | True when all gates pass: valid tail + valid actual dep/arr times + not cancelled + not diverted + not inconsistent + not implausible |
| `exclusion_reason` | first failing gate as a string; empty if eligible |
| `duplicate_exact` | True for exact-duplicate rows (removed in cleaning) |
| `duplicate_identity` | True for rows sharing flight-date/carrier/number/route/time keys |

---

## 9. Output files

| File | Contents |
|---|---|
| `data/processed/flights_clean.csv` | One row per retained flight, standardized columns + flags. **This is the canonical flight-level dataset.** |
| `data/processed/flights_rotation_eligible.csv` | Subset where `rotation_eligible == True` — the convenience input for the rotation module. |
| `data/processed/duplicate_records.csv` | Exact-duplicate rows that were removed (audit log). |
| `data/processed/column_mapping.json` | Original -> standardized column-name mapping used this run. |
| `results/data_quality_report.md` | Human-readable data-quality report. |
| `results/data_quality_summary.json` | Machine-readable summary (for reproducibility). |

---

## 10. Known limitations affecting the project

- Datetimes are local and timezone-naive; cross-airport comparison needs the
  rotation module's UTC normalization.
- Delay-cause fields are incomplete and self-reported — not a full weather proxy.
- Flights crossing a daylight-saving boundary may have a ~1 hour offset between
  wall-clock and true elapsed time (affects very few records).
- Scheduled airport flight volume (the congestion proxy) is added later by the
  weather/congestion module; it is not in this cleaned dataset.
