"""Reproducible builder for data/processed/external_features.csv.

Owned by Dhanush R (Person 3).

Performs a validated 1:1 LEFT JOIN of congestion_features.csv with
weather_features.csv on flight_id, then writes the integrated external
features dataset.

Usage::

    python -m scripts.build_external_features

    # Explicit paths:
    python -m scripts.build_external_features \\
        --congestion data/processed/congestion_features.csv \\
        --weather    data/processed/weather_features.csv \\
        --output     data/processed/external_features.csv

Outputs::

    data/processed/external_features.csv   (2,319,468 rows x 19 columns)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

FINAL_COLS = [
    "flight_id",
    "flight_date", "reporting_airline", "flight_number", "origin", "dest",
    "crs_dep_dt_utc",
    "dep_count_origin_1h", "dep_count_origin_3h",
    "arr_count_dest_1h",   "arr_count_dest_3h",
    "total_traffic_origin_1h",
    "weather_date",
    "temp_c_prev", "dewp_c_prev", "visibility_km_prev",
    "wind_ms_prev", "gust_ms_prev", "prcp_mm_prev",
]

WX_FEAT_COLS = [
    "weather_date",
    "temp_c_prev", "dewp_c_prev", "visibility_km_prev",
    "wind_ms_prev", "gust_ms_prev", "prcp_mm_prev",
]

CHUNKSIZE = 200_000


def build_external_features(
    congestion_path: str,
    weather_path: str,
    output_path: str,
    chunksize: int = CHUNKSIZE,
) -> dict:
    """Join congestion and weather features, write external_features.csv.

    Parameters
    ----------
    congestion_path, weather_path : str
        Paths to the individual feature CSVs.
    output_path : str
        Destination path.
    chunksize : int
        Rows per chunk for streaming the congestion file.

    Returns
    -------
    dict
        Validation summary.
    """
    print(f"Loading weather features from {weather_path} ...")
    wx = pd.read_csv(weather_path, usecols=["flight_id"] + WX_FEAT_COLS, low_memory=False)
    wx = wx.set_index("flight_id")

    n_wx_dup = wx.index.duplicated().sum()
    if n_wx_dup > 0:
        raise ValueError(f"{n_wx_dup} duplicate flight_ids in weather_features — cannot join safely.")
    print(f"  {len(wx):,} weather rows loaded, 0 duplicates.")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    rows_written = 0
    identity_mismatches = 0
    flight_id_counter = 0
    first_chunk = True

    print(f"Joining and writing {output_path} ...")
    for chunk in pd.read_csv(congestion_path, chunksize=chunksize, low_memory=False):
        n = len(chunk)

        # Validate sequential flight_ids
        expected = list(range(flight_id_counter, flight_id_counter + n))
        if list(chunk["flight_id"]) != expected:
            raise ValueError(
                f"Non-sequential flight_ids at offset {flight_id_counter}. "
                "Source files may have been modified."
            )
        flight_id_counter += n

        # Attach weather columns
        wx_chunk = wx.loc[chunk["flight_id"].values, WX_FEAT_COLS].reset_index(drop=True)

        # Identity check: weather_date + 1 day == flight_date
        wx_flight_date = (
            pd.to_datetime(wx_chunk["weather_date"]) + pd.Timedelta(days=1)
        ).dt.strftime("%Y-%m-%d")
        cong_flight_date = chunk["flight_date"].astype(str).reset_index(drop=True)
        mismatch = int((wx_flight_date != cong_flight_date).sum())
        identity_mismatches += mismatch

        # Build output frame
        out = pd.concat([
            chunk[[
                "flight_id", "flight_date", "reporting_airline", "flight_number",
                "origin", "dest", "crs_dep_dt_utc",
                "dep_count_origin_1h", "dep_count_origin_3h",
                "arr_count_dest_1h",   "arr_count_dest_3h",
                "total_traffic_origin_1h",
            ]].reset_index(drop=True),
            wx_chunk,
        ], axis=1)[FINAL_COLS]

        mode = "w" if first_chunk else "a"
        out.to_csv(output_path, index=False, mode=mode, header=first_chunk)
        rows_written += len(out)
        first_chunk = False
        print(f"\r  {rows_written:,} rows written ...", end="", flush=True)

    print()
    print(f"Done. {rows_written:,} rows -> {output_path}")

    if identity_mismatches > 0:
        raise ValueError(
            f"{identity_mismatches} identity mismatches (weather_date+1 != flight_date). "
            "Source files are inconsistent."
        )

    return {
        "rows_written": rows_written,
        "identity_mismatches": identity_mismatches,
        "output_path": output_path,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build external_features.csv from congestion + weather feature files."
    )
    parser.add_argument("--congestion",
                        default=os.path.join("data","processed","congestion_features.csv"))
    parser.add_argument("--weather",
                        default=os.path.join("data","processed","weather_features.csv"))
    parser.add_argument("--output",
                        default=os.path.join("data","processed","external_features.csv"))
    parser.add_argument("--chunksize", type=int, default=CHUNKSIZE)
    args = parser.parse_args(argv)

    result = build_external_features(
        congestion_path=args.congestion,
        weather_path=args.weather,
        output_path=args.output,
        chunksize=args.chunksize,
    )
    print(f"external_features.csv: {result['rows_written']:,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
