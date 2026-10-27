"""Reconstruct aircraft rotations and calculate turnaround measures.

Author: Amulya P

This module links consecutive flights operated by the same aircraft,
validates candidate connections, and calculates scheduled ground time
and turnaround slack.

All timestamps used for comparisons must be UTC instants produced by
the preprocessing module.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


TIMESTAMP_COLUMNS = [
    "crs_dep_dt_utc",
    "dep_dt_utc",
    "crs_arr_dt_utc",
    "arr_dt_utc",
]

REQUIRED_COLUMNS = [
    "tail_number",
    "rotation_eligible",
    "origin",
    "dest",
    *TIMESTAMP_COLUMNS,
]


def _is_eligible(value: object) -> bool:
    """Interpret a preprocessing eligibility flag safely."""
    if pd.isna(value):
        return False

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}

    return value == True  # noqa: E712


def build_rotation_pairs(flights: pd.DataFrame) -> pd.DataFrame:
    """Build and validate candidate pairs of consecutive aircraft flights.

    Parameters
    ----------
    flights:
        Cleaned flight-level data from src.preprocessing.

    Returns
    -------
    pandas.DataFrame
        One row per candidate connection between adjacent records for
        the same aircraft. Invalid candidates are retained with an
        exclusion reason so the decisions remain auditable.

    Notes
    -----
    Negative turnaround slack is not automatically invalid. It means
    the previous flight arrived after the next flight's scheduled
    departure, which is potentially important evidence of delay
    propagation.
    """
    missing = sorted(set(REQUIRED_COLUMNS) - set(flights.columns))
    if missing:
        raise ValueError(
            f"Missing required columns: {', '.join(missing)}"
        )

    if flights.empty:
        return pd.DataFrame(
            columns=[
                "incoming_row_id",
                "next_row_id",
                "tail_number",
                "incoming_origin",
                "incoming_dest",
                "next_origin",
                "next_dest",
                "scheduled_ground_time_min",
                "turnaround_slack_min",
                "actual_turnaround_min",
                "connection_valid",
                "exclusion_reason",
            ]
        )

    work = flights.copy().reset_index(drop=True)
    work["_source_row_id"] = range(len(work))

    # Parse timestamps as UTC. Naive timestamps are interpreted as UTC,
    # so the input must already have been normalized by preprocessing.
    for column in TIMESTAMP_COLUMNS:
        work[column] = pd.to_datetime(
            work[column],
            errors="coerce",
            utc=True,
        )

    work["eligible"] = work["rotation_eligible"].map(_is_eligible)

    # Missing aircraft identifiers cannot establish a rotation.
    work = work.loc[
        work["tail_number"].notna()
        & work["tail_number"].astype(str).str.strip().ne("")
    ].copy()

    if work.empty:
        return pd.DataFrame(
            columns=[
                "incoming_row_id",
                "next_row_id",
                "tail_number",
                "incoming_origin",
                "incoming_dest",
                "next_origin",
                "next_dest",
                "scheduled_ground_time_min",
                "turnaround_slack_min",
                "actual_turnaround_min",
                "connection_valid",
                "exclusion_reason",
            ]
        )

    # Sort by scheduled departure, which preserves the planned sequence.
    # We then check actual chronology before accepting each connection.
    work = work.sort_values(
        ["tail_number", "crs_dep_dt_utc", "_source_row_id"],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)

    grouped = work.groupby("tail_number", sort=False)

    next_columns = {
        "_source_row_id": "next_row_id",
        "eligible": "next_eligible",
        "crs_dep_dt_utc": "next_crs_dep_dt_utc",
        "dep_dt_utc": "next_dep_dt_utc",
        "crs_arr_dt_utc": "next_crs_arr_dt_utc",
        "arr_dt_utc": "next_arr_dt_utc",
        "origin": "next_origin",
        "dest": "next_dest",
    }

    for source, target in next_columns.items():
        work[target] = grouped[source].shift(-1)

    # The final record for each aircraft has no following flight.
    candidates = work.loc[work["next_row_id"].notna()].copy()

    if candidates.empty:
        return pd.DataFrame(
            columns=[
                "incoming_row_id",
                "next_row_id",
                "tail_number",
                "incoming_origin",
                "incoming_dest",
                "next_origin",
                "next_dest",
                "scheduled_ground_time_min",
                "turnaround_slack_min",
                "actual_turnaround_min",
                "connection_valid",
                "exclusion_reason",
            ]
        )

    # Time differences are expressed in minutes.
    candidates["scheduled_ground_time_min"] = (
        candidates["next_crs_dep_dt_utc"]
        - candidates["crs_arr_dt_utc"]
    ).dt.total_seconds() / 60

    candidates["turnaround_slack_min"] = (
        candidates["next_crs_dep_dt_utc"]
        - candidates["arr_dt_utc"]
    ).dt.total_seconds() / 60

    candidates["actual_turnaround_min"] = (
        candidates["next_dep_dt_utc"]
        - candidates["arr_dt_utc"]
    ).dt.total_seconds() / 60

    reasons = []

    for row in candidates.itertuples(index=False):
        row_reasons = []

        if not row.eligible:
            row_reasons.append("incoming_flight_ineligible")

        if not row.next_eligible:
            row_reasons.append("next_flight_ineligible")

        if pd.isna(row.crs_arr_dt_utc):
            row_reasons.append("missing_incoming_scheduled_arrival")

        if pd.isna(row.next_crs_dep_dt_utc):
            row_reasons.append("missing_next_scheduled_departure")

        if (
            pd.notna(row.scheduled_ground_time_min)
            and row.scheduled_ground_time_min < 0
        ):
            row_reasons.append("negative_scheduled_ground_time")

        if pd.isna(row.arr_dt_utc):
            row_reasons.append("missing_incoming_actual_arrival")

        if pd.isna(row.next_dep_dt_utc):
            row_reasons.append("missing_next_actual_departure")

        if (
            pd.notna(row.arr_dt_utc)
            and pd.notna(row.next_dep_dt_utc)
            and row.next_dep_dt_utc < row.arr_dt_utc
        ):
            row_reasons.append("actual_flights_overlap")

        reasons.append(row_reasons)

    candidates["exclusion_reason"] = [
        ";".join(row_reasons) if row_reasons else ""
        for row_reasons in reasons
    ]

    candidates["connection_valid"] = (
        candidates["exclusion_reason"].eq("")
    )

    result = pd.DataFrame(
        {
            "incoming_row_id": candidates["_source_row_id"].astype(int),
            "next_row_id": candidates["next_row_id"].astype(int),
            "tail_number": candidates["tail_number"].values,
            "incoming_origin": candidates["origin"].values,
            "incoming_dest": candidates["dest"].values,
            "next_origin": candidates["next_origin"].values,
            "next_dest": candidates["next_dest"].values,
            "scheduled_ground_time_min": (
                candidates["scheduled_ground_time_min"].values
            ),
            "turnaround_slack_min": (
                candidates["turnaround_slack_min"].values
            ),
            "actual_turnaround_min": (
                candidates["actual_turnaround_min"].values
            ),
            "connection_valid": candidates["connection_valid"].values,
            "exclusion_reason": candidates["exclusion_reason"].values,
        }
    )

    return result.reset_index(drop=True)


def generate_rotation_report(pairs: pd.DataFrame) -> dict[str, int]:
    """Summarize candidate connections and validation outcomes."""
    if pairs.empty:
        return {
            "candidate_connections": 0,
            "valid_connections": 0,
            "invalid_connections": 0,
        }

    valid_count = int(pairs["connection_valid"].sum())

    return {
        "candidate_connections": len(pairs),
        "valid_connections": valid_count,
        "invalid_connections": len(pairs) - valid_count,
    }


def main() -> None:
    """Run rotation reconstruction from a cleaned CSV."""
    parser = argparse.ArgumentParser(
        description="Reconstruct aircraft rotations."
    )

    parser.add_argument(
        "--input",
        default="data/processed/flights_clean.csv",
        help="Path to the cleaned flight-level CSV.",
    )

    parser.add_argument(
        "--output",
        default="results/aircraft_rotation_pairs.csv",
        help="Path for the candidate-pair output CSV.",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input dataset not found: {input_path}. "
            "Run preprocessing first or specify --input."
        )

    flights = pd.read_csv(input_path)
    pairs = build_rotation_pairs(flights)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pairs.to_csv(output_path, index=False)

    report = generate_rotation_report(pairs)

    print("Aircraft rotation reconstruction complete.")
    print(f"Input records: {len(flights)}")
    print(f"Output file: {output_path}")

    for key, value in report.items():
        print(f"{key.replace('_', ' ').title()}: {value}")


if __name__ == "__main__":
    main()