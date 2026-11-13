"""Reproducible CLI runner for airport network analysis.

Owned by Dhanush R (Person 3).

Usage::

    python -m scripts.run_network_analysis

    # With explicit paths:
    python -m scripts.run_network_analysis \\
        --flights  data/processed/flights_rotation_eligible.csv \\
        --edge-out data/processed/network_edge_list.csv \\
        --node-out data/processed/network_node_metrics.csv \\
        --report   reports/network_analysis_report.md

Outputs::

    data/processed/network_edge_list.csv
    data/processed/network_node_metrics.csv
    reports/network_analysis_report.md
    reports/network_degree_distribution.png
    reports/network_top_hubs.png
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.network_analysis import run

if __name__ == "__main__":
    raise SystemExit(run())
