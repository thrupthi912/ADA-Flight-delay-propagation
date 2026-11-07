"""Reproducible CLI runner for the congestion feature pipeline.

Owned by Dhanush R (Person 3).

Usage::

    # From the repository root (with the virtual environment active):
    python -m scripts.run_congestion

    # With explicit paths:
    python -m scripts.run_congestion \\
        --input  data/processed/flights_rotation_eligible.csv \\
        --output data/processed/congestion_features.csv \\
        --report reports/congestion_features_report.md

Outputs::

    data/processed/congestion_features.csv
    reports/congestion_features_report.md
    reports/congestion_features_report.json
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is importable as src.* regardless of invocation style.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.congestion_features import run

if __name__ == "__main__":
    raise SystemExit(run())
