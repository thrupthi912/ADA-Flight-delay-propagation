"""Reproducible CLI runner for the NOAA station matching pipeline.

Owned by Dhanush R (Person 3).

Usage::

    # From the repository root (with the virtual environment active):
    python -m scripts.build_station_map

    # Force re-download of the ISD catalog:
    python -m scripts.build_station_map --force-download

    # With explicit paths:
    python -m scripts.build_station_map \\
        --airport-tz  data/airport_timezones.csv \\
        --flights     data/processed/flights_rotation_eligible.csv \\
        --isd-catalog data/weather/isd-history.csv \\
        --output      data/weather/airport_station_map.csv \\
        --report      reports/station_matching_report.md

Outputs::

    data/weather/isd-history.csv            (downloaded once, cached)
    data/weather/airport_station_map.csv    (airport -> station map)
    reports/station_matching_report.md
    reports/station_matching_report.json
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is importable as src.* regardless of invocation style.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.station_matching import run

if __name__ == "__main__":
    raise SystemExit(run())
