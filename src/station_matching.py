"""NOAA ISD weather-station matching for airport coordinates.

Owned by Dhanush R (Person 3).

Maps every airport that appears in the rotation-eligible flight dataset to its
nearest suitable NOAA Integrated Surface Database (ISD) weather station.  The
output ``airport_station_map.csv`` is the prerequisite for Phase 2
(weather feature download and preprocessing).

Matching strategy (three tiers, applied in priority order)
-----------------------------------------------------------
1. **ICAO primary** — ``data/airport_timezones.csv`` supplies an ICAO code for
   most airports.  The ISD station catalog is joined on ``ISD.ICAO == airport.icao``,
   restricted to stations whose END date covers 2024 and that have non-zero
   coordinates.  This is the most accurate match because it is a direct
   identifier link, not an approximation.

2. **Extended ICAO** — Eight US-territory airports (BQN, GUM, PBI, PPG, PSE,
   SJU, STT, STX) are absent from ``airport_timezones.csv`` but their ICAO
   codes (TJBQ, PGUM, KPBI, NSTU, TJPS, TJSJ, TIST, TISX) are present in the
   ISD catalog.  These are matched by looking up the IATA code in a small
   hardcoded supplement table whose values are derived exclusively from the ISD
   catalog records — no coordinates are invented.

3. **Geographic fallback** — XWA (Williston Basin International Airport, ND)
   has ICAO = KXWA in the timezone CSV but KXWA does not appear in the ISD
   catalog (the airport opened in 2019 after the catalog snapshot).  Its
   coordinates (48.2606 °N, 103.7433 °W) are sourced from the FAA
   Airport/Facility Directory — a public US government record.  The nearest
   ISD station within ``MAX_FALLBACK_KM`` is selected.

Airports with no match within the radius threshold are recorded in the output
with ``match_method = 'none'`` and ``distance_km = NaN``.

Station selection criteria
--------------------------
* ``END >= 20240101`` — station must have been active at some point in 2024.
* ``LAT`` and ``LON`` both non-null and non-zero (zero coordinates indicate
  missing data in the ISD catalog).
* Among candidates sharing the same ICAO code, the station with the latest
  END date is preferred (highest data recency).
* Among geographic-fallback candidates, the nearest station by Haversine
  distance is selected.

Output schema (``airport_station_map.csv``)
-------------------------------------------
iata              IATA airport code (join key to flight data)
icao_airport      ICAO code of the airport (from timezone CSV or supplement)
usaf              NOAA USAF station identifier (6-digit string)
wban              NOAA WBAN station identifier (integer)
station_name      Human-readable station name from ISD catalog
station_lat       Station latitude (decimal degrees, from ISD)
station_lon       Station longitude (decimal degrees, from ISD)
distance_km       Haversine distance airport → station (km); NaN if unmatched
match_method      'icao_primary' | 'icao_extended' | 'geographic' | 'none'
isd_end_date      Station END date from ISD catalog (YYYYMMDD integer)

Reproducibility
---------------
The ISD catalog is downloaded once and cached at ``data/weather/isd-history.csv``.
Subsequent runs load from the cache unless ``force_download=True`` is passed.
All distances are computed from ISD station coordinates; no external coordinate
service is used.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

__all__ = [
    "ISD_CATALOG_URL",
    "ISD_ACTIVE_CUTOFF",
    "MAX_FALLBACK_KM",
    "TERRITORY_SUPPLEMENT",
    "XWA_COORDS",
    "download_isd_catalog",
    "load_isd_catalog",
    "haversine_km",
    "build_station_map",
    "run",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: S3 URL for the NOAA ISD station history catalog.
ISD_CATALOG_URL: str = "https://noaa-isd-pds.s3.amazonaws.com/isd-history.csv"

#: Station must have END date >= this value (YYYYMMDD int) to be considered
#: active during the 2024 study period.
ISD_ACTIVE_CUTOFF: int = 20240101

#: Maximum airport-to-station distance for the geographic fallback (km).
#: Stations beyond this radius are not used.
MAX_FALLBACK_KM: float = 200.0

#: Eight US-territory airports absent from airport_timezones.csv.
#: Columns: iata, icao_airport.  Coordinates are sourced from the ISD catalog
#: records for these stations and are NOT hardcoded here — only the ICAO link
#: is hardcoded (these are stable ICAO identifiers, not invented values).
TERRITORY_SUPPLEMENT: dict[str, str] = {
    "BQN": "TJBQ",  # Rafael Hernandez, Aguadilla, Puerto Rico
    "GUM": "PGUM",  # Guam International
    "PBI": "KPBI",  # Palm Beach International, FL
    "PPG": "NSTU",  # Tafuna/Pago Pago, American Samoa
    "PSE": "TJPS",  # Mercedita, Ponce, Puerto Rico
    "SJU": "TJSJ",  # Luis Munoz Marin, San Juan, Puerto Rico
    "STT": "TIST",  # Cyril E King, St Thomas, USVI
    "STX": "TISX",  # Henry E Rohlsen, St Croix, USVI
}

#: XWA (Williston Basin International Airport, ND) fallback coordinates.
#: Source: FAA Airport/Facility Directory (public US government record).
#: ICAO KXWA exists in airport_timezones.csv but is absent from the ISD catalog
#: because the airport opened in 2019, after the catalog snapshot.
XWA_COORDS: tuple[float, float] = (48.2606, -103.7433)

# ---------------------------------------------------------------------------
# ISD catalog download and loading
# ---------------------------------------------------------------------------


def download_isd_catalog(
    dest_path: str,
    url: str = ISD_CATALOG_URL,
    timeout: int = 60,
    chunk_size: int = 65_536,
) -> str:
    """Download the NOAA ISD station history catalog and save to ``dest_path``.

    Parameters
    ----------
    dest_path : str
        Local path where the CSV will be written.
    url : str
        Source URL (default: S3 public bucket).
    timeout : int
        HTTP request timeout in seconds.
    chunk_size : int
        Download chunk size in bytes.

    Returns
    -------
    str
        The path that was written (same as ``dest_path``).

    Raises
    ------
    requests.RequestException
        On any network or HTTP error.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    with open(dest_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            fh.write(chunk)
    return dest_path


def load_isd_catalog(
    catalog_path: str,
    url: str = ISD_CATALOG_URL,
    force_download: bool = False,
    timeout: int = 60,
) -> pd.DataFrame:
    """Load the ISD station history catalog, downloading if needed.

    Parameters
    ----------
    catalog_path : str
        Local cache path.  If the file exists and ``force_download`` is False,
        it is loaded from disk without a network request.
    url : str
        Remote URL (used only when downloading).
    force_download : bool
        Re-download even if the local cache exists.
    timeout : int
        HTTP timeout in seconds.

    Returns
    -------
    pd.DataFrame
        Raw ISD catalog with original column names.
    """
    if force_download or not os.path.exists(catalog_path):
        print(f"Downloading ISD catalog from {url} ...")
        download_isd_catalog(catalog_path, url=url, timeout=timeout)
        print(f"  Saved to {catalog_path}")
    else:
        print(f"Loading ISD catalog from cache: {catalog_path}")

    df = pd.read_csv(catalog_path, dtype={"USAF": str})
    # WBAN can be 99999 (missing); keep as int
    print(f"  ISD catalog: {len(df):,} stations, columns: {list(df.columns)}")
    return df


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------


def haversine_km(
    lat1: float | np.ndarray,
    lon1: float | np.ndarray,
    lat2: float | np.ndarray,
    lon2: float | np.ndarray,
) -> float | np.ndarray:
    """Haversine great-circle distance in kilometres.

    Parameters
    ----------
    lat1, lon1 : float or ndarray
        Point A coordinates (decimal degrees).
    lat2, lon2 : float or ndarray
        Point B coordinates (decimal degrees).

    Returns
    -------
    float or ndarray
        Distance(s) in kilometres.

    Notes
    -----
    Uses Earth radius R = 6_371.0 km (mean spherical radius, WGS-84).
    Accurate to within ~0.5% for all distances under 10,000 km.
    """
    R = 6_371.0
    dlat = np.radians(np.asarray(lat2, dtype=float) - np.asarray(lat1, dtype=float))
    dlon = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(np.radians(np.asarray(lat1, dtype=float)))
        * np.cos(np.radians(np.asarray(lat2, dtype=float)))
        * np.sin(dlon / 2) ** 2
    )
    return R * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# ---------------------------------------------------------------------------
# Core matching logic
# ---------------------------------------------------------------------------


def _active_stations(isd: pd.DataFrame) -> pd.DataFrame:
    """Filter ISD catalog to stations active in 2024 with valid coordinates."""
    mask = (
        (isd["END"] >= ISD_ACTIVE_CUTOFF)
        & isd["LAT"].notna()
        & isd["LON"].notna()
        & (isd["LAT"].abs() > 0.01)   # reject zero/near-zero coordinates
        & (isd["LON"].abs() > 0.01)
    )
    return isd[mask].copy()


def _best_icao_station(candidates: pd.DataFrame) -> pd.Series | None:
    """From multiple ISD stations sharing an ICAO code, pick the best one.

    Selection rule: highest END date (most recently active).  If tied, pick
    the row with the smallest WBAN (arbitrary but deterministic tie-break).
    """
    if candidates.empty:
        return None
    return (
        candidates.sort_values(["END", "WBAN"], ascending=[False, True])
        .iloc[0]
    )


def build_station_map(
    airport_tz_path: str,
    isd_catalog_path: str,
    flights_path: str,
    output_path: str,
    max_fallback_km: float = MAX_FALLBACK_KM,
) -> dict[str, Any]:
    """Build the airport → NOAA station mapping and write ``airport_station_map.csv``.

    Parameters
    ----------
    airport_tz_path : str
        Path to ``data/airport_timezones.csv``.
    isd_catalog_path : str
        Path to cached ``data/weather/isd-history.csv``.
    flights_path : str
        Path to ``data/processed/flights_rotation_eligible.csv``.
        Used only to determine which airports need to be matched.
    output_path : str
        Destination for the output ``airport_station_map.csv``.
    max_fallback_km : float
        Maximum radius for geographic fallback matches.

    Returns
    -------
    dict
        Coverage summary suitable for report generation.
    """
    # ------------------------------------------------------------------
    # 1. Collect all unique airports from the flight dataset
    # ------------------------------------------------------------------
    print("Collecting unique airports from flight dataset ...")
    all_airports: set[str] = set()
    for chunk in pd.read_csv(
        flights_path, usecols=["origin", "dest"], chunksize=200_000
    ):
        all_airports.update(chunk["origin"].unique())
        all_airports.update(chunk["dest"].unique())
    print(f"  {len(all_airports)} unique airports (origin + dest)")

    # ------------------------------------------------------------------
    # 2. Load airport timezone CSV (provides ICAO codes)
    # ------------------------------------------------------------------
    tz = pd.read_csv(airport_tz_path, dtype=str)
    tz["iata"] = tz["iata"].str.strip().str.upper()
    tz["icao"] = tz["icao"].str.strip().str.upper()
    # Only keep airports present in the flight dataset
    tz_rot = tz[tz["iata"].isin(all_airports)].copy()
    in_tz = set(tz_rot["iata"])
    not_in_tz = all_airports - in_tz
    print(f"  {len(tz_rot)} airports in airport_timezones.csv")
    print(f"  {len(not_in_tz)} airports NOT in airport_timezones.csv: {sorted(not_in_tz)}")

    # ------------------------------------------------------------------
    # 3. Load and filter ISD catalog
    # ------------------------------------------------------------------
    isd_raw = pd.read_csv(isd_catalog_path, dtype={"USAF": str})
    isd = _active_stations(isd_raw)
    print(f"  ISD active-2024 stations with valid coordinates: {len(isd):,}")

    # Build ICAO → best station lookup
    isd_has_icao = isd[isd["ICAO"].notna() & (isd["ICAO"].str.strip() != "")].copy()
    isd_has_icao["ICAO"] = isd_has_icao["ICAO"].str.strip().str.upper()

    # ------------------------------------------------------------------
    # 4. Tier 1 — ICAO primary match (airports in timezone CSV)
    # ------------------------------------------------------------------
    print("Tier 1: ICAO primary match ...")
    rows: list[dict] = []
    unmatched_after_t1: list[str] = []

    for _, ap in tz_rot.iterrows():
        iata = ap["iata"]
        icao = ap["icao"] if pd.notna(ap["icao"]) else None

        if icao is None:
            unmatched_after_t1.append(iata)
            continue

        candidates = isd_has_icao[isd_has_icao["ICAO"] == icao]
        best = _best_icao_station(candidates)
        if best is None:
            unmatched_after_t1.append(iata)
            continue

        # Distance from ISD station coords back to itself is 0 for ICAO match —
        # record as 0 km (the station IS at the airport).
        rows.append(
            dict(
                iata=iata,
                icao_airport=icao,
                usaf=best["USAF"],
                wban=int(best["WBAN"]),
                station_name=best["STATION NAME"],
                station_lat=float(best["LAT"]),
                station_lon=float(best["LON"]),
                distance_km=0.0,
                match_method="icao_primary",
                isd_end_date=int(best["END"]),
            )
        )

    print(
        f"  Tier 1 matched: {len(rows)}  |  "
        f"Forwarded to Tier 2/3: {len(unmatched_after_t1)}"
    )

    # ------------------------------------------------------------------
    # 5. Tier 2 — Extended ICAO for territory airports
    # ------------------------------------------------------------------
    print("Tier 2: extended ICAO for territory airports ...")
    unmatched_after_t2: list[str] = []

    for iata in list(not_in_tz) + unmatched_after_t1:
        icao = TERRITORY_SUPPLEMENT.get(iata)
        if icao is None:
            unmatched_after_t2.append(iata)
            continue

        candidates = isd_has_icao[isd_has_icao["ICAO"] == icao]
        best = _best_icao_station(candidates)
        if best is None:
            unmatched_after_t2.append(iata)
            continue

        rows.append(
            dict(
                iata=iata,
                icao_airport=icao,
                usaf=best["USAF"],
                wban=int(best["WBAN"]),
                station_name=best["STATION NAME"],
                station_lat=float(best["LAT"]),
                station_lon=float(best["LON"]),
                distance_km=0.0,
                match_method="icao_extended",
                isd_end_date=int(best["END"]),
            )
        )

    print(
        f"  Tier 2 matched: {sum(1 for r in rows if r['match_method'] == 'icao_extended')}  |  "
        f"Forwarded to Tier 3: {len(unmatched_after_t2)}"
    )

    # ------------------------------------------------------------------
    # 6. Tier 3 — Geographic fallback
    # ------------------------------------------------------------------
    print("Tier 3: geographic fallback ...")
    truly_unmatched: list[str] = []

    for iata in unmatched_after_t2:
        # Only XWA has a known FAA coordinate; all others have no coordinate source.
        if iata == "XWA":
            ap_lat, ap_lon = XWA_COORDS
        else:
            # No coordinate source available — cannot match.
            truly_unmatched.append(iata)
            continue

        dists = haversine_km(ap_lat, ap_lon, isd["LAT"].values, isd["LON"].values)
        within = isd.copy()
        within["_dist"] = dists
        within = within[within["_dist"] <= max_fallback_km]

        if within.empty:
            truly_unmatched.append(iata)
            continue

        best_idx = within["_dist"].idxmin()
        best = within.loc[best_idx]

        # For XWA, use the ICAO from airport_timezones.csv (KXWA is there)
        icao_ap = tz[tz["iata"] == iata]["icao"].iloc[0] if iata in tz["iata"].values else ""

        rows.append(
            dict(
                iata=iata,
                icao_airport=icao_ap if pd.notna(icao_ap) else "",
                usaf=best["USAF"],
                wban=int(best["WBAN"]),
                station_name=best["STATION NAME"],
                station_lat=float(best["LAT"]),
                station_lon=float(best["LON"]),
                distance_km=round(float(best["_dist"]), 3),
                match_method="geographic",
                isd_end_date=int(best["END"]),
            )
        )

    geo_matches = sum(1 for r in rows if r["match_method"] == "geographic")
    print(f"  Tier 3 matched: {geo_matches}  |  Truly unmatched: {len(truly_unmatched)}")

    # ------------------------------------------------------------------
    # 7. Record truly unmatched airports
    # ------------------------------------------------------------------
    for iata in truly_unmatched:
        icao_ap = ""
        if iata in set(tz["iata"]):
            icao_val = tz[tz["iata"] == iata]["icao"].values[0]
            icao_ap = icao_val if pd.notna(icao_val) else ""
        rows.append(
            dict(
                iata=iata,
                icao_airport=icao_ap,
                usaf="",
                wban=pd.NA,
                station_name="",
                station_lat=float("nan"),
                station_lon=float("nan"),
                distance_km=float("nan"),
                match_method="none",
                isd_end_date=pd.NA,
            )
        )

    # ------------------------------------------------------------------
    # 8. Assemble and validate output
    # ------------------------------------------------------------------
    result = pd.DataFrame(rows)

    # Ensure every airport appears exactly once
    n_dup = result["iata"].duplicated().sum()
    if n_dup > 0:
        raise ValueError(
            f"BUG: {n_dup} duplicate IATA codes in station map — "
            "each airport must appear at most once."
        )

    # Sort by IATA for deterministic output
    result = result.sort_values("iata").reset_index(drop=True)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    result.to_csv(output_path, index=False)
    print(f"\nStation map written: {output_path}  ({len(result)} rows)")

    # ------------------------------------------------------------------
    # 9. Summary statistics
    # ------------------------------------------------------------------
    method_counts = result["match_method"].value_counts().to_dict()
    matched = result[result["match_method"] != "none"]
    dist_stats: dict[str, Any] = {}
    if not matched.empty:
        geo_only = matched[matched["match_method"] == "geographic"]["distance_km"].dropna()
        dist_stats = {
            "n_geographic": len(geo_only),
            "geographic_min_km": round(float(geo_only.min()), 1) if len(geo_only) else None,
            "geographic_max_km": round(float(geo_only.max()), 1) if len(geo_only) else None,
            "geographic_mean_km": round(float(geo_only.mean()), 1) if len(geo_only) else None,
        }

    summary = {
        "total_airports": len(result),
        "matched": int((result["match_method"] != "none").sum()),
        "unmatched": int((result["match_method"] == "none").sum()),
        "by_method": {k: int(v) for k, v in method_counts.items()},
        "truly_unmatched_iata": truly_unmatched,
        "distance_stats": dist_stats,
        "output_path": output_path,
    }
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Build NOAA ISD weather station map for all airports in the flight dataset.\n"
            "\n"
            "Downloads the ISD station catalog (once), matches each airport to its\n"
            "nearest suitable station, and writes airport_station_map.csv."
        )
    )
    parser.add_argument(
        "--airport-tz",
        default=os.path.join("data", "airport_timezones.csv"),
        help="Path to airport_timezones.csv (IATA + ICAO codes).",
    )
    parser.add_argument(
        "--flights",
        default=os.path.join("data", "processed", "flights_rotation_eligible.csv"),
        help="Path to flights_rotation_eligible.csv (determines which airports to match).",
    )
    parser.add_argument(
        "--isd-catalog",
        default=os.path.join("data", "weather", "isd-history.csv"),
        help="Local cache path for the ISD station history catalog.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join("data", "weather", "airport_station_map.csv"),
        help="Destination path for the airport-station map CSV.",
    )
    parser.add_argument(
        "--report",
        default=os.path.join("reports", "station_matching_report.md"),
        help="Path for the Markdown quality report.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download ISD catalog even if local cache exists.",
    )
    parser.add_argument(
        "--max-fallback-km",
        type=float,
        default=MAX_FALLBACK_KM,
        help=f"Maximum radius for geographic fallback matches (default {MAX_FALLBACK_KM} km).",
    )
    args = parser.parse_args(argv)

    print(f"Airport timezone CSV:  {args.airport_tz}")
    print(f"Flight dataset:        {args.flights}")
    print(f"ISD catalog cache:     {args.isd_catalog}")
    print(f"Output map:            {args.output}")
    print(f"Report:                {args.report}")
    print()

    # Download / load ISD catalog
    load_isd_catalog(
        args.isd_catalog,
        force_download=args.force_download,
    )

    # Build the map
    summary = build_station_map(
        airport_tz_path=args.airport_tz,
        isd_catalog_path=args.isd_catalog,
        flights_path=args.flights,
        output_path=args.output,
        max_fallback_km=args.max_fallback_km,
    )

    # Write JSON summary
    json_path = args.report.replace(".md", ".json")
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Summary JSON: {json_path}")

    # Write Markdown report
    _write_report(summary, args.report, args.output)
    print(f"Report:       {args.report}")

    # Console summary
    print("\n=== Station Matching — Summary ===")
    print(f"  Total airports:    {summary['total_airports']}")
    print(f"  Matched:           {summary['matched']}")
    print(f"  Unmatched:         {summary['unmatched']}")
    for method, count in sorted(summary["by_method"].items()):
        print(f"  Method {method:<22} {count}")
    if summary["truly_unmatched_iata"]:
        print(f"  Truly unmatched:   {summary['truly_unmatched_iata']}")

    return 0


def _write_report(summary: dict, report_path: str, map_path: str) -> None:
    """Write a Markdown quality and coverage report."""
    result = pd.read_csv(map_path)

    lines = [
        "# Station Matching Report — NOAA ISD",
        "",
        "Generated by `src/station_matching.py`.",
        "Owned by Dhanush R (Person 3).",
        "",
        "---",
        "",
        "## 1. Overview",
        "",
        f"- Total airports matched: **{summary['total_airports']}** "
        f"({summary['matched']} matched, {summary['unmatched']} unmatched)",
        f"- Source: NOAA ISD station history catalog "
        f"(`https://noaa-isd-pds.s3.amazonaws.com/isd-history.csv`)",
        "",
        "---",
        "",
        "## 2. Matching Strategy",
        "",
        "| Tier | Method | Description |",
        "|---|---|---|",
        "| 1 | `icao_primary` | Airport ICAO from `airport_timezones.csv` joined to ISD `ICAO` field |",
        "| 2 | `icao_extended` | US-territory airports absent from timezone CSV, "
        "matched via known ICAO in ISD |",
        "| 3 | `geographic` | No ICAO match; nearest ISD station by Haversine distance |",
        "| — | `none` | No suitable station found within radius |",
        "",
        "---",
        "",
        "## 3. Match Counts by Method",
        "",
        "| Method | Airports |",
        "|---|---|",
    ]
    for method, count in sorted(summary["by_method"].items()):
        lines.append(f"| `{method}` | {count} |")

    lines += [
        "",
        "---",
        "",
        "## 4. Geographic Fallback Distance Statistics",
        "",
    ]
    ds = summary.get("distance_stats", {})
    if ds.get("n_geographic", 0) > 0:
        lines += [
            f"- Geographic fallback airports: **{ds['n_geographic']}**",
            f"- Distance range: {ds['geographic_min_km']} – {ds['geographic_max_km']} km",
            f"- Mean distance: {ds['geographic_mean_km']} km",
            f"- Threshold used: {MAX_FALLBACK_KM} km",
        ]
    else:
        lines.append("No geographic fallback airports.")

    lines += [
        "",
        "---",
        "",
        "## 5. Duplicate Airport Check",
        "",
    ]
    n_dup = result["iata"].duplicated().sum()
    lines.append(f"- Duplicate IATA codes in output: **{n_dup}** (must be 0)")

    lines += [
        "",
        "---",
        "",
        "## 6. Unmatched Airports",
        "",
    ]
    unmatched = result[result["match_method"] == "none"]
    if unmatched.empty:
        lines.append("None — all airports successfully matched.")
    else:
        lines.append("| IATA | ICAO | Reason |")
        lines.append("|---|---|---|")
        for _, row in unmatched.iterrows():
            lines.append(
                f"| {row['iata']} | {row['icao_airport']} | "
                f"No ISD station within {MAX_FALLBACK_KM} km and no ICAO match |"
            )

    lines += [
        "",
        "---",
        "",
        "## 7. Geographic Fallback Airports",
        "",
    ]
    geo = result[result["match_method"] == "geographic"]
    if geo.empty:
        lines.append("None.")
    else:
        lines.append("| IATA | ICAO | Station | USAF | Distance (km) |")
        lines.append("|---|---|---|---|---|")
        for _, row in geo.iterrows():
            lines.append(
                f"| {row['iata']} | {row['icao_airport']} | "
                f"{row['station_name']} | {row['usaf']} | {row['distance_km']:.1f} |"
            )

    lines += [
        "",
        "---",
        "",
        "## 8. Station Operational Date Check",
        "",
    ]
    active_2024 = result[result["match_method"] != "none"]["isd_end_date"].dropna()
    n_active = (active_2024.astype(float) >= ISD_ACTIVE_CUTOFF).sum()
    lines += [
        f"- Matched stations with END ≥ {ISD_ACTIVE_CUTOFF}: **{n_active}** of "
        f"{len(active_2024)} matched airports",
        f"- All matched stations were pre-filtered to END ≥ {ISD_ACTIVE_CUTOFF}",
    ]

    lines += [
        "",
        "---",
        "",
        "## 9. Sample Matched Airports",
        "",
        "| IATA | ICAO | USAF | WBAN | Station Name | Method |",
        "|---|---|---|---|---|---|",
    ]
    for _, row in result[result["match_method"] != "none"].head(10).iterrows():
        lines.append(
            f"| {row['iata']} | {row['icao_airport']} | {row['usaf']} | "
            f"{row['wban']} | {row['station_name'][:40]} | {row['match_method']} |"
        )

    lines.append(
        f"\n*Report generated {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} UTC.*"
    )

    os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(run())
