"""Initial cleaning of BTS On-Time Performance flight records.

Owned by Thrupthi G D. This module standardizes the raw BTS columns, validates
records, reconstructs consistent LOCAL timestamps, flags data-quality issues,
removes exact duplicate rows (and only those), and writes a cleaned
flight-level dataset plus a reproducible data-quality report.

Scope and important decisions
-----------------------------
* Preserve the raw file unchanged. Everything is written to data/processed/.
* Standardize column names to snake_case without losing meaning. The original
  -> standardized mapping is written to data/processed/column_mapping.json for
  traceability.
* Timestamps are reconstructed from the SCHEDULED departure, not by subtracting
  local hhmm strings. This is the critical correctness decision:

      crs_dep_dt = flight_date + crs_dep_time        (local, hhmm)
      dep_dt     = crs_dep_dt + dep_delay (minutes)   (signed delay handles midnight)
      crs_arr_dt = crs_dep_dt + crs_elapsed_time      (minutes)
      arr_dt     = dep_dt + actual_elapsed_time        (minutes)

  Using BTS's pre-computed minute delays/elapsed times avoids the classic
  midnight-crossing error (you must not subtract local clock strings directly).
* All datetimes are LOCAL airport time and are timezone-NAIVE (they carry no
  UTC offset). Converting these local times to a common, timezone-aware
  reference (UTC) so flights at different airports can be compared and sorted is
  the responsibility of the aircraft-rotation module (Amulya). That step needs
  a per-airport timezone map and daylight-saving handling, which is out of
  scope for initial cleaning. See docs/data_dictionary.md for details.
* Units are minutes for all delay/elapsed/taxi/air-time fields and miles for
  distance.
* Exclusions are documented, never silent:
    - Exact duplicate rows are removed (a data error) and logged to
      data/processed/duplicate_records.csv.
    - Missing tail numbers, invalid timestamps, cancelled, diverted, and
      implausible records are NOT deleted; they are flagged with boolean
      columns and an ``exclusion_reason`` string. Downstream code (e.g. the
      rotation module) filters on the ``rotation_eligible`` flag.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from typing import Any

import numpy as np
import pandas as pd

from src.data_ingestion import ALIASES, COLUMN_MAP, REQUIRED_FIELDS, basic_info

__all__ = [
    "parse_hhmm",
    "standardize_columns",
    "coerce_types",
    "build_timestamps",
    "validate_records",
    "detect_duplicates",
    "clean",
    "generate_quality_report",
    "main",
]

# Numeric fields (minutes / miles / counts / ids) coerced from string.
NUMERIC_FIELDS = [
    "year", "quarter", "month", "day_of_month", "day_of_week",
    "dot_id_reporting_airline", "origin_airport_id", "origin_airport_seq_id",
    "origin_city_market_id", "origin_state_fips", "origin_wac",
    "dest_airport_id", "dest_airport_seq_id", "dest_city_market_id",
    "dest_state_fips", "dest_wac",
    "dep_delay", "dep_delay_minutes", "departure_delay_groups",
    "arr_delay", "arr_delay_minutes", "arrival_delay_groups",
    "taxi_out", "taxi_in", "wheels_off", "wheels_on",
    "crs_elapsed_time", "actual_elapsed_time", "air_time", "flights",
    "distance", "distance_group",
    "carrier_delay", "weather_delay", "nas_delay", "security_delay",
    "late_aircraft_delay", "total_add_gtime", "longest_add_gtime",
    "div_airport_landings", "div_actual_elapsed_time", "div_arr_delay",
    "div_distance",
]

# 0/1 indicator fields -> nullable integer.
INDICATOR_FIELDS = ["cancelled", "diverted", "dep_del15", "arr_del15"]

# Plausibility bounds (minutes / miles) used to flag implausible records.
MAX_ELAPSED_MIN = 24 * 60      # a domestic leg should not take more than 24h
MAX_DELAY_MIN = 24 * 60        # a single delay > 24h is implausible
MAX_DISTANCE_MI = 6000         # domestic non-stop max is well under this
MIN_TAIL_LEN = 1
MAX_TAIL_LEN = 6              # N-numbers are at most 6 characters


def parse_hhmm(value: Any) -> float:
    """Parse a BTS hhmm local time into minutes-since-midnight.

    Returns NaN for missing/invalid input. 2400 is treated as 1440 minutes
    (midnight at the end of the day, i.e. start of the next day).

    Examples: "735" -> 455, "1430" -> 870, "2400" -> 1440, "" -> NaN, "2500" -> NaN.
    """
    if value is None:
        return np.nan
    try:
        if isinstance(value, str):
            v = value.strip()
            if v == "" or v.lower() == "nan":
                return np.nan
            f = float(v)
        elif isinstance(value, (int, float, np.integer, np.floating)):
            f = float(value)
        else:
            return np.nan
        if math.isnan(f):
            return np.nan
        n = int(round(f))
    except (ValueError, TypeError):
        return np.nan
    if n == 2400:
        return 1440.0
    if n < 0 or n > 2400:
        return np.nan
    hh, mm = n // 100, n % 100
    if hh > 23 or mm > 59:
        return np.nan
    return float(hh * 60 + mm)


# ---------------------------------------------------------------------------
# Step 1: standardize column names
# ---------------------------------------------------------------------------
def _resolve_name(col: str) -> str:
    """Map a raw column name to its standardized snake_case name, if known."""
    canonical = ALIASES.get(col, col)          # old name -> current BTS name
    if canonical in COLUMN_MAP:                  # current BTS name -> snake_case
        return COLUMN_MAP[canonical]
    # Case-insensitive fallback for lowercase variants (e.g. "tail_number").
    for key in COLUMN_MAP:
        if key.lower() == canonical.lower():
            return COLUMN_MAP[key]
    for key in ALIASES:
        if key.lower() == canonical.lower():
            return COLUMN_MAP[ALIASES[key]]
    return col                                   # unknown -> keep as-is (preserved)


def standardize_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Rename raw BTS columns to standardized snake_case names.

    Returns the renamed dataframe and the {original: standardized} mapping
    actually applied (for traceability). Unknown columns are kept untouched.
    """
    mapping = {}
    new_cols = []
    for col in df.columns:
        std = _resolve_name(col)
        mapping[col] = std
        new_cols.append(std)
    out = df.copy()
    out.columns = new_cols
    return out, mapping


# ---------------------------------------------------------------------------
# Step 2: coerce types
# ---------------------------------------------------------------------------
def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Convert string columns to their proper numeric / datetime types.

    Done explicitly (rather than at read time) so every conversion and the
    resulting missing values are auditable. Missing/blank -> NaN.
    """
    out = df.copy()

    # flight_date: BTS stores yyyymmdd. Normalize hyphenated forms first.
    if "flight_date" in out.columns:
        raw = out["flight_date"].astype(str).str.replace("-", "", regex=False)
        out["flight_date"] = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")

    for col in NUMERIC_FIELDS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    for col in INDICATOR_FIELDS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")

    # Free-text identifiers: normalize blank/whitespace to NaN, strip spaces.
    for col in ["tail_number", "reporting_airline", "iata_code_reporting_airline",
               "origin", "dest", "cancellation_code", "origin_state",
               "dest_state", "origin_city_name", "dest_city_name"]:
        if col in out.columns:
            s = out[col].astype("string").str.strip()
            out[col] = s.where((s != "") & (s.notna()) & (s.str.lower() != "nan"),
                               pd.NA)
    return out


# ---------------------------------------------------------------------------
# Step 3: reconstruct consistent LOCAL timestamps
# ---------------------------------------------------------------------------
def build_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """Add reconstructed local datetime columns.

    Adds:
      crs_dep_dt, dep_dt, crs_arr_dt, arr_dt   (timezone-naive LOCAL datetimes)
      arr_date_adjusted   (bool, always False here; kept for downstream compat)
      timestamp_inconsistent (bool)
    """
    out = df.copy()

    # minutes-since-midnight for the local hhmm columns
    out["_crs_dep_min"] = out["crs_dep_time"].map(parse_hhmm) if "crs_dep_time" in out else np.nan
    out["_dep_min"] = out["dep_time"].map(parse_hhmm) if "dep_time" in out else np.nan
    out["_crs_arr_min"] = out["crs_arr_time"].map(parse_hhmm) if "crs_arr_time" in out else np.nan
    out["_arr_min"] = out["arr_time"].map(parse_hhmm) if "arr_time" in out else np.nan

    fd = out["flight_date"] if "flight_date" in out else pd.NaT

    # Scheduled departure = flight_date + local hhmm (handles 2400 -> next day).
    out["crs_dep_dt"] = fd + pd.to_timedelta(out["_crs_dep_min"], unit="m")

    # Actual departure = scheduled departure + signed dep_delay (minutes).
    # Using the pre-computed delay is robust to midnight crossings.
    if "dep_delay" in out:
        out["dep_dt"] = out["crs_dep_dt"] + pd.to_timedelta(out["dep_delay"], unit="m")
    else:
        out["dep_dt"] = pd.NaT

    # Scheduled arrival = scheduled departure + scheduled elapsed time.
    if "crs_elapsed_time" in out:
        out["crs_arr_dt"] = out["crs_dep_dt"] + pd.to_timedelta(out["crs_elapsed_time"], unit="m")
    else:
        out["crs_arr_dt"] = pd.NaT

    # Actual arrival = actual departure + actual elapsed time (minutes).
    # For diverted flights actual_elapsed_time is NULL -> arr_dt becomes NaT,
    # which is correct (the flight did not arrive at its scheduled destination).
    if "actual_elapsed_time" in out:
        out["arr_dt"] = out["dep_dt"] + pd.to_timedelta(out["actual_elapsed_time"], unit="m")
    else:
        out["arr_dt"] = pd.NaT

    # Cross-check: actual arrival should also equal scheduled arrival + arr_delay.
    if "arr_delay" in out and "crs_arr_dt" in out:
        arr_alt = out["crs_arr_dt"] + pd.to_timedelta(out["arr_delay"], unit="m")
    else:
        arr_alt = pd.NaT

    diff = (out["arr_dt"] - arr_alt).dt.total_seconds().abs() / 60.0
    out["timestamp_inconsistent"] = (
        out["arr_dt"].notna() & arr_alt.notna() & (diff > 1.0)
    )

    out["arr_date_adjusted"] = False  # placeholder for the rotation module's UTC step
    out = out.drop(columns=[c for c in ["_crs_dep_min", "_dep_min",
                                        "_crs_arr_min", "_arr_min"] if c in out])
    return out


# ---------------------------------------------------------------------------
# Step 4: validate and flag records
# ---------------------------------------------------------------------------
def validate_records(df: pd.DataFrame) -> pd.DataFrame:
    """Add boolean quality flags and an ``exclusion_reason`` string per record."""
    out = df.copy()

    cancelled = (out["cancelled"] == 1) if "cancelled" in out else pd.Series(False, index=out.index)
    diverted = (out["diverted"] == 1) if "diverted" in out else pd.Series(False, index=out.index)

    out["is_cancelled"] = cancelled.fillna(False)
    out["is_diverted"] = diverted.fillna(False)

    # Tail number presence + plausibility (alphanumeric, 1-6 chars).
    tail = out["tail_number"].astype("string") if "tail_number" in out else pd.Series([pd.NA] * len(out))
    tail_present = tail.notna()
    tail_alnum = tail.str.match(r"^[A-Za-z0-9]{%d,%d}$" % (MIN_TAIL_LEN, MAX_TAIL_LEN)).fillna(False)
    out["has_tail_number"] = tail_present.fillna(False)
    out["valid_tail_number"] = (tail_present & tail_alnum).fillna(False)

    # Time validity (a scheduled departure time must parse; actual times may be
    # missing only for cancelled flights).
    out["valid_crs_dep_time"] = out["crs_dep_dt"].notna()
    out["valid_dep_time"] = out["dep_dt"].notna()
    out["valid_arr_time"] = out["arr_dt"].notna()
    out["invalid_timestamp"] = ~out["valid_crs_dep_time"]

    # Implausible numeric values.
    out["implausible_record"] = pd.Series(False, index=out.index)
    if "distance" in out:
        d = out["distance"]
        out["implausible_record"] = out["implausible_record"] | (d.notna() & (d <= 0)) | (d > MAX_DISTANCE_MI)
    if "actual_elapsed_time" in out:
        e = out["actual_elapsed_time"]
        out["implausible_record"] = out["implausible_record"] | (e.notna() & (e <= 0)) | (e > MAX_ELAPSED_MIN)
    if "crs_elapsed_time" in out:
        e = out["crs_elapsed_time"]
        out["implausible_record"] = out["implausible_record"] | (e.notna() & (e <= 0)) | (e > MAX_ELAPSED_MIN)
    if "dep_delay" in out:
        out["implausible_record"] = out["implausible_record"] | (out["dep_delay"].abs() > MAX_DELAY_MIN)
    if "arr_delay" in out:
        out["implausible_record"] = out["implausible_record"] | (out["arr_delay"].abs() > MAX_DELAY_MIN)

    # rotation_eligible: usable by the aircraft-rotation module.
    out["rotation_eligible"] = (
        out["valid_tail_number"]
        & out["valid_crs_dep_time"]
        & out["valid_dep_time"]
        & out["valid_arr_time"]
        & ~out["is_cancelled"]
        & ~out["is_diverted"]
        & ~out["timestamp_inconsistent"].fillna(False)
        & ~out["implausible_record"].fillna(False)
    ).fillna(False)

    # Human-readable exclusion reason (first failing gate wins).
    def _reason(row) -> str:
        if row.get("is_cancelled", False):
            return "cancelled"
        if row.get("is_diverted", False):
            return "diverted"
        if not row.get("valid_tail_number", False):
            return "missing_or_invalid_tail_number"
        if not row.get("valid_crs_dep_time", False):
            return "invalid_scheduled_departure_time"
        if not row.get("valid_dep_time", False):
            return "missing_actual_departure"
        if not row.get("valid_arr_time", False):
            return "missing_actual_arrival"
        if bool(row.get("timestamp_inconsistent", False)):
            return "inconsistent_timestamps"
        if bool(row.get("implausible_record", False)):
            return "implausible_value"
        return ""
    out["exclusion_reason"] = out.apply(_reason, axis=1)
    return out


# ---------------------------------------------------------------------------
# Step 5: duplicate detection
# ---------------------------------------------------------------------------
def detect_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flag and remove exact duplicate rows.

    Returns ``(kept, removed)``:
      * ``kept`` - dataframe with exact duplicates removed, with boolean
        columns ``duplicate_exact`` (always False here) and ``duplicate_identity``.
      * ``removed`` - the exact-duplicate rows that were dropped, for the audit log.

    Exact duplicates are a data error and are removed. Identity duplicates (same
    flight day/carrier/number/route/time) are flagged but kept, because two
    physically distinct flights can rarely share those keys and removing them
    could lose legitimate records.
    """
    out = df.copy()
    if out.empty:
        out["duplicate_exact"] = pd.Series(dtype=bool)
        out["duplicate_identity"] = pd.Series(dtype=bool)
        return out, out.iloc[0:0]

    # Exact duplicates: identical across every column. Keep first occurrence.
    out["duplicate_exact"] = out.duplicated(keep="first")
    keep = ~out["duplicate_exact"]
    kept = out[keep].copy()
    removed = out[~keep].copy()

    # Identity duplicates among retained rows (operational keys).
    identity_keys = [c for c in
                     ["flight_date", "reporting_airline", "flight_number",
                      "origin", "dest", "crs_dep_time"] if c in kept.columns]
    kept["duplicate_identity"] = (
        kept.duplicated(subset=identity_keys, keep=False) if identity_keys
        else False
    )
    kept["duplicate_exact"] = False
    return kept, removed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run the full cleaning pipeline on a raw BTS dataframe.

    Returns ``(cleaned, removed_duplicates, summary)``:
      * cleaned - flight-level dataframe (exact duplicates removed; all other
        records kept and flagged).
      * removed_duplicates - the exact-duplicate rows dropped (audit log).
      * summary - counts dictionary.
    """
    raw_rows = len(df)
    std, mapping = standardize_columns(df)

    # Required-field sanity check: warn but proceed if some are missing.
    missing_required = [f for f in REQUIRED_FIELDS if f not in std.columns]
    coerced = coerce_types(std)
    with_ts = build_timestamps(coerced)
    flagged = validate_records(with_ts)
    kept, removed = detect_duplicates(flagged)
    cleaned = kept

    n_exact_dups = raw_rows - len(cleaned)
    summary = {
        "raw_rows": raw_rows,
        "rows_after_dedup": len(cleaned),
        "exact_duplicates_removed": n_exact_dups,
        "columns_original_to_standardized": mapping,
        "missing_required_fields": missing_required,
        "n_rotation_eligible": int(cleaned.get("rotation_eligible", pd.Series(dtype=bool)).sum()),
        "n_cancelled": int(cleaned["is_cancelled"].sum()) if "is_cancelled" in cleaned else 0,
        "n_diverted": int(cleaned["is_diverted"].sum()) if "is_diverted" in cleaned else 0,
        "n_missing_tail": int((~cleaned["valid_tail_number"]).sum()) if "valid_tail_number" in cleaned else 0,
        "n_invalid_timestamp": int(cleaned["invalid_timestamp"].sum()) if "invalid_timestamp" in cleaned else 0,
        "n_implausible": int(cleaned["implausible_record"].sum()) if "implausible_record" in cleaned else 0,
        "n_timestamp_inconsistent": int(cleaned["timestamp_inconsistent"].sum()) if "timestamp_inconsistent" in cleaned else 0,
    }
    cleaned.attrs["cleaning_summary"] = summary
    cleaned.attrs["column_mapping"] = mapping
    return cleaned, removed, summary


# ---------------------------------------------------------------------------
# Data-quality report
# ---------------------------------------------------------------------------
def generate_quality_report(
    raw_df: pd.DataFrame,
    cleaned: pd.DataFrame,
    summary: dict[str, Any],
    out_md: str,
    out_json: str,
    source: str = "",
) -> dict[str, Any]:
    """Write a reproducible markdown + JSON data-quality report to results/."""
    os.makedirs(os.path.dirname(os.path.abspath(out_md)), exist_ok=True)

    report: dict[str, Any] = dict(summary)
    report["source"] = source
    report["raw_shape"] = list(raw_df.shape)
    report["cleaned_shape"] = list(cleaned.shape)
    report["cleaned_columns"] = list(cleaned.columns)

    # Missingness for important fields, before and after cleaning.
    important = [f for f in
                 ["tail_number", "crs_dep_time", "dep_time", "dep_delay",
                  "crs_arr_time", "arr_time", "arr_delay", "cancelled",
                  "diverted", "crs_elapsed_time", "actual_elapsed_time",
                  "distance"] if f in cleaned.columns]
    miss_after = {c: int(cleaned[c].isna().sum()) for c in important}
    raw_std, _ = standardize_columns(raw_df)
    miss_before = {c: int(raw_std[c].isna().sum()) for c in important
                    if c in raw_std.columns}
    report["missing_before"] = miss_before
    report["missing_after"] = miss_after

    # Exclusion reasons tally (only among retained records; dups already removed).
    if "exclusion_reason" in cleaned.columns:
        reasons = cleaned.loc[cleaned["exclusion_reason"] != "", "exclusion_reason"]
        report["exclusion_reasons"] = reasons.value_counts().to_dict()

    report["timestamp_validation"] = {
        "valid_crs_dep_time": int(cleaned["valid_crs_dep_time"].sum()) if "valid_crs_dep_time" in cleaned else 0,
        "valid_dep_time": int(cleaned["valid_dep_time"].sum()) if "valid_dep_time" in cleaned else 0,
        "valid_arr_time": int(cleaned["valid_arr_time"].sum()) if "valid_arr_time" in cleaned else 0,
        "inconsistent": int(cleaned["timestamp_inconsistent"].sum()) if "timestamp_inconsistent" in cleaned else 0,
    }

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    # Human-readable markdown
    lines = []
    lines.append("# Data-Quality Report\n")
    lines.append(f"**Source file:** `{source or 'n/a'}`\n")
    lines.append("This report is generated automatically by "
                 "`src/preprocessing.py` and is reproducible from the raw input.\n")
    lines.append("## Shape\n")
    lines.append(f"- Raw rows: {report['raw_shape'][0]:,}  |  columns: {report['raw_shape'][1]}")
    lines.append(f"- Cleaned rows: {report['cleaned_shape'][0]:,}  |  columns: {report['cleaned_shape'][1]}")
    lines.append(f"- Exact duplicate rows removed: {summary['exact_duplicates_removed']:,}")
    lines.append(f"- Rotation-eligible rows: {summary['n_rotation_eligible']:,}\n")

    lines.append("## Records flagged (retained, not deleted)\n")
    lines.append(f"- Cancelled: {summary['n_cancelled']:,}")
    lines.append(f"- Diverted: {summary['n_diverted']:,}")
    lines.append(f"- Missing/invalid tail number: {summary['n_missing_tail']:,}")
    lines.append(f"- Invalid scheduled departure time: {summary['n_invalid_timestamp']:,}")
    lines.append(f"- Implausible values: {summary['n_implausible']:,}")
    lines.append(f"- Inconsistent timestamps: {summary['n_timestamp_inconsistent']:,}\n")

    lines.append("## Missing values for important fields\n")
    lines.append("| Field | Before | After |")
    lines.append("|---|---:|---:|")
    for c in important:
        lines.append(f"| {c} | {miss_before.get(c, 'n/a')} | {miss_after.get(c, 'n/a')} |")
    lines.append("")

    lines.append("## Timestamp validation\n")
    tv = report["timestamp_validation"]
    lines.append(f"- Valid scheduled departure time: {tv['valid_crs_dep_time']:,}")
    lines.append(f"- Valid actual departure time: {tv['valid_dep_time']:,}")
    lines.append(f"- Valid actual arrival time: {tv['valid_arr_time']:,}")
    lines.append(f"- Inconsistent arrival reconstruction: {tv['inconsistent']:,}\n")

    if report.get("exclusion_reasons"):
        lines.append("## Exclusion reasons (retained records)\n")
        lines.append("| Reason | Count |")
        lines.append("|---|---:|")
        for reason, n in report["exclusion_reasons"].items():
            lines.append(f"| {reason} | {n:,} |")
        lines.append("")

    if summary.get("missing_required_fields"):
        lines.append("## Missing required fields\n")
        lines.append("These standardized fields were not found in the raw file: "
                     f"{', '.join(summary['missing_required_fields'])}\n")

    lines.append("## Limitations\n")
    lines.append("- All datetimes are LOCAL airport time and timezone-naive. "
                 "Cross-airport comparison/sorting requires UTC conversion in the "
                 "aircraft-rotation module.")
    lines.append("- Scheduled airport flight volume is used as a congestion proxy "
                 "by other team members; this cleaned dataset does not include it.")
    lines.append("- Delay-cause fields (CarrierDelay, etc.) are present only from "
                 "June 2003 and may be blank for many carriers/months.")
    lines.append("- Flights crossing a daylight-saving boundary may have a 1-hour "
                 "offset between wall-clock and true elapsed time.\n")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clean BTS flight records and write a data-quality report."
    )
    parser.add_argument("path", nargs="?", help="Path to the raw BTS CSV file.")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--nrows", type=int, default=None,
                        help="Process only the first N rows (sample month, etc.).")
    parser.add_argument("--out-dir", default="data/processed")
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args(argv)

    from src.data_ingestion import find_raw_file, load_raw
    path = args.path or find_raw_file(args.raw_dir)
    if not path:
        print(f"No BTS CSV found in '{args.raw_dir}'. Place a downloaded month in "
              f"data/raw/ first.")
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(args.results_dir, exist_ok=True)

    print(f"Loading {path} ...")
    raw = load_raw(path, nrows=args.nrows)
    print(f"  raw rows: {len(raw):,}")

    cleaned, removed, summary = clean(raw)
    print(f"  cleaned rows: {len(cleaned):,}  "
          f"(removed {summary['exact_duplicates_removed']:,} exact duplicates)")
    print(f"  rotation-eligible: {summary['n_rotation_eligible']:,}")

    # Write outputs
    clean_path = os.path.join(args.out_dir, "flights_clean.csv")
    eligible_path = os.path.join(args.out_dir, "flights_rotation_eligible.csv")
    dup_path = os.path.join(args.out_dir, "duplicate_records.csv")
    map_path = os.path.join(args.out_dir, "column_mapping.json")
    report_md = os.path.join(args.results_dir, "data_quality_report.md")
    report_json = os.path.join(args.results_dir, "data_quality_summary.json")

    cleaned.to_csv(clean_path, index=False)
    if "rotation_eligible" in cleaned.columns:
        cleaned[cleaned["rotation_eligible"]].to_csv(eligible_path, index=False)
    removed.to_csv(dup_path, index=False)

    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(summary["columns_original_to_standardized"], f, indent=2)

    generate_quality_report(raw, cleaned, summary, report_md, report_json,
                            source=path)
    print(f"  cleaned dataset: {clean_path}")
    print(f"  rotation-eligible subset: {eligible_path}")
    print(f"  duplicate audit log: {dup_path}")
    print(f"  column mapping: {map_path}")
    print(f"  report: {report_md}")
    print(f"  report (json): {report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
