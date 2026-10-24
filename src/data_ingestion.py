"""BTS On-Time Performance data ingestion.

This module is owned by Thrupthi G D. Its only job is to LOAD and validate the
raw BTS CSV: it does not clean, standardize, or transform the data. Cleaning
lives in ``preprocessing.py``.

Data source
------------
U.S. Bureau of Transportation Statistics (BTS), "Reporting Carrier On-Time
Performance (1987-present)" table:
    https://www.transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ

The field names and definitions used here were verified against the official
BTS "Fields" reference page:
    https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ

Key facts confirmed from that page (and assumed throughout this project):
  * Times (CRSDepTime, DepTime, CRSArrTime, ArrTime, WheelsOff, WheelsOn) are
    LOCAL time in hhmm 24-hour format. They are NOT UTC and they are NOT
    timezone-aware.
  * FlightDate is yyyymmdd and represents the scheduled departure date.
  * DepDelay / ArrDelay are signed minutes (early = negative).
  * Cancelled and Diverted are 0/1 indicators.
  * ActualElapsedTime and ArrDelay are NULL for diverted flights.

Because the raw file can be large, ``load_raw`` reads everything as strings by
default. This preserves exact values (leading zeros in times, blank cells) and
avoids pandas silently coercing mixed-type columns such as CancellationCode.
Type coercion happens deliberately, later, in ``preprocessing.py``.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import pandas as pd

__all__ = [
    "CANONICAL_FIELDS",
    "COLUMN_MAP",
    "ALIASES",
    "REQUIRED_FIELDS",
    "load_raw",
    "validate_columns",
    "basic_info",
    "save_sample",
    "find_raw_file",
    "main",
]

# ---------------------------------------------------------------------------
# Field schema
# ---------------------------------------------------------------------------
# Grouped reference of the BTS "Reporting Carrier On-Time Performance" fields.
# This is documentation only; COLUMN_MAP is what the code uses.
CANONICAL_FIELDS: dict[str, list[str]] = {
    "Time Period": [
        "Year", "Quarter", "Month", "DayofMonth", "DayOfWeek", "FlightDate",
    ],
    "Airline": [
        "Reporting_Airline", "DOT_ID_Reporting_Airline",
        "IATA_CODE_Reporting_Airline", "Tail_Number",
        "Flight_Number_Reporting_Airline",
    ],
    "Origin": [
        "OriginAirportID", "OriginAirportSeqID", "OriginCityMarketID",
        "Origin", "OriginCityName", "OriginState", "OriginStateFips",
        "OriginStateName", "OriginWac",
    ],
    "Destination": [
        "DestAirportID", "DestAirportSeqID", "DestCityMarketID",
        "Dest", "DestCityName", "DestState", "DestStateFips",
        "DestStateName", "DestWac",
    ],
    "Departure Performance": [
        "CRSDepTime", "DepTime", "DepDelay", "DepDelayMinutes", "DepDel15",
        "DepartureDelayGroups", "DepTimeBlk", "TaxiOut", "WheelsOff",
    ],
    "Arrival Performance": [
        "WheelsOn", "TaxiIn", "CRSArrTime", "ArrTime", "ArrDelay",
        "ArrDelayMinutes", "ArrDel15", "ArrivalDelayGroups", "ArrTimeBlk",
    ],
    "Cancellations and Diversions": [
        "Cancelled", "CancellationCode", "Diverted",
    ],
    "Flight Summaries": [
        "CRSElapsedTime", "ActualElapsedTime", "AirTime", "Flights",
        "Distance", "DistanceGroup",
    ],
    "Cause of Delay (from 6/2003)": [
        "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay",
        "LateAircraftDelay",
    ],
    "Gate Return Information (from 10/2008)": [
        "FirstDepTime", "TotalAddGTime", "LongestAddGTime",
    ],
    "Diverted Airport Information (from 10/2008)": [
        "DivAirportLandings", "DivReachedDest", "DivActualElapsedTime",
        "DivArrDelay", "DivDistance",
    ],
}

# Original BTS field name -> standardized snake_case name used in the cleaned
# dataset. Names preserve the original meaning; only casing/underscores change.
COLUMN_MAP: dict[str, str] = {
    # Time period
    "Year": "year", "Quarter": "quarter", "Month": "month",
    "DayofMonth": "day_of_month", "DayOfWeek": "day_of_week",
    "FlightDate": "flight_date",
    # Airline
    "Reporting_Airline": "reporting_airline",
    "DOT_ID_Reporting_Airline": "dot_id_reporting_airline",
    "IATA_CODE_Reporting_Airline": "iata_code_reporting_airline",
    "Tail_Number": "tail_number",
    "Flight_Number_Reporting_Airline": "flight_number",
    # Origin
    "OriginAirportID": "origin_airport_id",
    "OriginAirportSeqID": "origin_airport_seq_id",
    "OriginCityMarketID": "origin_city_market_id",
    "Origin": "origin", "OriginCityName": "origin_city_name",
    "OriginState": "origin_state", "OriginStateFips": "origin_state_fips",
    "OriginStateName": "origin_state_name", "OriginWac": "origin_wac",
    # Destination
    "DestAirportID": "dest_airport_id",
    "DestAirportSeqID": "dest_airport_seq_id",
    "DestCityMarketID": "dest_city_market_id",
    "Dest": "dest", "DestCityName": "dest_city_name",
    "DestState": "dest_state", "DestStateFips": "dest_state_fips",
    "DestStateName": "dest_state_name", "DestWac": "dest_wac",
    # Departure
    "CRSDepTime": "crs_dep_time", "DepTime": "dep_time",
    "DepDelay": "dep_delay", "DepDelayMinutes": "dep_delay_minutes",
    "DepDel15": "dep_del15", "DepartureDelayGroups": "departure_delay_groups",
    "DepTimeBlk": "dep_time_blk", "TaxiOut": "taxi_out",
    "WheelsOff": "wheels_off",
    # Arrival
    "WheelsOn": "wheels_on", "TaxiIn": "taxi_in",
    "CRSArrTime": "crs_arr_time", "ArrTime": "arr_time",
    "ArrDelay": "arr_delay", "ArrDelayMinutes": "arr_delay_minutes",
    "ArrDel15": "arr_del15", "ArrivalDelayGroups": "arrival_delay_groups",
    "ArrTimeBlk": "arr_time_blk",
    # Cancellation / diversion
    "Cancelled": "cancelled", "CancellationCode": "cancellation_code",
    "Diverted": "diverted",
    # Flight summaries
    "CRSElapsedTime": "crs_elapsed_time",
    "ActualElapsedTime": "actual_elapsed_time",
    "AirTime": "air_time", "Flights": "flights",
    "Distance": "distance", "DistanceGroup": "distance_group",
    # Delay causes
    "CarrierDelay": "carrier_delay", "WeatherDelay": "weather_delay",
    "NASDelay": "nas_delay", "SecurityDelay": "security_delay",
    "LateAircraftDelay": "late_aircraft_delay",
    # Gate return
    "FirstDepTime": "first_dep_time", "TotalAddGTime": "total_add_gtime",
    "LongestAddGTime": "longest_add_gtime",
    # Diverted info
    "DivAirportLandings": "div_airport_landings",
    "DivReachedDest": "div_reached_dest",
    "DivActualElapsedTime": "div_actual_elapsed_time",
    "DivArrDelay": "div_arr_delay", "DivDistance": "div_distance",
}

# Older BTS downloads (pre-2018) used different names for a few fields. These
# aliases are resolved to the current canonical names BEFORE standardization,
# so the pipeline works with either vintage of the data.
ALIASES: dict[str, str] = {
    "UniqueCarrier": "Reporting_Airline",
    "TailNum": "Tail_Number",
    "FlightNum": "Flight_Number_Reporting_Airline",
}

# Minimal set of standardized fields needed for the cleaning step to be
# meaningful. ``validate_columns`` reports which of these are present.
REQUIRED_FIELDS: list[str] = [
    "flight_date", "tail_number", "origin", "dest",
    "crs_dep_time", "dep_time", "dep_delay",
    "crs_arr_time", "arr_time", "arr_delay",
    "cancelled", "diverted",
    "crs_elapsed_time", "actual_elapsed_time", "distance",
]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_raw(path: str, dtype: Any = str, nrows: int | None = None) -> pd.DataFrame:
    """Load a raw BTS CSV file as-is (no standardization, no coercion).

    Parameters
    ----------
    path : str
        Path to the raw BTS CSV (e.g. data/raw/ONTIME_2024-01.csv).
    dtype : type, default str
        Read every column as string to preserve exact values (leading zeros in
        hhmm times, blank cells, mixed cancellation codes). Type coercion is a
        deliberate, separate step done in ``preprocessing.standardize``.
    nrows : int, optional
        Read only the first ``nrows`` rows. Useful to try one month or a sample
        before processing a full year.

    Returns
    -------
    pandas.DataFrame
        Raw, untouched flight records.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Raw data file not found: {path}")
    df = pd.read_csv(path, dtype=dtype, nrows=nrows, low_memory=False)
    # Strip whitespace from column headers; BTS downloads sometimes pad them.
    df.columns = [c.strip() for c in df.columns]
    return df


def find_raw_file(raw_dir: str = "data/raw") -> str | None:
    """Return the path of the first CSV/zip in ``raw_dir``, or None if empty."""
    if not os.path.isdir(raw_dir):
        return None
    for name in sorted(os.listdir(raw_dir)):
        if name.lower().endswith((".csv",)) and not name.startswith("."):
            return os.path.join(raw_dir, name)
    return None


# ---------------------------------------------------------------------------
# Validation / reporting
# ---------------------------------------------------------------------------
def validate_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    """Check which required BTS fields are present in a raw dataframe.

    Works against the ORIGINAL BTS column names (the file has not been
    standardized yet). Aliases are recognized so an older-format file still
    validates.

    Returns a dict with keys ``present``, ``missing``, ``unexpected``.
    """
    have = set(df.columns)
    # Resolve aliases present in the file to canonical names before checking.
    have_resolved = {ALIASES.get(c, c) for c in have}

    required_canonical = [  # standardized -> original canonical name
        next(k for k, v in COLUMN_MAP.items() if v == std)
        for std in REQUIRED_FIELDS
    ]
    present = sorted(c for c in required_canonical if c in have_resolved)
    missing = sorted(c for c in required_canonical if c not in have_resolved)
    known = set(COLUMN_MAP) | set(ALIASES)
    unexpected = sorted(c for c in have if c not in known)
    return {"present": present, "missing": missing, "unexpected": unexpected}


def basic_info(df: pd.DataFrame, source: str = "") -> dict[str, Any]:
    """Summarize a raw dataframe: shape, columns, dtypes, missingness, memory."""
    n_rows, n_cols = df.shape
    missing = df.isna().sum()
    missing = missing[missing > 0].sort_values(ascending=False)
    return {
        "source": source,
        "n_rows": int(n_rows),
        "n_cols": int(n_cols),
        "columns": list(df.columns),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "memory_mb": round(df.memory_usage(deep=True).sum() / 1e6, 2),
        "missing_per_column": {c: int(n) for c, n in missing.items()},
    }


def save_sample(df: pd.DataFrame, out_path: str, n: int = 1000) -> str:
    """Write the first ``n`` raw rows to ``out_path`` for a quick-look sample."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    df.head(n).to_csv(out_path, index=False)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load and validate a raw BTS On-Time Performance CSV."
    )
    parser.add_argument("path", nargs="?", help="Path to the raw BTS CSV file.")
    parser.add_argument("--raw-dir", default="data/raw",
                        help="Directory to search if no path is given.")
    parser.add_argument("--nrows", type=int, default=None,
                        help="Read only the first N rows (sample month, etc.).")
    parser.add_argument("--sample-out", default="data/sample/raw_sample.csv",
                        help="Where to write a small raw sample for quick review.")
    parser.add_argument("--sample-n", type=int, default=1000,
                        help="Number of rows to keep in the raw sample.")
    args = parser.parse_args(argv)

    path = args.path or find_raw_file(args.raw_dir)
    if not path:
        print(f"No BTS CSV found in '{args.raw_dir}'.")
        print("Download a month from BTS and place the CSV in data/raw/:")
        print("  https://www.transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ")
        return 1

    print(f"Loading {path} ...")
    df = load_raw(path, nrows=args.nrows)
    info = basic_info(df, source=path)
    print(f"  rows: {info['n_rows']:,}   cols: {info['n_cols']}   "
          f"memory: {info['memory_mb']} MB")
    print(f"  columns: {', '.join(info['columns'])}")

    val = validate_columns(df)
    print(f"  required fields present: {len(val['present'])}/{len(REQUIRED_FIELDS)}")
    if val["missing"]:
        print(f"  MISSING required fields: {', '.join(val['missing'])}")
    if val["unexpected"]:
        print(f"  unexpected/unknown columns: {', '.join(val['unexpected'])}")

    if info["missing_per_column"]:
        print("  missing values per column (top 10):")
        for c, n in list(info["missing_per_column"].items())[:10]:
            print(f"    {c}: {n:,}")

    save_sample(df, args.sample_out, n=args.sample_n)
    print(f"  raw sample written to {args.sample_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
