"""Airport congestion feature engineering.

Owned by Dhanush R (Person 3).

Computes airport-level scheduled-traffic congestion features from the BTS
cleaned flight dataset.  All features are derived **exclusively from scheduled
(CRS) UTC timestamps** — never from actual departure or arrival times — so
they carry no information that would be unavailable at the time a delay
prediction is made.

Congestion features
-------------------
For each flight row the following five features are appended:

dep_count_origin_1h
    Number of *other* scheduled departures from the same origin airport in
    the trailing 1-hour window [crs_dep_dt_utc − 1 h,  crs_dep_dt_utc).

dep_count_origin_3h
    Same metric over a 3-hour trailing window.

arr_count_dest_1h
    Number of *other* scheduled arrivals at the destination airport in the
    trailing 1-hour window [crs_arr_dt_utc − 1 h,  crs_arr_dt_utc).

arr_count_dest_3h
    Same metric over a 3-hour trailing window.

total_traffic_origin_1h
    Combined scheduled movements at the **origin** airport in the trailing
    1-hour window before CRS departure:
        departures from origin + arrivals at origin in [crs_dep_dt_utc − 1 h,
        crs_dep_dt_utc).
    Arrivals at the origin are drawn from the full dataset's ``dest`` column,
    keyed on the same origin IATA code.

Leakage guarantee
-----------------
Every window is **strictly trailing** and **open on the right** — the flight
under consideration is never included in its own congestion count.  The
boundary rule ``[t − W, t)`` ensures:

* No knowledge of the flight's own actual outcome.
* No other flights scheduled to depart/arrive *after* the reference time.
* Consistency with a real-time prediction setting where all scheduled
  flights for the day are published in advance but only past movements are
  observable.

Unique key
----------
The output preserves the original row order of the input CSV.  A ``flight_id``
column (0-based integer row position in the input file) is added as an
explicit, unique row identifier.  The join key for downstream consumers is
simply ``flight_id``, which maps to the same positional row in the source
``flights_rotation_eligible.csv``.

Memory efficiency
-----------------
The algorithm processes the data in two passes:

1. **Index pass** — load only the columns needed to build per-airport sorted
   timestamp arrays (``origin``, ``dest``, ``crs_dep_dt_utc``,
   ``crs_arr_dt_utc``, ``flight_id``).  The sorted arrays are built once
   per airport and kept as compact numpy arrays.

2. **Feature lookup** — for each flight row the five congestion counts are
   computed via ``np.searchsorted`` (O(log N) per lookup).  Results are
   written to a pre-allocated integer array and streamed to disk.

Peak memory is dominated by two sorted datetime arrays per airport
(O(N) total across all airports) rather than the full 93-column dataset.

Algorithm
---------
For a sorted array of timestamps T and a query point t with window width W:

    count = searchsorted(T, t, 'left') − searchsorted(T, t − W, 'left')

``searchsorted(T, t, 'left')`` returns the index of the first element ≥ t,
so subtracting ``searchsorted(T, t − W, 'left')`` gives the number of
elements in [t − W, t).  The flight itself is excluded because the query
uses strict-less-than on the right (open interval).

Timestamps are compared as ``datetime64[us]`` numpy arrays (tz-info stripped
after confirming all values are UTC) to avoid the overhead of timezone-aware
object arrays.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "WINDOW_1H",
    "WINDOW_3H",
    "INPUT_COLS",
    "OUTPUT_FEATURES",
    "build_airport_index",
    "compute_trailing_count",
    "compute_congestion_features",
    "run",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Trailing window widths as numpy timedelta64 (us resolution to match pandas 3.x).
WINDOW_1H: np.timedelta64 = np.timedelta64(1, "h")
WINDOW_3H: np.timedelta64 = np.timedelta64(3, "h")

#: Columns loaded from the source CSV (minimises memory).
INPUT_COLS: list[str] = [
    "flight_date",
    "reporting_airline",
    "flight_number",
    "origin",
    "dest",
    "crs_dep_dt_utc",
    "crs_arr_dt_utc",
]

#: Congestion feature names in output order.
OUTPUT_FEATURES: list[str] = [
    "dep_count_origin_1h",
    "dep_count_origin_3h",
    "arr_count_dest_1h",
    "arr_count_dest_3h",
    "total_traffic_origin_1h",
]

# ---------------------------------------------------------------------------
# Airport index (sorted timestamp arrays)
# ---------------------------------------------------------------------------


def build_airport_index(
    df: pd.DataFrame,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Build per-airport sorted numpy datetime64 arrays for departures and arrivals.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain ``origin``, ``dest``, ``crs_dep_dt_utc``,
        ``crs_arr_dt_utc`` columns with UTC-parsed timestamps.

    Returns
    -------
    dep_index : dict[str, ndarray[datetime64[us]]]
        ``{iata: sorted_departure_timestamps}`` — one entry per origin airport.
    arr_index : dict[str, ndarray[datetime64[us]]]
        ``{iata: sorted_arrival_timestamps}`` — one entry per dest airport.

    Notes
    -----
    Timestamps are stripped of timezone info (all values are UTC) so that
    numpy can use the compact ``datetime64[us]`` dtype rather than object
    arrays.  The stripping is safe because every row in
    ``flights_rotation_eligible.csv`` has ``has_utc_timestamps=True``.
    """
    # Strip tz info to get plain datetime64[us] — safe because all values are UTC.
    dep_ts = df["crs_dep_dt_utc"].dt.tz_localize(None)
    arr_ts = df["crs_arr_dt_utc"].dt.tz_localize(None)

    dep_index: dict[str, np.ndarray] = {}
    arr_index: dict[str, np.ndarray] = {}

    for airport in df["origin"].unique():
        mask = df["origin"] == airport
        arr = np.sort(dep_ts[mask].to_numpy())
        dep_index[airport] = arr

    for airport in df["dest"].unique():
        mask = df["dest"] == airport
        arr = np.sort(arr_ts[mask].to_numpy())
        arr_index[airport] = arr

    return dep_index, arr_index


# ---------------------------------------------------------------------------
# Core counting primitive
# ---------------------------------------------------------------------------


def compute_trailing_count(
    sorted_ts: np.ndarray,
    query_ts: np.ndarray | np.datetime64,
    window: np.timedelta64,
) -> np.ndarray | int:
    """Count timestamps in ``[query − window, query)`` using binary search.

    Parameters
    ----------
    sorted_ts : ndarray of datetime64
        Pre-sorted array of all scheduled timestamps for one airport.
    query_ts : ndarray of datetime64 or scalar datetime64
        The reference time(s) for the trailing window.
    window : timedelta64
        Width of the trailing window (e.g. ``WINDOW_1H``).

    Returns
    -------
    ndarray of int (or scalar int for scalar input)
        Count of elements in ``[query − window, query)`` for each query.

    Notes
    -----
    ``searchsorted(T, t, 'left')`` returns the first index where T[i] ≥ t,
    so the half-open interval count is::

        right − left
        = searchsorted(T, t, 'left') − searchsorted(T, t − W, 'left')

    The flight at exactly ``t`` is excluded (right boundary is open), which
    prevents a flight from counting itself.
    """
    right = np.searchsorted(sorted_ts, query_ts, side="left")
    left = np.searchsorted(sorted_ts, query_ts - window, side="left")
    return right - left


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------


def compute_congestion_features(
    input_path: str,
    output_path: str,
    chunksize: int = 200_000,
) -> dict[str, Any]:
    """Compute congestion features for every flight and write output CSV.

    Parameters
    ----------
    input_path : str
        Path to ``flights_rotation_eligible.csv`` (or equivalent cleaned BTS
        file with ``crs_dep_dt_utc`` and ``crs_arr_dt_utc`` columns).
    output_path : str
        Destination path for ``congestion_features.csv``.
    chunksize : int, default 200_000
        Rows per chunk for the index-building pass.

    Returns
    -------
    dict
        Summary statistics and coverage metrics.

    Algorithm
    ---------
    Pass 1 — build index
        Load only ``origin``, ``dest``, ``crs_dep_dt_utc``, ``crs_arr_dt_utc``
        in chunks.  Accumulate per-airport timestamp lists, then sort once.

    Pass 2 — compute features
        Re-read the same columns plus the join key columns chunk by chunk.
        For each row look up pre-sorted airport arrays via
        ``compute_trailing_count``.  Write results incrementally to avoid
        holding the full output in memory.
    """
    t0 = time.time()

    # ------------------------------------------------------------------
    # Pass 1 — build the per-airport timestamp index
    # ------------------------------------------------------------------
    print("Pass 1: building airport timestamp index ...")

    dep_lists: dict[str, list[np.ndarray]] = {}
    arr_lists: dict[str, list[np.ndarray]] = {}
    total_rows = 0
    null_dep = 0
    null_arr = 0

    for chunk in pd.read_csv(
        input_path,
        usecols=["origin", "dest", "crs_dep_dt_utc", "crs_arr_dt_utc"],
        chunksize=chunksize,
        low_memory=False,
    ):
        null_dep += int(chunk["crs_dep_dt_utc"].isna().sum())
        null_arr += int(chunk["crs_arr_dt_utc"].isna().sum())

        # Drop rows with missing scheduled timestamps — cannot place in index
        valid = chunk.dropna(subset=["crs_dep_dt_utc", "crs_arr_dt_utc"])
        total_rows += len(chunk)

        dep_ts = pd.to_datetime(valid["crs_dep_dt_utc"], utc=True).dt.tz_localize(None)
        arr_ts = pd.to_datetime(valid["crs_arr_dt_utc"], utc=True).dt.tz_localize(None)

        for airport in valid["origin"].unique():
            mask = valid["origin"] == airport
            dep_lists.setdefault(airport, []).append(dep_ts[mask].to_numpy())

        for airport in valid["dest"].unique():
            mask = valid["dest"] == airport
            arr_lists.setdefault(airport, []).append(arr_ts[mask].to_numpy())

    # Sort and consolidate
    dep_index: dict[str, np.ndarray] = {
        ap: np.sort(np.concatenate(parts)) for ap, parts in dep_lists.items()
    }
    arr_index: dict[str, np.ndarray] = {
        ap: np.sort(np.concatenate(parts)) for ap, parts in arr_lists.items()
    }

    n_origin_airports = len(dep_index)
    n_dest_airports = len(arr_index)
    print(
        f"  Index built: {n_origin_airports} origin airports, "
        f"{n_dest_airports} destination airports."
    )
    print(f"  Null crs_dep_dt_utc: {null_dep:,}  |  Null crs_arr_dt_utc: {null_arr:,}")

    # ------------------------------------------------------------------
    # Pass 2 — compute features row-by-row (chunk-at-a-time), stream output
    # ------------------------------------------------------------------
    print("Pass 2: computing congestion features ...")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # Track global stats
    flight_id_counter = 0
    rows_written = 0
    rows_skipped_missing_ts = 0
    first_chunk = True

    for chunk in pd.read_csv(
        input_path,
        usecols=INPUT_COLS,
        chunksize=chunksize,
        low_memory=False,
    ):
        n = len(chunk)
        chunk_ids = np.arange(flight_id_counter, flight_id_counter + n)
        flight_id_counter += n

        # Parse timestamps
        dep_utc_raw = pd.to_datetime(chunk["crs_dep_dt_utc"], utc=True, errors="coerce")
        arr_utc_raw = pd.to_datetime(chunk["crs_arr_dt_utc"], utc=True, errors="coerce")

        # Strip tz for numpy arithmetic
        dep_utc = dep_utc_raw.dt.tz_localize(None)
        arr_utc = arr_utc_raw.dt.tz_localize(None)

        missing_ts = dep_utc_raw.isna() | arr_utc_raw.isna()
        rows_skipped_missing_ts += int(missing_ts.sum())

        # Initialise feature arrays with NaN sentinel (-1 = missing)
        dep_1h = np.full(n, -1, dtype=np.int32)
        dep_3h = np.full(n, -1, dtype=np.int32)
        arr_1h = np.full(n, -1, dtype=np.int32)
        arr_3h = np.full(n, -1, dtype=np.int32)
        tot_1h = np.full(n, -1, dtype=np.int32)

        # Vectorised per-airport computation
        for i, (origin, dest) in enumerate(
            zip(chunk["origin"].to_numpy(), chunk["dest"].to_numpy())
        ):
            if missing_ts.iloc[i]:
                continue

            dep_t = dep_utc.iloc[i]
            arr_t = arr_utc.iloc[i]

            dep_t_np = dep_t.to_datetime64()
            arr_t_np = arr_t.to_datetime64()

            # Departure congestion at origin
            if origin in dep_index:
                d_arr = dep_index[origin]
                dep_1h[i] = int(compute_trailing_count(d_arr, dep_t_np, WINDOW_1H))
                dep_3h[i] = int(compute_trailing_count(d_arr, dep_t_np, WINDOW_3H))
            else:
                dep_1h[i] = 0
                dep_3h[i] = 0

            # Arrival congestion at destination
            if dest in arr_index:
                a_arr = arr_index[dest]
                arr_1h[i] = int(compute_trailing_count(a_arr, arr_t_np, WINDOW_1H))
                arr_3h[i] = int(compute_trailing_count(a_arr, arr_t_np, WINDOW_3H))
            else:
                arr_1h[i] = 0
                arr_3h[i] = 0

            # Combined traffic at origin (deps + arrivals AT origin before CRS dep)
            origin_arr_count = 0
            if origin in arr_index:
                origin_arr_count = int(
                    compute_trailing_count(arr_index[origin], dep_t_np, WINDOW_1H)
                )
            tot_1h[i] = dep_1h[i] + origin_arr_count

        # Build output DataFrame
        out = pd.DataFrame(
            {
                "flight_id": chunk_ids,
                "flight_date": chunk["flight_date"].to_numpy(),
                "reporting_airline": chunk["reporting_airline"].to_numpy(),
                "flight_number": chunk["flight_number"].to_numpy(),
                "origin": chunk["origin"].to_numpy(),
                "dest": chunk["dest"].to_numpy(),
                "crs_dep_dt_utc": chunk["crs_dep_dt_utc"].to_numpy(),
                "crs_arr_dt_utc": chunk["crs_arr_dt_utc"].to_numpy(),
                "dep_count_origin_1h": dep_1h,
                "dep_count_origin_3h": dep_3h,
                "arr_count_dest_1h": arr_1h,
                "arr_count_dest_3h": arr_3h,
                "total_traffic_origin_1h": tot_1h,
            }
        )

        # Replace -1 sentinel with NA for rows that had missing timestamps
        for feat in OUTPUT_FEATURES:
            out.loc[missing_ts.values, feat] = pd.NA

        # Write (append after first chunk)
        mode = "w" if first_chunk else "a"
        header = first_chunk
        out.to_csv(output_path, index=False, mode=mode, header=header)
        rows_written += len(out)
        first_chunk = False

        print(f"  ... wrote {rows_written:,} / {total_rows:,} rows", end="\r")

    print()  # newline after progress

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. Output: {output_path}")

    return {
        "total_input_rows": total_rows,
        "rows_written": rows_written,
        "rows_skipped_missing_timestamps": rows_skipped_missing_ts,
        "null_crs_dep_dt_utc": null_dep,
        "null_crs_arr_dt_utc": null_arr,
        "n_origin_airports": n_origin_airports,
        "n_dest_airports": n_dest_airports,
        "elapsed_seconds": round(elapsed, 1),
        "output_path": output_path,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Compute airport congestion features from cleaned BTS flight data.\n"
            "\n"
            "Reads flights_rotation_eligible.csv (or equivalent), computes five\n"
            "trailing-window scheduled-traffic counts per flight, and writes\n"
            "congestion_features.csv."
        )
    )
    parser.add_argument(
        "--input",
        default=os.path.join("data", "processed", "flights_rotation_eligible.csv"),
        help="Path to cleaned BTS CSV with crs_dep_dt_utc and crs_arr_dt_utc columns.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join("data", "processed", "congestion_features.csv"),
        help="Destination path for the congestion features CSV.",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=200_000,
        help="Rows per processing chunk (default 200,000).",
    )
    parser.add_argument(
        "--report",
        default=os.path.join("reports", "congestion_features_report.md"),
        help="Path to write the coverage/quality Markdown report.",
    )
    args = parser.parse_args(argv)

    print(f"Input:  {args.input}")
    print(f"Output: {args.output}")
    print(f"Report: {args.report}")

    summary = compute_congestion_features(
        input_path=args.input,
        output_path=args.output,
        chunksize=args.chunksize,
    )

    # Quick post-hoc stats on the output
    print("\nComputing output statistics ...")
    out_df = pd.read_csv(args.output, usecols=OUTPUT_FEATURES + ["flight_id"])

    # Duplicate check
    n_dup_ids = int(out_df["flight_id"].duplicated().sum())

    # Missing value check
    missing_counts: dict[str, int] = {
        feat: int(out_df[feat].isna().sum()) for feat in OUTPUT_FEATURES
    }

    # Feature distribution summary
    feat_stats: dict[str, dict] = {}
    for feat in OUTPUT_FEATURES:
        s = out_df[feat].dropna()
        feat_stats[feat] = {
            "mean": round(float(s.mean()), 2),
            "median": round(float(s.median()), 2),
            "max": int(s.max()),
            "p95": round(float(s.quantile(0.95)), 1),
            "pct_zero": round(100 * float((s == 0).mean()), 2),
        }

    summary["duplicate_flight_ids"] = n_dup_ids
    summary["missing_per_feature"] = missing_counts
    summary["feature_stats"] = feat_stats

    # Write JSON summary alongside report
    json_path = args.report.replace(".md", ".json")
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Summary JSON: {json_path}")

    _write_report(summary, args.report)
    print(f"Report:       {args.report}")

    # Final console summary
    print("\n=== Congestion Features — Summary ===")
    print(f"  Input rows:              {summary['total_input_rows']:,}")
    print(f"  Output rows:             {summary['rows_written']:,}")
    print(f"  Rows skipped (null ts):  {summary['rows_skipped_missing_timestamps']:,}")
    print(f"  Duplicate flight_ids:    {n_dup_ids}")
    print(f"  Origin airports covered: {summary['n_origin_airports']}")
    print(f"  Dest airports covered:   {summary['n_dest_airports']}")
    print()
    for feat in OUTPUT_FEATURES:
        ms = missing_counts[feat]
        st = feat_stats[feat]
        print(
            f"  {feat:<30}  missing={ms:,}  "
            f"mean={st['mean']:.1f}  max={st['max']}  "
            f"p95={st['p95']}  pct_zero={st['pct_zero']}%"
        )

    return 0


def _write_report(summary: dict, path: str) -> None:
    """Write a Markdown quality report for the congestion features."""
    lines = [
        "# Congestion Features — Data Quality and Coverage Report",
        "",
        "Generated by `src/congestion_features.py`.",
        "Owned by Dhanush R (Person 3).",
        "",
        "---",
        "",
        "## 1. Feature Definitions",
        "",
        "All features are computed from **scheduled (CRS) UTC timestamps only**.",
        "No actual departure or arrival times are used.",
        "",
        "### Time window convention",
        "",
        "For a flight with scheduled departure `t`:",
        "",
        "```",
        "trailing window = [t − W, t)   (closed on left, open on right)",
        "```",
        "",
        "The flight itself is never counted (right boundary is strictly open).",
        "",
        "| Feature | Timestamp used | Airport | Window |",
        "|---|---|---|---|",
        "| `dep_count_origin_1h` | `crs_dep_dt_utc` | origin | trailing 1 h |",
        "| `dep_count_origin_3h` | `crs_dep_dt_utc` | origin | trailing 3 h |",
        "| `arr_count_dest_1h` | `crs_arr_dt_utc` | dest | trailing 1 h |",
        "| `arr_count_dest_3h` | `crs_arr_dt_utc` | dest | trailing 3 h |",
        "| `total_traffic_origin_1h` | `crs_dep_dt_utc` | origin | trailing 1 h |",
        "",
        "`total_traffic_origin_1h` = `dep_count_origin_1h` + scheduled arrivals at origin",
        "in the same window.",
        "",
        "---",
        "",
        "## 2. Unique Key",
        "",
        "`flight_id` — 0-based integer row position in `flights_rotation_eligible.csv`.",
        "Guaranteed unique. Use this column to join congestion features back to the",
        "source flight data.",
        "",
        "---",
        "",
        "## 3. Input Coverage",
        "",
        f"- Total input rows: **{summary['total_input_rows']:,}**",
        f"- Null `crs_dep_dt_utc`: **{summary['null_crs_dep_dt_utc']:,}**",
        f"- Null `crs_arr_dt_utc`: **{summary['null_crs_arr_dt_utc']:,}**",
        f"- Rows skipped (missing either timestamp): **{summary['rows_skipped_missing_timestamps']:,}**",
        f"- Origin airports indexed: **{summary['n_origin_airports']}**",
        f"- Destination airports indexed: **{summary['n_dest_airports']}**",
        "",
        "---",
        "",
        "## 4. Output Coverage",
        "",
        f"- Rows written: **{summary['rows_written']:,}**",
        f"- Duplicate `flight_id` values: **{summary.get('duplicate_flight_ids', 'N/A')}**",
        "",
        "---",
        "",
        "## 5. Feature Statistics",
        "",
        "| Feature | Missing | Mean | Median | Max | P95 | % Zero |",
        "|---|---|---|---|---|---|---|",
    ]

    for feat in OUTPUT_FEATURES:
        ms = summary.get("missing_per_feature", {}).get(feat, "?")
        st = summary.get("feature_stats", {}).get(feat, {})
        lines.append(
            f"| `{feat}` | {ms:,} | {st.get('mean','?')} | "
            f"{st.get('median','?')} | {st.get('max','?')} | "
            f"{st.get('p95','?')} | {st.get('pct_zero','?')}% |"
        )

    lines += [
        "",
        "---",
        "",
        "## 6. Leakage Assessment",
        "",
        "- All features use **scheduled** (CRS) timestamps, not actual times.",
        "- The trailing window is **open on the right** — the flight itself is",
        "  excluded from its own count.",
        "- No future flight movements enter any feature value.",
        "- `dep_dt_utc` and `arr_dt_utc` (actual times) are **not used**.",
        "",
        "---",
        "",
        "## 7. Known Limitations",
        "",
        "- Source dataset covers **January, June, July, August 2024 only**",
        "  (the strict rotation-eligible cohort). The other 8 months require",
        "  re-downloading raw BTS data and rerunning the preprocessing pipeline.",
        "- Congestion counts for the **very first flights of each month** will be",
        "  zero because earlier flights in the same calendar month are in prior",
        "  months not present in the dataset. This affects only the first ~3 hours",
        "  of January 1, June 1, July 1, and August 1.",
        "- 8 US-territory airports (BQN, GUM, PBI, PPG, PSE, SJU, STT, STX) are",
        "  not in `airport_timezones.csv` but are present as destinations; they",
        "  are correctly handled because congestion is computed from `dest` lookups",
        "  using the flight dataset itself, not the timezone CSV.",
        "",
        f"*Report generated {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} UTC.*",
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(run())
