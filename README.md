# ADA-Flight-delay-propagation

**Quantifying Aircraft-Rotation Delay Propagation Under Operational and Environmental Confounding**

An ADA course project that reconstructs aircraft rotations from the BTS
On-Time Performance dataset and estimates the association between an inbound
flight's arrival delay and the departure delay of the next flight operated by
the same aircraft, while accounting for turnaround time, weather, congestion,
and other operational factors.

---

## Team and responsibility split

| Member | Responsibility |
|---|---|
| **Thrupthi G D** | Repository, BTS ingestion, initial cleaning, data dictionary, data-quality report, preprocessing tests |
| Amulya | Aircraft rotation and turnaround analysis (consecutive-flight linking, pair dataset, ground-time/slack/buffer, validation, descriptives) |
| Anagha Kaushik | Statistical modelling and recovery analysis (baseline/adjusted models, CIs, persistence/recovery, robustness, plots) |
| Dhanush R | Weather/congestion integration and airport-network analysis (NOAA/FAA joins, coverage, network metrics) |

This repository currently contains **Thrupthi's** deliverables. The other
modules (`src/aircraft_rotations.py`, `src/external_features.py`,
`src/modelling.py`, `src/network_analysis.py`) are owned by teammates.

---

## Repository structure

```text
ada-flight-delay-propagation/
├── README.md
├── requirements.txt
├── .gitignore
├── conftest.py                 # makes `import src.*` work for tests
├── data/
│   ├── raw/                    # <- place downloaded BTS CSVs here (gitignored)
│   ├── processed/              # cleaned outputs (gitignored; mapping tracked)
│   └── sample/                 # small raw samples for quick review
├── src/
│   ├── data_ingestion.py       # load + validate raw BTS CSV        (Thrupthi)
│   ├── preprocessing.py         # clean, validate, flag, report        (Thrupthi)
│   └── (aircraft_rotations.py, external_features.py, modelling.py,
│        network_analysis.py — owned by teammates)
├── tests/
│   ├── fixtures/               # small synthetic BTS-schema test data
│   ├── test_data_ingestion.py
│   └── test_preprocessing.py
├── docs/
│   └── data_dictionary.md
├── notebooks/
└── results/                    # data-quality report goes here
```

---

## Setup

```bash
# 1. Create and activate a virtual environment (optional but recommended)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate    # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt
```

Tested with Python 3.15, pandas 3.1, pytest 9.1. Any modern Python 3.10+ with
pandas >= 2.0 should work.

---

## Data placement

Download the BTS "Reporting Carrier On-Time Performance" data:

> <https://www.transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ>

Select a month (or a full year for the final study period), choose "Download"
and save the CSV into `data/raw/`, for example `data/raw/ONTIME_2024-01.csv`.
**Do not commit raw data to Git** — it is gitignored because the files are large.

The pipeline reads the first CSV it finds in `data/raw/`, or a path you pass
explicitly. It is strongly recommended to start with **one month** to confirm
everything runs before processing the full year.

---

## Commands

All commands are run from the repository root.

```bash
# 1. Load + validate raw data and write a small raw sample for review
python -m src.data_ingestion data/raw/ONTIME_2024-01.csv \
    --sample-out data/sample/raw_sample.csv --sample-n 1000

# (if no path is given, it auto-detects the first CSV in data/raw/)
python -m src.data_ingestion --nrows 50000   # read only first 50k rows

# 2. Clean the data and generate the quality report
python -m src.preprocessing data/raw/ONTIME_2024-01.csv
# outputs:
#   data/processed/flights_clean.csv
#   data/processed/flights_rotation_eligible.csv
#   data/processed/duplicate_records.csv
#   data/processed/column_mapping.json
#   results/data_quality_report.md
#   results/data_quality_summary.json

# 3. Run the test suite
python -m pytest tests/ -v
```

---

## What the cleaning step does (Thrupthi's contribution)

1. **Standardize column names** to snake_case without losing meaning; the
   original -> standardized mapping is written to `column_mapping.json`. Older
   BTS column names are handled automatically.
2. **Coerce types** explicitly (delays/elapsed/distance -> numeric, indicators ->
   0/1, flight_date -> datetime).
3. **Reconstruct consistent LOCAL timestamps** from the scheduled departure plus
   BTS's pre-computed minute deltas — this avoids the classic midnight-crossing
   error of subtracting local clock strings directly. All datetimes are local and
   timezone-naive; UTC normalization for cross-airport comparison is the rotation
   module's job.
4. **Validate and flag** tail numbers, timestamps, cancellation/diversion
   indicators, delay fields, and implausible values. Records are flagged, not
   silently deleted.
5. **Remove exact duplicate rows** (the one justified exclusion) and log them.
6. **Write a reproducible data-quality report** to `results/`.

See `docs/data_dictionary.md` for the full field list, units, missing-value
conventions, and validation rules.

---

## Key assumptions and limitations

- Times are local airport time (hhmm). Datetimes in the cleaned file are
  timezone-naive local.
- Delays and elapsed times are in minutes; distance in miles.
- `ActualElapsedTime` and `ArrDelay` are NULL for diverted flights (per BTS).
- Delay-cause fields exist only from June 2003 and are self-reported/incomplete.
- Results describe **adjusted associations**, not definitive causation.

---

## Reproducibility

The cleaning pipeline is deterministic for a given input file: running
`src/preprocessing.py` on the same raw CSV always produces the same cleaned
dataset and the same `data_quality_summary.json`. The synthetic test fixtures in
`tests/fixtures/` are clearly labelled test data, not real BTS data, and let
the tests run without the full dataset.

## Weather-enhanced delay propagation model

### Pipeline
1. Recover flight records with `recover_flights.py`.
2. Match US airports to nearby NOAA GHCNh weather stations using `match_weather_stations.py`, `find_nearest_stations.py`, and the station reference files.
3. Download hourly 2024 station observations using `download_weather.py`.
4. Clean and prepare observations using `src/preprocess_weather.py`.
5. Train and evaluate the weather-enhanced model using `src/train_weather_model.py`.

### Weather features
The weather-enhanced model adds temperature, wind speed, precipitation, relative humidity, visibility, and sea-level pressure to the core flight-delay features.

Weather observations are matched to the origin airport of the next flight. The matching uses the latest available observation at or before the incoming flight's actual arrival time, with a 120-minute lookback. Future observations are not used.

### Results

The core and weather-enhanced models were compared on the same test rows and split.

| Model | Test MAE | Test RMSE |
|---|---:|---:|
| Core model | 16.253 | 33.350 |
| Weather-enhanced model | 15.547 | 33.130 |

The weather-enhanced model improved test MAE by approximately 4.34% and RMSE by 0.66% compared with the core model.

### Reproducibility notes
- Weather data is sourced from NOAA GHCNh hourly observations for 2024.
- Raw weather downloads and the merged weather-observation dataset are generated locally and are not committed to Git.
- `match_weather_stations.py` currently expects the cleaned flights CSV at `~/Downloads/flight_data/flights_clean.csv`.
- The weather pipeline depends on the airport and NOAA station reference CSV files in the repository.
- Flight recovery and weather matching involve assumptions. In particular, recovered flight statuses are inferred from available arrival evidence and should not be interpreted as directly observed labels.
