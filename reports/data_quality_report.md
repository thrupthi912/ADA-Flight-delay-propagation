# Data Quality Report

## Dataset Overview

- **Study period**: 2024 (12 months)
- **Source**: U.S. Bureau of Transportation Statistics (BTS) On-Time Performance
- **Total source rows**: 7,079,081
- **Total cleaned rows**: 7,079,081
- **Total exact duplicates removed**: 0
- **Total cancelled flights**: 96,315
- **Total confirmed diverted**: 7,807
- **Total unknown diversion status**: 4,667,040
- **Strict rotation-eligible**: 2,319,468
- **Sensitivity cohort (unknown diversion)**: 6,889,577

## Schema Variations by Month

| Month | DIVERTED | WHEELS_OFF/ON | DISTANCE |
|-------|----------|---------------|----------|
| April | No | No | Yes |
| August | Yes | No | Yes |
| December | No | No | Yes |
| February | No | No | Yes |
| January | Yes | Yes | No |
| July | Yes | No | Yes |
| June | Yes | No | Yes |
| March | No | No | Yes |
| May | No | No | Yes |
| November | No | No | Yes |
| October | No | No | Yes |
| September | No | No | Yes |

## Monthly Row Counts

| Month | Source Rows | Cleaned Rows | Duplicates | Cancelled | Confirmed Diverted | Unknown Diversion | Strict Rotation | Sensitivity |
|-------|-------------|--------------|------------|-----------|-------------------|-------------------|-----------------|-------------||
| April | 582,205 | 582,205 | 0 | 4,035 | 0 | 582,205 | 0 | 570,207 |
| August | 619,025 | 619,025 | 0 | 12,714 | 2,008 | 0 | 598,650 | 598,650 |
| December | 590,581 | 590,581 | 0 | 4,151 | 0 | 590,581 | 0 | 577,329 |
| February | 519,221 | 519,221 | 0 | 3,002 | 0 | 519,221 | 0 | 508,852 |
| January | 547,271 | 547,271 | 0 | 20,389 | 1,512 | 0 | 518,378 | 518,378 |
| July | 634,613 | 634,613 | 0 | 18,372 | 2,303 | 0 | 607,467 | 607,467 |
| June | 611,132 | 611,132 | 0 | 7,876 | 1,984 | 0 | 594,973 | 594,973 |
| March | 591,767 | 591,767 | 0 | 5,120 | 0 | 591,767 | 0 | 577,827 |
| May | 609,743 | 609,743 | 0 | 8,244 | 0 | 609,743 | 0 | 592,667 |
| November | 575,404 | 575,404 | 0 | 2,629 | 0 | 575,404 | 0 | 565,815 |
| October | 615,497 | 615,497 | 0 | 6,284 | 0 | 615,497 | 0 | 603,588 |
| September | 582,622 | 582,622 | 0 | 3,499 | 0 | 582,622 | 0 | 573,824 |

## DIVERTED Column Availability

The BTS On-Time Performance download for 2024 does not include the DIVERTED
column in all months. Based on inspection of the raw files:

- **DIVERTED present**: January, June, July, August
- **DIVERTED absent**: February, March, April, May, September, October, November, December

For months without DIVERTED, all flights are assigned unknown diversion status.
These flights appear in the sensitivity cohort but NOT in the strict rotation-eligible cohort.
Downstream analysis teams must decide which cohort to use.

## Timestamp Reconstruction

BTS stores times as LOCAL airport clock times (hhmm format).
This pipeline converts them to UTC instants using IANA timezone data from OurAirports.

- Departure times are interpreted in the origin airport's timezone
- Arrival instants (UTC) are derived from UTC departure + actual elapsed time
- Destination-local arrival clock time (raw arr_time) is preserved separately
- Overnight flights are handled correctly via UTC arithmetic
- DST ambiguous/nonexistent timestamps are detected and flagged

## Known Limitations

1. **DIVERTED absent from 8 months**: Cannot distinguish diverted vs non-diverted flights
   for Feb, Mar, Apr, May, Sep, Oct, Nov, Dec. Use sensitivity cohort with caution.
2. **All timestamps are local clock times**: UTC conversion requires the airport timezone map.
   Airports not in the map produce local-only timestamps (flagged `tz_origin_known=False`).
3. **DISTANCE absent from January**: Distance-based analysis requires special handling for January.
4. **WHEELS_OFF/ON only in January**: Ground time analysis is limited to January.
5. **This is an observational study**: No causal claims about delay propagation are supported by the data alone.

*Report generated from pipeline run on 2026-10-31.*
