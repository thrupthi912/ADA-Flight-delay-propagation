# Data Dictionary — BTS On-Time Performance

This dictionary documents the columns of the cleaned flight-level dataset (`data/processed/flights_clean.csv`) produced by `src/preprocessing.py`, and the rules used to validate them. It is owned by Thrupthi G D.

The original column names and definitions were verified against the official BTS Fields reference page:

https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ

---

## 1. Source and global conventions

- **Source:** BTS Reporting Carrier On-Time Performance (1987-present) table.
- **Dataset reference:** https://www.transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ
- **Raw data location:** Download monthly or yearly BTS CSV files into `data/raw/`. Raw datasets are large and should remain excluded from Git.
- **Time units:** Delay, elapsed-time, taxi-time, and air-time fields are measured in minutes. Distance is measured in miles.
- **Timezone assumption (critical):** BTS time fields are local airport clock times in `hhmm` format, not UTC. The preprocessing pipeline uses the origin airport's IANA timezone to convert scheduled departure local time to a UTC instant. BTS delay and elapsed-time durations are then used to derive related UTC instants. Airport timezone information is provided in `data/airport_timezones.csv`.
- Flights with unknown origin timezones are flagged rather than assigned an invented timezone. Ambiguous or nonexistent daylight-saving departure times are flagged and excluded from rotation eligibility.
- Original BTS clock-time fields are preserved for validation and interpretation. UTC timestamps represent instants on a common timeline and must not be interpreted as destination-local clock times.
- **Missing values:** Blank raw CSV cells may become `NaN` or `<NA>`. Missing cancellation and diversion indicators must not automatically be interpreted as zero.

---

## 2. Column-name standardization

The cleaned dataset uses snake_case column names. The mapping from original BTS names to standardized names is written to `data/processed/column_mapping.json`.

Older BTS downloads may use alternative names for some fields. The ingestion and preprocessing modules handle supported aliases.

| Old name (alias) | Current BTS name | Standardized name |
|---|---|---|
| `UniqueCarrier` | `Reporting_Airline` | `reporting_airline` |
| `TailNum` | `Tail_Number` | `tail_number` |
| `FlightNum` | `Flight_Number_Reporting_Airline` | `flight_number` |

---

## 3. Core flight-identity fields

These fields are important for reconstructing consecutive flights operated by the same aircraft.

| Original BTS field | Standardized name | Meaning | Type | Missing convention |
|---|---|---|---|---|
| `FlightDate` | `flight_date` | Scheduled departure date | Date/datetime | Required for a valid flight record |
| `Tail_Number` | `tail_number` | Aircraft registration | String | May be blank if unreported |
| `Reporting_Airline` | `reporting_airline` | Reporting carrier code | String | Usually present |
| `Flight_Number_Reporting_Airline` | `flight_number` | Carrier flight number | String/numeric | Usually present |
| `Origin` | `origin` | Origin airport IATA code | String | Required for valid chronology |
| `Dest` | `dest` | Destination airport IATA code | String | Required for valid chronology |

Tail numbers are checked for plausible formatting. Records with missing or invalid tail numbers are retained in the cleaned dataset but cannot be reliably linked into aircraft rotations.

---

## 4. Scheduled and actual timestamps

BTS stores scheduled and actual times as local airport clock values in `hhmm` format. For example, `735` represents 07:35, `1430` represents 14:30, and `2400` represents midnight at the end of the day.

The preprocessing pipeline preserves the original BTS time fields and constructs UTC instants for chronological comparison across airports.

### Intended timestamp logic

- `crs_dep_dt_utc`: Scheduled departure local date and time converted to UTC using the origin airport's timezone.
- `dep_dt_utc`: Scheduled departure UTC instant plus the signed `dep_delay` duration.
- `crs_arr_dt_utc`: Scheduled departure UTC instant plus `crs_elapsed_time`.
- `arr_dt_utc`: Actual departure UTC instant plus `actual_elapsed_time`.

These derived UTC timestamps are used to compare flights across time zones and sort flights chronologically. The raw `crs_arr_time` and `arr_time` fields remain available for independent consistency checks against the reconstructed arrival instant.

The timestamp names above describe the intended UTC schema; the exact names and availability should be confirmed against the actual columns produced by `src/preprocessing.py`.

### Important source fields

| Original BTS field | Standardized name | Meaning | Missing convention |
|---|---|---|---|
| `CRSDepTime` | `crs_dep_time` | Scheduled departure, local `hhmm` | Normally present |
| `DepTime` | `dep_time` | Actual departure, local `hhmm` | Blank when cancelled |
| `DepDelay` | `dep_delay` | Actual minus scheduled departure, signed minutes | Blank when cancelled |
| `CRSArrTime` | `crs_arr_time` | Scheduled arrival, local `hhmm` | Normally present |
| `ArrTime` | `arr_time` | Actual arrival, local `hhmm` | Blank when cancelled or diverted |
| `ArrDelay` | `arr_delay` | Actual minus scheduled arrival, signed minutes | Blank when cancelled or diverted |
| `CRSElapsedTime` | `crs_elapsed_time` | Scheduled gate-to-gate time, minutes | Normally present |
| `ActualElapsedTime` | `actual_elapsed_time` | Actual gate-to-gate time, minutes | Missing for diverted flights under BTS conventions |

### Timestamp validation

The pipeline checks reconstructed timestamps against independent raw BTS fields. It identifies departure-time inconsistencies, arrival-time inconsistencies, and mismatches between the reconstructed arrival instant and the raw destination-local arrival time.

Records with unknown origin timezones or problematic daylight-saving departure times are flagged. They are not silently deleted.

---

## 5. Cancellation and diversion indicators

| Original BTS field | Standardized name | Meaning | Values | Missing convention |
|---|---|---|---|---|
| `Cancelled` | `cancelled` | Whether the flight was cancelled | 0 / 1 | Missing values are flagged, not assumed to be zero |
| `CancellationCode` | `cancellation_code` | Reason for cancellation | A=carrier, B=weather, C=NAS, D=security | Blank unless cancelled |
| `Diverted` | `diverted` | Whether the flight was diverted | 0 / 1 | Missing values are flagged, not assumed to be zero |

For cancelled flights, actual departure and arrival instants are unavailable. Scheduled timestamps may still be available if the necessary inputs and timezone information are valid.

For diverted flights, the aircraft departed, but an actual arrival instant at the scheduled destination may not be available or derivable from the standard BTS elapsed-time fields.

Cancelled and diverted records are retained in the cleaned dataset. Their eligibility for aircraft-rotation analysis is determined by validation flags.

---

## 6. Delay-cause fields

Delay-cause fields are supplementary. They are self-reported, may be incomplete, and are available only for certain reporting periods.

| Original BTS field | Standardized name | Meaning |
|---|---|---|
| `CarrierDelay` | `carrier_delay` | Minutes attributed to the carrier |
| `WeatherDelay` | `weather_delay` | Minutes attributed to weather |
| `NASDelay` | `nas_delay` | Minutes attributed to the National Aviation System |
| `SecurityDelay` | `security_delay` | Minutes attributed to security |
| `LateAircraftDelay` | `late_aircraft_delay` | Minutes attributed to a late aircraft |

These fields are not a substitute for observed weather data. The weather and congestion analysis should use suitable external weather observations as the primary weather source.

---

## 7. Other retained fields

Distance (`distance`, in miles), taxi times, air times, elapsed times, flight counts, and available airport identifiers are retained and type-coerced where appropriate.

Airport identifiers such as `origin_airport_id`, `dest_airport_id`, sequence IDs, city-market IDs, state FIPS codes, and World Area Codes can support joins with external reference datasets.

The full standardized column list is available in the generated cleaned CSV header and `data/processed/column_mapping.json`.

---

## 8. Quality and exclusion flags

The preprocessing pipeline adds quality and eligibility flags to the cleaned dataset. Records are flagged rather than silently deleted, except for exact duplicate rows, which are removed and logged to `data/processed/duplicate_records.csv`.

The following table describes the intended meanings. The exact flag names and definitions must match the current implementation in `src/preprocessing.py`.

| Flag column | Meaning |
|---|---|
| `is_cancelled` | Indicates `cancelled == 1` |
| `is_diverted` | Indicates `diverted == 1` |
| `has_tail_number` | Indicates that `tail_number` is non-blank |
| `valid_tail_number` | Indicates that the tail number passes formatting checks |
| `valid_crs_dep_time` | Indicates that scheduled departure time was parsed successfully |
| `valid_dep_time` | Indicates that a valid actual departure timestamp is available |
| `valid_arr_time` | Indicates that a valid actual arrival timestamp is available |
| `invalid_timestamp` | Indicates an invalid or unparseable required timestamp |
| `timestamp_inconsistent` | Indicates a timestamp consistency warning; specific flags should be consulted when available |
| `implausible_record` | Indicates an implausible value, such as non-positive distance or elapsed time, excessive elapsed time, or an extreme delay |
| `rotation_eligible` | Indicates that a record passes the required checks for aircraft-rotation analysis |
| `exclusion_reason` | Records the first applicable reason a flight is not eligible; empty when eligible |
| `duplicate_exact` | Identifies an exact duplicate row |
| `duplicate_identity` | Identifies records sharing a possible flight-identity key |

Rotation eligibility generally requires a usable aircraft identifier, valid chronology, suitable actual departure and arrival information, valid cancellation/diversion indicators, and no disqualifying inconsistency or implausible value.

Downstream analyses should use the actual flag definitions in the implementation rather than relying only on this descriptive table.

---

## 9. Output files

| File | Contents |
|---|---|
| `data/processed/flights_clean.csv` | Canonical flight-level dataset containing retained records, standardized fields, and quality flags |
| `data/processed/flights_rotation_eligible.csv` | Subset of records eligible for aircraft-rotation analysis |
| `data/processed/duplicate_records.csv` | Audit log of exact duplicate records removed during cleaning |
| `data/processed/column_mapping.json` | Original-to-standardized column mapping for the current run |
| `results/data_quality_report.md` | Human-readable data-quality report |
| `results/data_quality_summary.json` | Machine-readable data-quality summary |

Raw BTS files should remain in `data/raw/` and should not be committed to Git.

---

## 10. Known limitations affecting the project

- **Timezone coverage:** UTC conversion depends on the airport timezone map. Flights with unknown origin timezones are flagged rather than assigned an invented timezone.
- **Daylight-saving time:** Ambiguous or nonexistent local departure times are flagged and excluded from rotation eligibility. The preprocessing implementation documents its handling of these edge cases.
- **Arrival consistency:** Reconstructed arrival instants are checked against raw destination-local arrival clock fields. Mismatches require review and should not be ignored automatically.
- **Delay-cause fields:** These fields are incomplete and self-reported; they are not a complete measure of weather effects.
- **Congestion:** Scheduled airport flight volume is added later by the weather/congestion module and is not part of this initial cleaned dataset.
- **Observational interpretation:** The cleaned data support analysis of adjusted associations between inbound delays and subsequent departure delays. They do not, by themselves, establish causation.
