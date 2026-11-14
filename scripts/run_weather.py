"""Reproducible CLI runner for the full weather feature pipeline.

Owned by Dhanush R (Person 3).

Usage::

    # Full pipeline (download + preprocess + features):
    python -m scripts.run_weather

    # Skip download if raw files already present:
    python -m scripts.run_weather --skip-download

    # Explicit paths:
    python -m scripts.run_weather \\
        --station-map data/weather/airport_station_map.csv \\
        --flights     data/processed/flights_rotation_eligible.csv \\
        --raw-dir     data/weather/raw \\
        --obs-out     data/weather/weather_obs.csv \\
        --features-out data/processed/weather_features.csv \\
        --download-log data/weather/download_log.json \\
        --report      reports/weather_features_report.md \\
        --years 2023 2024

Outputs::

    data/weather/raw/{year}/{station_id}.csv   (one per station-year)
    data/weather/download_log.json
    data/weather/weather_obs.csv
    data/processed/weather_features.csv
    reports/weather_features_report.md
    reports/weather_features_report.json
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.weather_features import run

if __name__ == "__main__":
    raise SystemExit(run())
