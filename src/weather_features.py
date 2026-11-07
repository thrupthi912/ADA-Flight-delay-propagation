"""NOAA GSOD weather data download, preprocessing, and feature generation.

Owned by Dhanush R (Person 3).

Pipeline overview
-----------------
This module implements three sequential stages:

Stage 1 — Download (``download_gsod``)
    Downloads one GSOD CSV per station per year from NCEI and writes
    raw files to ``data/weather/raw/{year}/{station_id}.csv``.
    Failures are logged but do not abort the pipeline; missing stations
    are recorded in the download log.

Stage 2 — Preprocess (``build_weather_obs``)
    Reads the raw CSVs, applies missing-value sentinel replacement,
    unit conversion (°F→°C, knots→m/s, miles→km, inches→mm), and
    deduplication, then writes a single flat parquet/CSV:
    ``data/weather/weather_obs.csv``.

Stage 3 — Feature generation (``build_weather_features``)
    Joins weather observations to flights using the **prior-day lag**
    strategy described below.  Outputs ``data/processed/weather_features.csv``
    with one row per flight_id.

Prediction-time design (prior-day lag)
---------------------------------------
GSOD provides daily summaries — averages and totals computed over the
entire local calendar day.  A GSOD row for 2024-08-01 at ATL includes
observations from every hour of that day, including hours after most
morning flights have departed.  Using same-day GSOD for a 07:00 departure
would silently include post-departure observations (future information).

This pipeline joins on:

    weather_date = flight_date − 1 day

The prior day's summary was finalized at midnight and was fully available
before any departure on ``flight_date``.  Features carry the suffix ``_prev``
(e.g., ``temp_c_prev``) to make the lag explicit.

This is a deliberate and documented design choice — not a workaround.
Weather is strongly autocorrelated: yesterday's temperature, visibility, and
precipitation at an airport is a valid and causally reasonable predictor of
today's operational conditions.

Join key
--------
The join is: ``(origin, flight_date)`` → ``(iata, weather_date)`` where
``weather_date = flight_date − 1 day``.  Specifically:

1. Load station map to get ``iata → station_id``.
2. For each flight row: look up ``origin``'s station ID, then look up
   the GSOD row for ``(station_id, flight_date − 1)``.
3. Attach the five weather features.  If no obs available: NA.

Join cardinality: at most one GSOD row per (station, date), so the join
cannot multiply flight rows.

Weather features produced
-------------------------
temp_c_prev         Mean air temperature, °C (converted from °F)
dewp_c_prev         Mean dew point, °C
visibility_km_prev  Mean visibility, km (converted from miles)
wind_ms_prev        Mean wind speed, m/s (converted from knots)
gust_ms_prev        Maximum wind gust, m/s (999.9 sentinel → NA)
prcp_mm_prev        Total precipitation, mm (converted from inches)

GSOD missing-value sentinels replaced with NaN before conversion:
  TEMP, DEWP, MAX, MIN  → 9999.9
  VISIB, WDSP, MXSPD, GUST, SNDP → 999.9
  PRCP → 99.99

Unit conversions:
  °F → °C  : (x − 32) × 5/9
  knots → m/s : × 0.514444
  statute miles → km : × 1.60934
  inches → mm : × 25.4

GSOD URL format
---------------
``https://www.ncei.noaa.gov/data/global-summary-of-the-day/access/{year}/{usaf:06d}{wban:05d}.csv``

Both years 2024 (primary) and 2023 (for Dec 31 prior-day of Jan 1 flights)
are downloaded.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

__all__ = [
    "GSOD_BASE_URL",
    "GSOD_SENTINELS",
    "WEATHER_FEATURES",
    "station_id_str",
    "gsod_url",
    "download_one_station",
    "download_gsod",
    "parse_gsod_csv",
    "build_weather_obs",
    "build_weather_features",
    "run",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GSOD_BASE_URL: str = (
    "https://www.ncei.noaa.gov/data/global-summary-of-the-day/access"
)

#: Missing-value sentinels in GSOD CSVs → replace with NaN.
GSOD_SENTINELS: dict[str, float] = {
    "TEMP":  9999.9,
    "DEWP":  9999.9,
    "VISIB": 999.9,
    "WDSP":  999.9,
    "MXSPD": 999.9,
    "GUST":  999.9,
    "MAX":   9999.9,
    "MIN":   9999.9,
    "PRCP":  99.99,
    "SNDP":  999.9,
}

#: Final output feature names (with _prev suffix to encode the lag).
WEATHER_FEATURES: list[str] = [
    "temp_c_prev",
    "dewp_c_prev",
    "visibility_km_prev",
    "wind_ms_prev",
    "gust_ms_prev",
    "prcp_mm_prev",
]

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def station_id_str(usaf: str | int, wban: str | int) -> str:
    """Return the 11-character GSOD station identifier ``USAF(6) + WBAN(5)``."""
    return f"{str(usaf).zfill(6)}{str(wban).zfill(5)}"


def gsod_url(station_id: str, year: int) -> str:
    """Return the NCEI GSOD CSV URL for a station and year."""
    return f"{GSOD_BASE_URL}/{year}/{station_id}.csv"


# ---------------------------------------------------------------------------
# Stage 1 — Download
# ---------------------------------------------------------------------------


def download_one_station(
    station_id: str,
    year: int,
    raw_dir: str,
    timeout: int = 30,
    retries: int = 2,
) -> dict[str, Any]:
    """Download GSOD CSV for one station/year.

    Parameters
    ----------
    station_id : str
        11-character station identifier (USAF + WBAN zero-padded).
    year : int
        Calendar year.
    raw_dir : str
        Directory to write ``{year}/{station_id}.csv``.
    timeout : int
        HTTP request timeout in seconds.
    retries : int
        Number of retry attempts on transient failure.

    Returns
    -------
    dict
        Keys: ``station_id``, ``year``, ``status`` ('ok'|'not_found'|'error'),
        ``rows``, ``path``, ``error``.
    """
    out_dir = os.path.join(raw_dir, str(year))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{station_id}.csv")

    # Skip if already downloaded
    if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
        try:
            df = pd.read_csv(out_path, nrows=1)
            return dict(station_id=station_id, year=year, status="cached",
                        rows=-1, path=out_path, error=None)
        except Exception:
            pass  # file corrupted; re-download

    url = gsod_url(station_id, year)
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 404:
                return dict(station_id=station_id, year=year, status="not_found",
                            rows=0, path=None, error="HTTP 404")
            resp.raise_for_status()
            with open(out_path, "wb") as fh:
                fh.write(resp.content)
            # Quick row count
            try:
                n = sum(1 for _ in open(out_path)) - 1
            except Exception:
                n = -1
            return dict(station_id=station_id, year=year, status="ok",
                        rows=n, path=out_path, error=None)
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))

    return dict(station_id=station_id, year=year, status="error",
                rows=0, path=None, error=last_error)


def download_gsod(
    station_map_path: str,
    raw_dir: str,
    years: list[int],
    log_path: str,
    timeout: int = 30,
    retries: int = 2,
) -> dict[str, Any]:
    """Download GSOD data for all mapped airports.

    Parameters
    ----------
    station_map_path : str
        Path to ``airport_station_map.csv``.
    raw_dir : str
        Root directory for raw GSOD files.
    years : list[int]
        Years to download (typically [2023, 2024]).
    log_path : str
        Path to write the download log JSON.
    timeout, retries : int
        Per-request timeout (s) and retry count.

    Returns
    -------
    dict
        Summary with counts of ok/cached/not_found/error and per-station log.
    """
    sm = pd.read_csv(station_map_path, dtype={"usaf": str})
    sm["station_id"] = sm["usaf"].str.zfill(6) + sm["wban"].astype(str).str.zfill(5)

    stations = sm["station_id"].unique().tolist()
    total = len(stations) * len(years)
    print(f"Downloading GSOD for {len(stations)} stations × {len(years)} years = {total} files ...")

    results = []
    ok = cached = not_found = errors = 0

    for year in years:
        for i, sid in enumerate(stations, 1):
            r = download_one_station(sid, year, raw_dir, timeout=timeout, retries=retries)
            results.append(r)
            status = r["status"]
            if status == "ok":
                ok += 1
            elif status == "cached":
                cached += 1
            elif status == "not_found":
                not_found += 1
            else:
                errors += 1

            done = (year - min(years)) * len(stations) + i
            print(f"\r  {done}/{total}  ok={ok} cached={cached} not_found={not_found} err={errors}",
                  end="", flush=True)

    print()  # newline

    summary = dict(
        total=total, ok=ok, cached=cached, not_found=not_found, errors=errors,
        stations=len(stations), years=years, results=results,
    )
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Download log: {log_path}")
    return summary


# ---------------------------------------------------------------------------
# Stage 2 — Preprocess
# ---------------------------------------------------------------------------


def parse_gsod_csv(path: str) -> pd.DataFrame | None:
    """Parse one raw GSOD CSV file and apply sentinel replacement + unit conversion.

    Returns None if the file is missing, empty, or unreadable.

    Unit conversions applied:
      TEMP, DEWP  °F → °C
      VISIB statute miles → km
      WDSP, MXSPD, GUST  knots → m/s
      PRCP inches → mm
    """
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"STATION": str})
    except Exception as exc:
        logger.warning("Could not parse %s: %s", path, exc)
        return None

    if df.empty or "DATE" not in df.columns:
        return None

    # Replace missing-value sentinels with NaN
    for col, sentinel in GSOD_SENTINELS.items():
        if col in df.columns:
            df[col] = df[col].where(df[col] != sentinel, other=np.nan)

    # Unit conversions (vectorised)
    if "TEMP" in df.columns:
        df["temp_c"] = (df["TEMP"] - 32) * 5 / 9
    if "DEWP" in df.columns:
        df["dewp_c"] = (df["DEWP"] - 32) * 5 / 9
    if "VISIB" in df.columns:
        df["visibility_km"] = df["VISIB"] * 1.60934
    if "WDSP" in df.columns:
        df["wind_ms"] = df["WDSP"] * 0.514444
    if "GUST" in df.columns:
        df["gust_ms"] = df["GUST"] * 0.514444
    if "PRCP" in df.columns:
        df["prcp_mm"] = df["PRCP"] * 25.4

    # Keep only what we need
    keep = ["STATION", "DATE", "temp_c", "dewp_c", "visibility_km",
            "wind_ms", "gust_ms", "prcp_mm"]
    keep = [c for c in keep if c in df.columns]
    df = df[keep].copy()

    # Parse DATE
    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    df = df.dropna(subset=["DATE"])

    return df


def build_weather_obs(
    raw_dir: str,
    station_map_path: str,
    output_path: str,
    years: list[int],
) -> dict[str, Any]:
    """Combine all raw GSOD CSVs into a single flat observation table.

    Parameters
    ----------
    raw_dir : str
        Root directory of raw GSOD files (subdirectories ``{year}/``).
    station_map_path : str
        Path to ``airport_station_map.csv``.
    output_path : str
        Destination for ``weather_obs.csv`` (station_id, date, 6 features).
    years : list[int]
        Years to include.

    Returns
    -------
    dict
        Coverage summary.
    """
    sm = pd.read_csv(station_map_path, dtype={"usaf": str})
    sm["station_id"] = sm["usaf"].str.zfill(6) + sm["wban"].astype(str).str.zfill(5)

    dfs: list[pd.DataFrame] = []
    parsed = 0
    skipped = 0
    total_rows = 0

    for year in years:
        for _, row in sm.iterrows():
            sid = row["station_id"]
            path = os.path.join(raw_dir, str(year), f"{sid}.csv")
            df = parse_gsod_csv(path)
            if df is None:
                skipped += 1
                continue
            df["station_id"] = sid
            df["iata"] = row["iata"]
            dfs.append(df)
            parsed += 1
            total_rows += len(df)

    if not dfs:
        print("WARNING: no GSOD files parsed — weather_obs.csv will be empty.")
        obs = pd.DataFrame(columns=["station_id", "iata", "DATE",
                                     "temp_c", "dewp_c", "visibility_km",
                                     "wind_ms", "gust_ms", "prcp_mm"])
    else:
        obs = pd.concat(dfs, ignore_index=True)

    # Deduplicate (same station + date should appear only once; keep first)
    n_before = len(obs)
    obs = obs.drop_duplicates(subset=["station_id", "DATE"], keep="first")
    n_dup = n_before - len(obs)

    # Sort
    obs = obs.sort_values(["iata", "DATE"]).reset_index(drop=True)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    obs.to_csv(output_path, index=False)

    print(f"Weather obs written: {output_path}  ({len(obs):,} rows)")

    return dict(
        parsed_files=parsed,
        skipped_files=skipped,
        total_obs_rows=len(obs),
        duplicate_rows_removed=n_dup,
        output_path=output_path,
    )


# ---------------------------------------------------------------------------
# Stage 3 — Feature generation
# ---------------------------------------------------------------------------


def build_weather_features(
    flights_path: str,
    weather_obs_path: str,
    station_map_path: str,
    output_path: str,
    chunksize: int = 200_000,
) -> dict[str, Any]:
    """Join prior-day GSOD weather onto flight records.

    Join strategy (prior-day lag — leakage-free)
    ---------------------------------------------
    For each flight with ``flight_date`` at ``origin``:

        weather_date = flight_date − 1 day

    The GSOD summary for ``weather_date`` was finalized at midnight before
    the flight's ``flight_date`` and is therefore fully available at any
    reasonable prediction time.

    Join key: ``(iata, DATE)`` where ``DATE == weather_date``.

    Cardinality guarantee: at most one GSOD row per (iata, date) after
    deduplication in Stage 2, so no flight row is multiplied.

    Parameters
    ----------
    flights_path : str
        Path to ``flights_rotation_eligible.csv``.
    weather_obs_path : str
        Path to ``weather_obs.csv`` (Stage 2 output).
    station_map_path : str
        Path to ``airport_station_map.csv`` (for station coverage info).
    output_path : str
        Destination for ``weather_features.csv``.
    chunksize : int
        Rows per chunk during flight CSV streaming.

    Returns
    -------
    dict
        Coverage and quality summary.
    """
    # Load weather obs into memory (it's small: ~343 stations × 2 years × 366 days)
    obs = pd.read_csv(weather_obs_path, dtype={"station_id": str})
    obs["DATE"] = pd.to_datetime(obs["DATE"])
    # Build lookup: (iata, date) -> feature row
    obs_lookup = obs.set_index(["iata", "DATE"])

    # Columns we will attach
    feature_cols = ["temp_c", "dewp_c", "visibility_km", "wind_ms", "gust_ms", "prcp_mm"]
    feature_cols = [c for c in feature_cols if c in obs_lookup.columns]

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    flight_id_counter = 0
    rows_written = 0
    rows_missing_weather = 0
    total_rows = 0
    first_chunk = True

    for chunk in pd.read_csv(
        flights_path,
        usecols=["flight_date", "reporting_airline", "flight_number",
                 "origin", "dest", "crs_dep_dt_utc"],
        chunksize=chunksize,
        low_memory=False,
    ):
        n = len(chunk)
        total_rows += n
        flight_ids = range(flight_id_counter, flight_id_counter + n)
        flight_id_counter += n

        chunk["flight_date"] = pd.to_datetime(chunk["flight_date"])
        # Prior-day weather date
        weather_dates = chunk["flight_date"] - pd.Timedelta(days=1)

        # Vectorised lookup via merge on (origin, weather_date)
        lookup_df = pd.DataFrame({
            "flight_id": list(flight_ids),
            "origin": chunk["origin"].values,
            "weather_date": weather_dates.values,
        })

        # Build the obs slice for the dates we need
        needed_dates = set(weather_dates.dt.normalize().unique())
        obs_slice = obs[obs["DATE"].isin(needed_dates)].copy()
        obs_slice = obs_slice.rename(columns={
            "temp_c": "temp_c_prev",
            "dewp_c": "dewp_c_prev",
            "visibility_km": "visibility_km_prev",
            "wind_ms": "wind_ms_prev",
            "gust_ms": "gust_ms_prev",
            "prcp_mm": "prcp_mm_prev",
        })

        merged = lookup_df.merge(
            obs_slice[["iata", "DATE"] + [c + "_prev" for c in feature_cols
                                           if c + "_prev" in obs_slice.columns]],
            left_on=["origin", "weather_date"],
            right_on=["iata", "DATE"],
            how="left",
        )

        # Count missing weather rows
        prev_cols = [c + "_prev" for c in feature_cols if c + "_prev" in merged.columns]
        if prev_cols:
            rows_missing_weather += int(merged[prev_cols[0]].isna().sum())

        # Drop redundant join columns, keep flight context
        out_cols = ["flight_id"] + prev_cols
        # Also keep the identity columns for downstream use
        merged["flight_date"] = chunk["flight_date"].values
        merged["reporting_airline"] = chunk["reporting_airline"].values
        merged["flight_number"] = chunk["flight_number"].values
        merged["origin"] = chunk["origin"].values
        merged["dest"] = chunk["dest"].values
        merged["crs_dep_dt_utc"] = chunk["crs_dep_dt_utc"].values
        merged["weather_date"] = weather_dates.dt.strftime("%Y-%m-%d").values

        out_cols = (["flight_id", "flight_date", "reporting_airline", "flight_number",
                     "origin", "dest", "crs_dep_dt_utc", "weather_date"] + prev_cols)
        out_cols = [c for c in out_cols if c in merged.columns]
        out = merged[out_cols].copy()

        mode = "w" if first_chunk else "a"
        out.to_csv(output_path, index=False, mode=mode, header=first_chunk)
        rows_written += len(out)
        first_chunk = False
        print(f"\r  Wrote {rows_written:,} / {total_rows:,} rows", end="", flush=True)

    print()

    pct_missing = 100 * rows_missing_weather / max(rows_written, 1)
    print(f"Weather features written: {output_path}  ({rows_written:,} rows)")
    print(f"  Rows missing prior-day weather: {rows_missing_weather:,} ({pct_missing:.2f}%)")

    # Post-hoc stats
    result_df = pd.read_csv(output_path)
    n_dup_ids = int(result_df["flight_id"].duplicated().sum())
    missing_per_feat = {}
    feat_stats = {}
    for feat in WEATHER_FEATURES:
        if feat in result_df.columns:
            col = result_df[feat].dropna()
            missing_per_feat[feat] = int(result_df[feat].isna().sum())
            if len(col) > 0:
                feat_stats[feat] = dict(
                    mean=round(float(col.mean()), 3),
                    min=round(float(col.min()), 3),
                    max=round(float(col.max()), 3),
                    p5=round(float(col.quantile(0.05)), 3),
                    p95=round(float(col.quantile(0.95)), 3),
                )

    return dict(
        total_flight_rows=total_rows,
        rows_written=rows_written,
        duplicate_flight_ids=n_dup_ids,
        rows_missing_weather=rows_missing_weather,
        pct_missing_weather=round(pct_missing, 2),
        missing_per_feature=missing_per_feat,
        feature_stats=feat_stats,
        output_path=output_path,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(argv: list[str] | None = None) -> int:
    """CLI entry point — runs all three stages."""
    parser = argparse.ArgumentParser(
        description=(
            "NOAA GSOD weather pipeline: download, preprocess, and join weather\n"
            "features onto the rotation-eligible flight dataset.\n"
            "\n"
            "Prior-day lag strategy: weather features use the GSOD summary for\n"
            "(flight_date - 1 day) so no future information enters the features."
        )
    )
    parser.add_argument("--station-map",
                        default=os.path.join("data", "weather", "airport_station_map.csv"))
    parser.add_argument("--flights",
                        default=os.path.join("data", "processed", "flights_rotation_eligible.csv"))
    parser.add_argument("--raw-dir",
                        default=os.path.join("data", "weather", "raw"))
    parser.add_argument("--obs-out",
                        default=os.path.join("data", "weather", "weather_obs.csv"))
    parser.add_argument("--features-out",
                        default=os.path.join("data", "processed", "weather_features.csv"))
    parser.add_argument("--download-log",
                        default=os.path.join("data", "weather", "download_log.json"))
    parser.add_argument("--report",
                        default=os.path.join("reports", "weather_features_report.md"))
    parser.add_argument("--years", nargs="+", type=int, default=[2023, 2024])
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip Stage 1 if raw files already present.")
    parser.add_argument("--chunksize", type=int, default=200_000)
    args = parser.parse_args(argv)

    print("=== Stage 1: Download GSOD ===")
    if args.skip_download:
        print("  Skipping download (--skip-download set).")
        dl_summary = {}
    else:
        dl_summary = download_gsod(
            station_map_path=args.station_map,
            raw_dir=args.raw_dir,
            years=args.years,
            log_path=args.download_log,
        )

    print("\n=== Stage 2: Build weather observations ===")
    obs_summary = build_weather_obs(
        raw_dir=args.raw_dir,
        station_map_path=args.station_map,
        output_path=args.obs_out,
        years=args.years,
    )

    print("\n=== Stage 3: Build weather features ===")
    feat_summary = build_weather_features(
        flights_path=args.flights,
        weather_obs_path=args.obs_out,
        station_map_path=args.station_map,
        output_path=args.features_out,
        chunksize=args.chunksize,
    )

    # Combined report
    full_summary = dict(
        download=dl_summary,
        observations=obs_summary,
        features=feat_summary,
    )
    json_path = args.report.replace(".md", ".json")
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(full_summary, f, indent=2, default=str)

    _write_report(full_summary, args.report)

    print(f"\n=== Summary ===")
    print(f"  Observations: {obs_summary.get('total_obs_rows',0):,} rows in {args.obs_out}")
    print(f"  Features:     {feat_summary.get('rows_written',0):,} rows in {args.features_out}")
    print(f"  Missing wx:   {feat_summary.get('rows_missing_weather',0):,} "
          f"({feat_summary.get('pct_missing_weather',0):.2f}%)")
    print(f"  Dup flight_ids: {feat_summary.get('duplicate_flight_ids',0)}")
    print(f"  Report: {args.report}")
    return 0


def _write_report(summary: dict, path: str) -> None:
    feat = summary.get("features", {})
    obs  = summary.get("observations", {})
    dl   = summary.get("download", {})

    lines = [
        "# Weather Features — Data Quality and Coverage Report",
        "",
        "Generated by `src/weather_features.py`.",
        "Owned by Dhanush R (Person 3).",
        "",
        "---",
        "",
        "## 1. Prediction-Time Design",
        "",
        "**Strategy: prior-day GSOD lag (leakage-free)**",
        "",
        "GSOD provides daily summaries averaged over the **entire local calendar day**.",
        "Using same-day GSOD for an early-morning departure would include post-departure",
        "observations (future information).",
        "",
        "This pipeline joins on:",
        "```",
        "weather_date = flight_date − 1 day",
        "```",
        "Features carry the `_prev` suffix to make the lag explicit.",
        "",
        "---",
        "",
        "## 2. Feature Definitions",
        "",
        "| Feature | Source field | Units | Description |",
        "|---|---|---|---|",
        "| `temp_c_prev` | GSOD TEMP | °C | Mean air temperature, prior day |",
        "| `dewp_c_prev` | GSOD DEWP | °C | Mean dew point, prior day |",
        "| `visibility_km_prev` | GSOD VISIB | km | Mean visibility, prior day |",
        "| `wind_ms_prev` | GSOD WDSP | m/s | Mean wind speed, prior day |",
        "| `gust_ms_prev` | GSOD GUST | m/s | Max wind gust, prior day (NA if no obs) |",
        "| `prcp_mm_prev` | GSOD PRCP | mm | Total precipitation, prior day |",
        "",
        "Unit conversions: °F→°C, statute miles→km, knots→m/s, inches→mm.",
        "",
        "---",
        "",
        "## 3. Download Coverage",
        "",
    ]
    if dl:
        lines += [
            f"- Stations: {dl.get('stations','?')}",
            f"- Years: {dl.get('years','?')}",
            f"- Files OK: {dl.get('ok',0)}  Cached: {dl.get('cached',0)}  "
            f"Not found: {dl.get('not_found',0)}  Errors: {dl.get('errors',0)}",
        ]
    else:
        lines.append("Download skipped (--skip-download).")

    lines += [
        "",
        "---",
        "",
        "## 4. Observation Coverage",
        "",
        f"- Files parsed: {obs.get('parsed_files','?')}",
        f"- Files skipped: {obs.get('skipped_files','?')}",
        f"- Total observations: {obs.get('total_obs_rows','?'):,}" if obs.get('total_obs_rows') else "- Total observations: ?",
        f"- Duplicate rows removed: {obs.get('duplicate_rows_removed','?')}",
        "",
        "---",
        "",
        "## 5. Feature Statistics",
        "",
        "| Feature | Missing | Mean | Min | Max | P5 | P95 |",
        "|---|---|---|---|---|---|---|",
    ]
    for feat_name in ["temp_c_prev","dewp_c_prev","visibility_km_prev",
                       "wind_ms_prev","gust_ms_prev","prcp_mm_prev"]:
        ms = feat.get("missing_per_feature", {}).get(feat_name, "?")
        st = feat.get("feature_stats", {}).get(feat_name, {})
        lines.append(
            f"| `{feat_name}` | {ms} | {st.get('mean','?')} | "
            f"{st.get('min','?')} | {st.get('max','?')} | "
            f"{st.get('p5','?')} | {st.get('p95','?')} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 6. Leakage Assessment",
        "",
        "- All features use **prior-day GSOD** (`flight_date − 1`).",
        "- No same-day or post-departure observations are used.",
        "- `dep_dt_utc` and `arr_dt_utc` are NOT read by this pipeline.",
        "",
        "---",
        "",
        "## 7. Join Cardinality",
        "",
        f"- Input flight rows: {feat.get('total_flight_rows','?'):,}" if feat.get('total_flight_rows') else "- Input flight rows: ?",
        f"- Output rows: {feat.get('rows_written','?'):,}" if feat.get('rows_written') else "- Output rows: ?",
        f"- Duplicate flight_id values: **{feat.get('duplicate_flight_ids','?')}**",
        f"- Rows missing prior-day weather: {feat.get('rows_missing_weather','?'):,} "
        f"({feat.get('pct_missing_weather','?')}%)" if feat.get('rows_missing_weather') is not None else "",
        "",
        f"*Report generated {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} UTC.*",
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(run())
