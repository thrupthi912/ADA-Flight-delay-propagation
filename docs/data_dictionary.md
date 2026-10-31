# Data Dictionary — ADA Flight Delay Propagation (2024)

Owned by Thrupthi G D. Documents the standardized fields in the cleaned BTS On-Time Performance dataset.

## Source

U.S. Bureau of Transportation Statistics (BTS), "Reporting Carrier On-Time Performance (1987-present)",
2024 monthly downloads from https://www.transtats.bts.gov/

Raw files: `data/raw/<Month>/T_ONTIME_REPORTING.csv` (12 monthly files).
Raw column names use UPPERCASE_UNDERSCORE format (e.g. `FL_DATE`, `OP_UNIQUE_CARRIER`).
The pipeline standardizes these to snake_case (e.g. `flight_date`, `reporting_airline`).

## Date Format in Raw Files

The `FL_DATE` column in all 2024 BTS monthly files uses the format `M/D/YYYY 12:00:00 AM`
(e.g. `2/1/2024 12:00:00 AM` for February 1, 2024). The pipeline parses this correctly
and stores it as a date-only value in `flight_date`.

## Fields

### Identity / Time Period

| Field | Raw BTS Name | Type | Required | Description |
|-------|-------------|------|----------|-------------|
| `flight_date` | `FL_DATE` | date | Yes | Scheduled departure date. Raw format: `M/D/YYYY 12:00:00 AM`. |
| `year` | `YEAR` | int | No | Calendar year (derived from flight_date). |
| `month` | `MONTH` | int | No | Calendar month (1-12). |
| `day_of_month` | `DAY_OF_MONTH` | int | No | Day of month (1-31). |
| `day_of_week` | `DAY_OF_WEEK` | int | No | Day of week (1=Monday, 7=Sunday). |

### Airline / Aircraft

| Field | Raw BTS Name | Type | Required | Description |
|-------|-------------|------|----------|-------------|
| `reporting_airline` | `OP_UNIQUE_CARRIER` | string | Yes | IATA carrier code (e.g. `AA`, `DL`). |
| `tail_number` | `TAIL_NUM` | string | Yes | Aircraft registration (N-number). Used as aircraft identity key. |
| `flight_number` | `OP_CARRIER_FL_NUM` | int | No | Flight number. |

### Origin Airport

| Field | Raw BTS Name | Type | Required | Description |
|-------|-------------|------|----------|-------------|
| `origin` | `ORIGIN` | string | Yes | 3-letter IATA airport code. |
| `origin_city_name` | `ORIGIN_CITY_NAME` | string | No | City and state (e.g. `New York, NY`). |
| `origin_state` | `ORIGIN_STATE_ABR` | string | No | 2-letter state abbreviation. |

### Destination Airport

| Field | Raw BTS Name | Type | Required | Description |
|-------|-------------|------|----------|-------------|
| `dest` | `DEST` | string | Yes | 3-letter IATA airport code. |
| `dest_city_name` | `DEST_CITY_NAME` | string | No | City and state. |
| `dest_state` | `DEST_STATE_ABR` | string | No | 2-letter state abbreviation. |

### Departure Performance

| Field | Raw BTS Name | Type | Units | Required | Description |
|-------|-------------|------|-------|----------|-------------|
| `crs_dep_time` | `CRS_DEP_TIME` | string | hhmm | Yes | Scheduled departure time, origin-local clock, 24h hhmm. |
| `dep_time` | `DEP_TIME` | string | hhmm | Yes | Actual departure time, origin-local clock, 24h hhmm. Blank for cancelled flights. |
| `dep_delay` | `DEP_DELAY` | float | minutes | Yes | Departure delay (actual - scheduled). Negative = early. |
| `taxi_out` | `TAXI_OUT` | float | minutes | No | Gate-to-wheels-off taxi time. |
| `wheels_off` | `WHEELS_OFF` | string | hhmm | No | Wheels-off time, origin-local. **January only** in 2024 data. |

### Arrival Performance

| Field | Raw BTS Name | Type | Units | Required | Description |
|-------|-------------|------|-------|----------|-------------|
| `crs_arr_time` | `CRS_ARR_TIME` | string | hhmm | Yes | Scheduled arrival time, destination-local clock, 24h hhmm. |
| `arr_time` | `ARR_TIME` | string | hhmm | Yes | Actual arrival time, destination-local clock. Blank for cancelled/diverted. |
| `arr_delay` | `ARR_DELAY` | float | minutes | Yes | Arrival delay. Null for diverted/cancelled flights. |
| `taxi_in` | `TAXI_IN` | float | minutes | No | Wheels-on-to-gate taxi time. |
| `wheels_on` | `WHEELS_ON` | string | hhmm | No | Wheels-on time, destination-local. **January only** in 2024 data. |

### Cancellation and Diversion

| Field | Raw BTS Name | Type | Required | Description | Missing-value interpretation |
|-------|-------------|------|----------|-------------|-----------------------------|
| `cancelled` | `CANCELLED` | Int64 | Yes | 0 = not cancelled, 1 = cancelled. Raw values are float strings (`"0.00"`, `"1.00"`), coerced to Int64. | NA = unknown (treated as not cancelled for cleaning, but flagged). |
| `cancellation_code` | `CANCELLATION_CODE` | string | No | BTS cancellation reason code (A/B/C/D). Blank for non-cancelled flights. | NA = not cancelled or unknown. |
| `diverted` | `DIVERTED` | Int64 | **No — optional** | 0 = not diverted, 1 = diverted. **Absent from Feb, Mar, Apr, May, Sep, Oct, Nov, Dec.** When absent, filled with NA. | NA = **unknown** (never assumed to be 0). |

### Flight Summary

| Field | Raw BTS Name | Type | Units | Required | Description |
|-------|-------------|------|-------|----------|-------------|
| `crs_elapsed_time` | `CRS_ELAPSED_TIME` | float | minutes | Yes | Scheduled gate-to-gate elapsed time. |
| `actual_elapsed_time` | `ACTUAL_ELAPSED_TIME` | float | minutes | Yes | Actual gate-to-gate elapsed time. Null for cancelled/diverted. |
| `air_time` | `AIR_TIME` | float | minutes | No | Wheels-off to wheels-on duration. |
| `distance` | `DISTANCE` | float | miles | No | Great-circle distance between airports. **Absent from January** in 2024 data. |

### Delay Cause Breakdown

| Field | Raw BTS Name | Type | Units | Description |
|-------|-------------|------|-------|-------------|
| `carrier_delay` | `CARRIER_DELAY` | float | minutes | Minutes attributed to carrier operations. |
| `weather_delay` | `WEATHER_DELAY` | float | minutes | Minutes attributed to weather. |
| `nas_delay` | `NAS_DELAY` | float | minutes | Minutes attributed to National Aviation System. |
| `security_delay` | `SECURITY_DELAY` | float | minutes | Minutes attributed to security. |
| `late_aircraft_delay` | `LATE_AIRCRAFT_DELAY` | float | minutes | Minutes attributed to a late incoming aircraft. |

### Derived Timestamps (UTC)

All local clock times are converted to UTC instants using IANA timezone data (source: OurAirports via `data/airport_timezones.csv`). Departure times are interpreted in the **origin** airport's timezone. Arrival instants are derived from UTC departure + actual elapsed time (not from destination-local clock times — these are kept separate to avoid cross-timezone errors).

| Field | Type | Description |
|-------|------|-------------|
| `crs_dep_dt_local` | datetime (naive) | Scheduled departure, origin-local naive datetime (reference only; not comparable across airports). |
| `dep_dt_local` | datetime (naive) | Actual departure, origin-local naive datetime. |
| `origin_tz` | string | IANA timezone of origin airport (e.g. `America/New_York`). Null if unknown. |
| `dest_tz` | string | IANA timezone of destination airport. Null if unknown. |
| `tz_origin_known` | bool | True if origin airport timezone was found in the map. |
| `tz_dest_known` | bool | True if destination airport timezone was found in the map. |
| `crs_dep_dt_utc` | datetime (UTC) | Scheduled departure instant in UTC. |
| `dep_dt_utc` | datetime (UTC) | Actual departure instant in UTC. |
| `crs_arr_dt_utc` | datetime (UTC) | Scheduled arrival instant in UTC (= crs_dep_dt_utc + crs_elapsed_time). |
| `arr_dt_utc` | datetime (UTC) | Actual arrival instant in UTC (= dep_dt_utc + actual_elapsed_time). |
| `has_utc_timestamps` | bool | True if dep_dt_utc is non-null. |
| `crs_dep_dst_status` | string | DST classification of scheduled departure: `ok`, `ambiguous`, `nonexistent`, `no_tz`, `na`. |
| `dep_dst_status` | string | DST classification of actual departure (same codes). |

### Consistency Checks

| Field | Type | Description |
|-------|------|-------------|
| `dep_time_inconsistent` | bool | True if raw dep_time disagrees with crs_dep_time + dep_delay (>1 min). |
| `arr_time_inconsistent` | bool | True if raw arr_time disagrees with crs_arr_time + arr_delay (>1 min). |
| `arrival_elapsed_inconsistent` | bool | True if UTC arrival back-projected to dest-local time disagrees with raw arr_time (>3 min). |

### Quality Flags

| Field | Type | Description |
|-------|------|-------------|
| `is_cancelled` | bool (nullable) | True if cancelled=1. NA if cancelled indicator is missing. |
| `is_diverted` | bool (nullable) | True if diverted=1. **NA if diverted column was absent from the source month** (not the same as False). |
| `has_tail_number` | bool | True if tail_number is non-null. |
| `valid_tail_number` | bool | True if tail_number is alphanumeric, 1-6 chars. |
| `valid_crs_dep_time` | bool | True if scheduled departure datetime was reconstructed successfully. |
| `invalid_timestamp` | bool | True if scheduled departure is invalid. |
| `implausible_record` | bool | True if any numeric value is outside plausible bounds. |

### Cohort Membership

| Field | Type | Description |
|-------|------|-------------|
| `rotation_eligible` | bool | **Strict cohort.** True only for flights confirmed non-cancelled AND confirmed non-diverted (is_diverted=False), with valid UTC timestamps, consistent times, and a valid tail number. Flights with unknown diversion status (is_diverted=NA) are excluded. |
| `sensitivity_eligible` | bool | **Sensitivity cohort.** True for rotation_eligible flights PLUS operationally complete flights where diversion status is unknown (is_diverted=NA). These are labeled explicitly; never treat them as confirmed non-diverted. |
| `exclusion_reason` | string | Human-readable reason the flight is not rotation-eligible (empty if rotation_eligible=True). |

### Duplicate Detection

| Field | Type | Description |
|-------|------|-------------|
| `duplicate_exact` | bool | True if this row is an exact duplicate of an earlier row. Exact duplicates are removed. |
| `duplicate_identity` | bool | True if another row shares the same (flight_date, airline, flight_number, origin, dest, crs_dep_time). Identity duplicates are flagged but kept. |

## Missing Value Conventions

- Blank strings in raw BTS files are loaded as `NaN`/`NA`.
- `diverted=NA` means **unknown**, never assumed to be 0 (not diverted).
- `cancelled=NA` means **unknown**, treated as a data quality flag.
- Numeric fields with BTS missing-value sentinels are coerced to NaN.
- Airport timezone lookup failures produce `origin_tz=None` and `tz_origin_known=False`; no timezone is invented.

## Known Schema Variations (2024 BTS Data)

| Column | Months Present | Months Absent |
|--------|---------------|---------------|
| `DIVERTED` | January, June, July, August | February, March, April, May, September, October, November, December |
| `WHEELS_OFF`, `WHEELS_ON` | January | All others |
| `DISTANCE`, `DISTANCE_GROUP` | February–December | January |

## Timestamp Derivation Model

```
crs_dep_dt_utc = local_to_utc(flight_date + crs_dep_time, origin_tz)
dep_dt_utc     = crs_dep_dt_utc + dep_delay (minutes)
crs_arr_dt_utc = crs_dep_dt_utc + crs_elapsed_time (minutes)
arr_dt_utc     = dep_dt_utc + actual_elapsed_time (minutes)
```

The destination-local arrival clock time (`arr_time`) is preserved as raw hhmm and is NOT derived from UTC arithmetic — this avoids timezone errors for cross-airport comparisons.
