
# ADA-Flight-delay-propagation

**Quantifying Aircraft-Rotation Delay Propagation Under Operational and Environmental Confounding**

An ADA course project using the BTS On-Time Performance dataset to study the association between an inbound flight’s arrival delay and the departure delay of the next flight operated by the same aircraft. The project considers turnaround time, weather, congestion, and other operational factors. Results are observational and should be interpreted as adjusted associations, not definitive causal effects.

## Team and responsibilities

| Member | Responsibility |
|---|---|
| **Thrupthi G D** | Repository setup, BTS ingestion, initial cleaning and preprocessing, data dictionary, data-quality report, preprocessing tests |
| Amulya | Aircraft-rotation reconstruction and turnaround analysis, consecutive-flight linking, pair dataset, ground-time/slack/buffer analysis, validation and descriptives |
| Anagha Kaushik | Statistical modelling and recovery analysis, baseline and adjusted models, confidence intervals, persistence/recovery, robustness checks and plots |
| Dhanush R | Weather/congestion integration and airport-network analysis, NOAA/FAA joins, coverage checks and network metrics |

This repository contains Thrupthi’s ingestion and preprocessing contribution. The other analysis modules are being developed by the respective teammates.

## Repository structure

```text
ADA-Flight-delay-propagation/
├── README.md
├── requirements.txt
├── .gitignore
├── conftest.py
├── data/
│   ├── raw/                 # Downloaded BTS CSVs; not committed
│   ├── processed/           # Generated cleaned and eligible datasets
│   └── sample/              # Small sample data for review
├── src/
│   ├── data_ingestion.py
│   └── preprocessing.py
├── scripts/
│   └── run_pipeline.py      # Full-year pipeline
├── tests/
│   ├── fixtures/
│   ├── test_data_ingestion.py
│   └── test_preprocessing.py
├── docs/
│   └── data_dictionary.md
└── reports/
    ├── data_quality_report.md
    └── data_quality_summary.json
```

## Setup

Run commands from the repository root.

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

Use Python 3.10 or newer, subject to the versions supported by the packages in `requirements.txt`.

## Data

Download the BTS **Reporting Carrier On-Time Performance** data from the [BTS TranStats table](https://www.transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ).

Place monthly CSV files in `data/raw/`, for example:

```text
data/raw/ONTIME_2024-01.csv
```

Raw data is not committed to Git because of its size.

## Run the pipeline

To process a single month:

```bash
python -m src.data_ingestion data/raw/ONTIME_2024-01.csv
python -m src.preprocessing data/raw/ONTIME_2024-01.csv
```

To run the full 2024 pipeline:

```bash
python scripts/run_pipeline.py
```

The pipeline reads the available monthly BTS files, applies preprocessing, and writes outputs under `data/processed/` and `reports/`. See the script’s help or configuration for available options.

### Main outputs

- `data/processed/flights_clean.csv` — cleaned flight records.
- `data/processed/flights_rotation_eligible.csv` — strict cohort requiring confirmed diversion status.
- `data/processed/flights_sensitivity_eligible.csv` — broader sensitivity cohort that includes records with unknown diversion status.
- `data/processed/duplicate_records.csv` — exact duplicate records identified during cleaning.
- `data/processed/column_mapping.json` — source-to-standardized column mapping.
- `reports/data_quality_report.md` — human-readable quality report.
- `reports/data_quality_summary.json` — machine-readable quality summary.

The processed datasets are large and may be shared separately rather than committed to Git. The strict and sensitivity cohorts should remain clearly distinguished in downstream analysis.

## Tests

Run the test suite:

```bash
python -m pytest tests/ -v
```

The tests use synthetic BTS-schema fixtures so they can run without downloading the full dataset.

## What the preprocessing does

1. Standardizes source column names to snake_case and records the source-to-standardized mapping.
2. Coerces delay, elapsed-time, distance, and indicator fields to appropriate types.
3. Reconstructs consistent local timestamps using scheduled times and BTS minute offsets, avoiding errors around midnight.
4. Validates and flags tail numbers, timestamps, cancellation/diversion indicators, delay fields, and implausible values.
5. Removes exact duplicate rows and records them separately.
6. Produces reproducible data-quality reports.

See [`docs/data_dictionary.md`](docs/data_dictionary.md) for field definitions, units, missing-value conventions, and validation rules.

## Assumptions and limitations

- BTS clock times are local airport times; cleaned datetimes are timezone-naive local timestamps.
- Delay and elapsed-time values are in minutes; distance is in miles.
- BTS reports `ActualElapsedTime` and `ArrDelay` as missing for diverted flights.
- Delay-cause fields are self-reported and may be incomplete.
- The project is observational; estimated results describe adjusted associations and do not establish causation.

## Reproducibility

The preprocessing pipeline is designed to be deterministic for a given input. Synthetic test fixtures are test data, not real BTS observations, and allow the test suite to run without the full dataset.
