# Data-Quality Report

**Source file:** `C:\Users\thrup\OneDrive\Desktop\Sem 5\ADA\ADA-Flight-delay-propagation\data\raw\On_Time_Reporting_Carrier_On_Time_Performance_(1987_present)_2024_1.csv`

Generated automatically by `src/preprocessing.py`; reproducible from the raw input.

## Shape

- Raw rows: 547,271  |  columns: 110
- Cleaned rows: 547,271  |  columns: 139
- Exact duplicate rows removed: 0
- Rotation-eligible rows: 518,378

## Records flagged (retained, not deleted)

- Cancelled: 20,389
- Diverted: 1,512
- Missing cancellation indicator: 0
- Missing diversion indicator: 0
- Missing/invalid tail number: 5,293
- Invalid scheduled departure time: 0
- Missing origin timezone: 7,059
- DST-transition departures (ambiguous/nonexistent): 0
- Implausible values: 69

## Timestamp consistency (non-circular checks)

- Dep time vs crs_dep+dep_delay mismatch: 0
- Arr time vs crs_arr+arr_delay mismatch: 1
- Arrival instant vs raw arr_time mismatch: 41
- Rows with UTC timestamps: 520,488

## Missing values for important fields

| Field | Before | After |
|---|---:|---:|
| tail_number | 5293 | 5293 |
| crs_dep_time | 0 | 0 |
| dep_time | 19784 | 19784 |
| dep_delay | 19858 | 19858 |
| crs_arr_time | 0 | 0 |
| arr_time | 20633 | 20633 |
| arr_delay | 21901 | 21901 |
| cancelled | 0 | 0 |
| diverted | 0 | 0 |
| crs_elapsed_time | 0 | 0 |
| actual_elapsed_time | 21901 | 21901 |
| distance | 0 | 0 |

## Exclusion reasons (retained records)

| Reason | Count |
|---|---:|
| cancelled | 20,389 |
| missing_origin_timezone | 6,886 |
| diverted | 1,512 |
| implausible_value | 65 |
| inconsistent_arrival_instant | 40 |
| inconsistent_arrival_time | 1 |

## Limitations

- Departure UTC instants come from the ORIGIN timezone; arrival UTC instants come from departure instant + elapsed time (a duration), so they are correct across time zones. The destination-local arrival CLOCK remains the raw `arr_time` field.
- UTC conversion needs the airport's IANA timezone. Airports not in `data/airport_timezones.csv` are flagged `missing_origin_timezone` and not converted (no timezone is invented). Extend the map with `scripts/build_airport_timezones.py`.
- Departures that fall in a daylight-saving gap (nonexistent) or overlap (ambiguous) get a best-effort UTC (fold=0), are flagged, and are excluded from `rotation_eligible` (kept, not dropped).
- Delay-cause fields exist only from June 2003 and are self-reported/incomplete; they are supplementary, not a weather proxy.
- Scheduled airport flight volume (the congestion proxy) is added later by the weather/congestion module.
