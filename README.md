# ADA Flight Delay Propagation — Thrupthi G D's Data Pipeline

**Quantifying Aircraft-Rotation Delay Propagation Under Operational and Environmental Confounding**

An ADA course project that reconstructs aircraft rotations from the BTS On-Time Performance
dataset and estimates the association between an inbound flight's arrival delay and the
departure delay of the next flight operated by the same aircraft, while accounting for
turnaround time, weather, congestion, and other operational factors.

---

## Team and responsibility split

| Member | Responsibility |
|--------|----------------|
| **Thrupthi G D** | Repository, BTS ingestion, initial cleaning, data dictionary, data-quality report, preprocessing tests |
| Amulya | Aircraft rotation and turnaround analysis (consecutive-flight linking, pair dataset, ground-time/slack/buffer, validation, descriptives) |
| Anagha Kaushik | Statistical modelling and recovery analysis (baseline/adjusted models, CIs, persistence/recovery, robustness, plots) |
| Dhanush R | Weather/congestion integration and airport-network analysis (NOAA/FAA joins, coverage, network metrics) |

This repository currently contains **Thrupthi's** deliverables. The other
modules (`src/aircraft_rotations.py`, `src/external_features.py`,
`src/modelling.py`, `src/network_analysis.py`) are owned by teammates.

---

## Dataset source

U.S. Bureau of Transportation Statistics (BTS), "Reporting Carrier On-Time Performance (1987-present)",
2024 monthly downloads from <https://www.transtats.bts.gov/>

12 monthly raw files, one per calendar month of 2024.

---

## Directory structure

```
ADA-Flight-delay-propagation/
├── README.md
├── requirements.txt
├── .gitignore
├── conftest.py
├── data/
│   ├── raw/
│   │   └── <Month>/T_ONTIME_REPORTING.csv   # 12 monthly BTS files (gitignored)
│   ├── airport_timezones.csv               # 2036 US airports with IANA timezones
│   └── processed/
│       ├── flights_clean.csv                 # all 12 months combined, cleaned
│       ├── flights_rotation_eligible.csv     # strict rotation cohort
│       ├── flights_sensitivity_eligible.csv  # sensitivity cohort (unknown diversion)
│       ├── duplicate_records.csv
│       ├── column_mapping.json
│       └── monthly/
│           ├── <Month>_clean.csv             # per-month cleaned
│           └── <Month>_rotation_eligible.csv
├── reports/
│   ├── data_quality_report.md
│   └── data_quality_summary.json
├── src/
│   ├── data_ingestion.py     # load and validate raw BTS CSVs
│   ├── preprocessing.py      # clean, standardize, timestamp reconstruction
│   └── airport_timezones.py  # airport IANA timezone lookup
├── scripts/
│   ├── run_pipeline.py       # full 12-month pipeline
│   └── __init__.py
├── tests/
│   ├── test_data_ingestion.py
│   ├── test_preprocessing.py
│   └── fixtures/
└── docs/
    └── data_dictionary.md
```

---

## Setup

```bash
pip install -r requirements.txt
```

Tested with Python 3.15, pandas 3.1, pytest 9.1. Any modern Python 3.10+ with pandas >= 2.0 should work.

---

## How to run the full pipeline

```bash
python -m scripts.run_pipeline
```

Processes all 12 months in `data/raw/` and writes cleaned outputs to `data/processed/`
and reports to `reports/`.

---

## How to run a single month (for debugging)

```bash
python -m src.data_ingestion data/raw/January/T_ONTIME_REPORTING.csv
```

---

## How to run tests

```bash
python -m pytest tests/ -v
```

---

## Key limitations

- **DIVERTED absent from 8 of 12 months**: February, March, April, May, September, October,
  November, and December do not include the DIVERTED column in the BTS download. Flights
  from those months cannot be confirmed non-diverted and therefore appear only in the
  sensitivity cohort, not the strict rotation-eligible cohort.
- **All BTS times are local clock times**: UTC conversion requires the airport timezone lookup
  (`data/airport_timezones.csv`). Airports not in the map produce local-only timestamps,
  flagged as `tz_origin_known=False`.
- **DISTANCE absent from January**: Distance-based analysis requires special handling for January.
- **WHEELS_OFF/ON available in January only**: Ground time analysis via wheels timestamps is
  limited to January.
- **This is an observational study**: No causal claims about delay propagation are supported
  by the data alone.

---

## Schema variations by month

| Month | DIVERTED | WHEELS_OFF/ON | DISTANCE |
|-------|----------|---------------|----------|
| January | Yes | Yes | No |
| February | No | No | Yes |
| March | No | No | Yes |
| April | No | No | Yes |
| May | No | No | Yes |
| June | Yes | No | Yes |
| July | Yes | No | Yes |
| August | Yes | No | Yes |
| September | No | No | Yes |
| October | No | No | Yes |
| November | No | No | Yes |
| December | No | No | Yes |

---

## What the cleaning step does

1. **Standardize column names** to snake_case; the original → standardized mapping is written to `column_mapping.json`.
2. **Coerce types** explicitly (delays/elapsed/distance → numeric, indicators → 0/1, flight_date → datetime).
3. **Reconstruct UTC timestamps** by interpreting scheduled departure clock times in the origin airport's timezone
   and converting to UTC. Arrival UTC is derived from UTC departure + actual elapsed time.
4. **Validate and flag** tail numbers, timestamps, cancellation/diversion indicators, delay fields, and
   implausible values. Records are flagged, not silently deleted.
5. **Separate rotation cohorts**: strict cohort (confirmed non-diverted) vs sensitivity cohort (unknown diversion status).
6. **Remove exact duplicate rows** and log them.
7. **Write a reproducible data-quality report** to `reports/`.

See `docs/data_dictionary.md` for the full field list, units, missing-value conventions, and validation rules.

---

## Reproducibility

The cleaning pipeline is deterministic for a given input file: running `scripts/run_pipeline.py`
on the same raw CSVs always produces the same cleaned dataset and the same `reports/data_quality_summary.json`.
The synthetic test fixtures in `tests/fixtures/` let the tests run without the full dataset.
