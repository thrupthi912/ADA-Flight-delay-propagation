"""Full 12-month BTS On-Time Performance pipeline.

Owned by Thrupthi G D. Processes all monthly raw CSV files found under
data/raw/, cleans each month sequentially, writes per-month and combined
output CSVs, and generates a data quality report.

Usage::

    python -m scripts.run_pipeline
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py --raw-dir data/raw --out-dir data/processed

Outputs::

    data/processed/monthly/<month>_clean.csv
    data/processed/monthly/<month>_rotation_eligible.csv
    data/processed/flights_clean.csv
    data/processed/flights_rotation_eligible.csv
    data/processed/flights_sensitivity_eligible.csv
    data/processed/duplicate_records.csv
    data/processed/column_mapping.json
    reports/data_quality_summary.json
    reports/data_quality_report.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

# Ensure src/ is importable when run as a script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_ingestion import find_monthly_files, load_raw
from src.airport_timezones import load_airport_tz
from src.preprocessing import clean


def _load_airport_tz(tz_csv: str) -> dict:
    """Load the airport timezone map; return empty dict on failure."""
    try:
        return load_airport_tz(tz_csv)
    except Exception as exc:
        print(f"  WARNING: could not load airport timezones from {tz_csv}: {exc}")
        return {}


def _process_month(
    month_name: str,
    csv_path: str,
    airport_tz: dict,
    out_monthly: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Load, clean, and return (cleaned, rotation_eligible, sensitivity_eligible,
    summary) for one month. Writes per-month CSVs to out_monthly/."""
    print(f"  loading {month_name} from {csv_path} ...")
    raw = load_raw(csv_path)
    source_rows = len(raw)
    print(f"    {source_rows:,} rows")

    cleaned, removed, summary = clean(raw, airport_tz=airport_tz)
    summary["month"] = month_name
    summary["source_file"] = csv_path
    summary["source_rows"] = source_rows
    summary["n_exact_duplicates"] = len(removed)
    summary["has_diverted_column"] = int((cleaned["diverted"].notna().any()
                                          if "diverted" in cleaned.columns
                                          else False))

    # Per-month stats
    n_unk_div = int(cleaned["diverted"].isna().sum()) if "diverted" in cleaned.columns else source_rows
    n_confirmed_div = int(cleaned["is_diverted"].fillna(False).sum()) if "is_diverted" in cleaned.columns else 0
    n_cancelled = int(cleaned["is_cancelled"].fillna(False).sum()) if "is_cancelled" in cleaned.columns else 0
    n_invalid_dates = int(cleaned["flight_date"].isna().sum()) if "flight_date" in cleaned.columns else 0
    n_utc_ts = int(cleaned["has_utc_timestamps"].fillna(False).sum()) if "has_utc_timestamps" in cleaned.columns else 0
    n_rot = int(cleaned["rotation_eligible"].fillna(False).sum()) if "rotation_eligible" in cleaned.columns else 0
    n_sens = int(cleaned["sensitivity_eligible"].fillna(False).sum()) if "sensitivity_eligible" in cleaned.columns else 0
    n_missing_tz = int((~cleaned["tz_origin_known"].fillna(False)).sum()) if "tz_origin_known" in cleaned.columns else 0

    summary.update({
        "source_rows": source_rows,
        "parsed_rows": len(cleaned),
        "invalid_dates": n_invalid_dates,
        "duplicate_rows": len(removed),
        "cancelled": n_cancelled,
        "confirmed_diverted": n_confirmed_div,
        "unknown_diversion": n_unk_div,
        "missing_tz": n_missing_tz,
        "valid_utc_timestamps": n_utc_ts,
        "strict_rotation_eligible": n_rot,
        "sensitivity_eligible": n_sens,
    })

    os.makedirs(out_monthly, exist_ok=True)
    safe_name = month_name.replace(" ", "_")
    clean_path = os.path.join(out_monthly, f"{safe_name}_clean.csv")
    rot_path = os.path.join(out_monthly, f"{safe_name}_rotation_eligible.csv")
    cleaned.to_csv(clean_path, index=False)
    rotation_df = cleaned[cleaned["rotation_eligible"].fillna(False)].copy() if "rotation_eligible" in cleaned.columns else cleaned.iloc[0:0]
    rotation_df.to_csv(rot_path, index=False)

    sensitivity_df = cleaned[cleaned["sensitivity_eligible"].fillna(False)].copy() if "sensitivity_eligible" in cleaned.columns else cleaned.iloc[0:0]

    print(f"    cleaned: {len(cleaned):,}  rotation: {n_rot:,}  sensitivity: {n_sens:,}  unknown_div: {n_unk_div:,}")
    return cleaned, rotation_df, sensitivity_df, summary


def run_pipeline(raw_dir: str, out_dir: str, tz_csv: str, reports_dir: str) -> None:
    start = time.time()
    monthly_files = find_monthly_files(raw_dir)
    if not monthly_files:
        print(f"ERROR: no T_ONTIME_REPORTING.csv files found under {raw_dir}")
        sys.exit(1)
    print(f"Found {len(monthly_files)} monthly files in {raw_dir}:")
    for name, path in monthly_files:
        print(f"  {name}: {path}")

    airport_tz = _load_airport_tz(tz_csv)
    print(f"Loaded {len(airport_tz):,} airport timezones from {tz_csv}")

    out_monthly = os.path.join(out_dir, "monthly")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(out_monthly, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    all_cleaned: list[pd.DataFrame] = []
    all_rotation: list[pd.DataFrame] = []
    all_sensitivity: list[pd.DataFrame] = []
    all_removed: list[pd.DataFrame] = []
    monthly_summaries: list[dict] = []
    column_mapping: dict[str, str] = {}

    for i, (month_name, csv_path) in enumerate(monthly_files, 1):
        print(f"\n[{i}/{len(monthly_files)}] Processing {month_name} ...")
        try:
            raw = load_raw(csv_path)
            cleaned, removed, summary = clean(raw, airport_tz=airport_tz)
            summary["month"] = month_name
            summary["source_file"] = csv_path
            summary["source_rows"] = len(raw)
            summary["n_exact_duplicates"] = len(removed)
            summary["has_diverted_column"] = bool(
                "diverted" in cleaned.columns and cleaned["diverted"].notna().any()
            )

            n_unk_div = int(cleaned["diverted"].isna().sum()) if "diverted" in cleaned.columns else len(cleaned)
            n_confirmed_div = int(cleaned["is_diverted"].fillna(False).sum()) if "is_diverted" in cleaned.columns else 0
            n_cancelled = int(cleaned["is_cancelled"].fillna(False).sum()) if "is_cancelled" in cleaned.columns else 0
            n_invalid_dates = int(cleaned["flight_date"].isna().sum()) if "flight_date" in cleaned.columns else 0
            n_utc_ts = int(cleaned["has_utc_timestamps"].fillna(False).sum()) if "has_utc_timestamps" in cleaned.columns else 0
            n_rot = int(cleaned["rotation_eligible"].fillna(False).sum()) if "rotation_eligible" in cleaned.columns else 0
            n_sens = int(cleaned["sensitivity_eligible"].fillna(False).sum()) if "sensitivity_eligible" in cleaned.columns else 0
            n_missing_tz = int((~cleaned["tz_origin_known"].fillna(False)).sum()) if "tz_origin_known" in cleaned.columns else 0

            summary.update({
                "source_rows": len(raw),
                "parsed_rows": len(cleaned),
                "invalid_dates": n_invalid_dates,
                "duplicate_rows": len(removed),
                "cancelled": n_cancelled,
                "confirmed_diverted": n_confirmed_div,
                "unknown_diversion": n_unk_div,
                "missing_tz": n_missing_tz,
                "valid_utc_timestamps": n_utc_ts,
                "strict_rotation_eligible": n_rot,
                "sensitivity_eligible": n_sens,
            })

            # Remove non-serialisable attrs before storing
            summary.pop("columns_original_to_standardized", None)

            safe_name = month_name.replace(" ", "_")
            clean_path = os.path.join(out_monthly, f"{safe_name}_clean.csv")
            rot_path = os.path.join(out_monthly, f"{safe_name}_rotation_eligible.csv")
            cleaned.to_csv(clean_path, index=False)
            rotation_df = (
                cleaned[cleaned["rotation_eligible"].fillna(False)].copy()
                if "rotation_eligible" in cleaned.columns else cleaned.iloc[0:0]
            )
            rotation_df.to_csv(rot_path, index=False)
            sensitivity_df = (
                cleaned[cleaned["sensitivity_eligible"].fillna(False)].copy()
                if "sensitivity_eligible" in cleaned.columns else cleaned.iloc[0:0]
            )

            all_cleaned.append(cleaned)
            all_rotation.append(rotation_df)
            all_sensitivity.append(sensitivity_df)
            all_removed.append(removed)
            monthly_summaries.append(summary)
            if column_mapping == {}:
                column_mapping = dict(summary.get("columns_original_to_standardized", {}))

            # Release memory for this month before loading the next.
            del raw, cleaned, removed, rotation_df, sensitivity_df
            import gc; gc.collect()

            print(f"    source: {summary['source_rows']:,}  parsed: {summary['parsed_rows']:,}  "
                  f"rotation: {n_rot:,}  sensitivity: {n_sens:,}  unknown_div: {n_unk_div:,}")
        except Exception as exc:
            print(f"  ERROR processing {month_name}: {exc}")
            import traceback; traceback.print_exc()
            monthly_summaries.append({
                "month": month_name,
                "source_file": csv_path,
                "error": str(exc),
            })

    # Combine all months
    print("\nCombining all months ...")
    if all_cleaned:
        combined_clean = pd.concat(all_cleaned, ignore_index=True)
        combined_rotation = pd.concat(all_rotation, ignore_index=True)
        combined_sensitivity = pd.concat(all_sensitivity, ignore_index=True)
        combined_removed = pd.concat(all_removed, ignore_index=True)
    else:
        print("ERROR: no months processed successfully.")
        sys.exit(1)

    clean_path = os.path.join(out_dir, "flights_clean.csv")
    rot_path = os.path.join(out_dir, "flights_rotation_eligible.csv")
    sens_path = os.path.join(out_dir, "flights_sensitivity_eligible.csv")
    dup_path = os.path.join(out_dir, "duplicate_records.csv")
    map_path = os.path.join(out_dir, "column_mapping.json")

    print(f"Writing {clean_path} ({len(combined_clean):,} rows) ...")
    combined_clean.to_csv(clean_path, index=False)
    print(f"Writing {rot_path} ({len(combined_rotation):,} rows) ...")
    combined_rotation.to_csv(rot_path, index=False)
    print(f"Writing {sens_path} ({len(combined_sensitivity):,} rows) ...")
    combined_sensitivity.to_csv(sens_path, index=False)
    print(f"Writing {dup_path} ({len(combined_removed):,} rows) ...")
    combined_removed.to_csv(dup_path, index=False)

    # Column mapping from the first successfully processed month
    if column_mapping:
        with open(map_path, "w") as f:
            json.dump(column_mapping, f, indent=2)
        print(f"Writing {map_path}")

    # Reconciliation check
    total_source = sum(s.get("source_rows", 0) for s in monthly_summaries)
    total_parsed = sum(s.get("parsed_rows", 0) for s in monthly_summaries)
    total_dup = sum(s.get("duplicate_rows", 0) for s in monthly_summaries)
    total_rot = sum(s.get("strict_rotation_eligible", 0) for s in monthly_summaries)
    total_sens = sum(s.get("sensitivity_eligible", 0) for s in monthly_summaries)
    print(f"\nReconciliation:")
    print(f"  Total source rows:    {total_source:,}")
    print(f"  Total parsed/cleaned: {total_parsed:,}")
    print(f"  Total duplicates:     {total_dup:,}")
    print(f"  Combined clean rows:  {len(combined_clean):,}")
    print(f"  Strict rotation:      {total_rot:,}")
    print(f"  Sensitivity cohort:   {total_sens:,}")
    if total_parsed != len(combined_clean):
        print(f"  WARNING: parsed total ({total_parsed}) != combined rows ({len(combined_clean)}) -- likely due to months with errors")

    # Reports
    summary_report = {
        "study_year": 2024,
        "n_months": len(monthly_files),
        "total_source_rows": total_source,
        "total_parsed_rows": total_parsed,
        "total_exact_duplicates": total_dup,
        "total_cancelled": sum(s.get("cancelled", 0) for s in monthly_summaries),
        "total_confirmed_diverted": sum(s.get("confirmed_diverted", 0) for s in monthly_summaries),
        "total_unknown_diversion": sum(s.get("unknown_diversion", 0) for s in monthly_summaries),
        "total_strict_rotation_eligible": total_rot,
        "total_sensitivity_eligible": total_sens,
        "months": monthly_summaries,
    }
    summary_path = os.path.join(reports_dir, "data_quality_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary_report, f, indent=2, default=str)
    print(f"\nWrote {summary_path}")

    _write_markdown_report(summary_report, monthly_summaries, reports_dir)

    elapsed = time.time() - start
    print(f"\nPipeline complete in {elapsed:.1f}s.")
    print(f"  Combined clean:         {len(combined_clean):,} rows -> {clean_path}")
    print(f"  Rotation eligible:      {len(combined_rotation):,} rows -> {rot_path}")
    print(f"  Sensitivity eligible:   {len(combined_sensitivity):,} rows -> {sens_path}")


def _write_markdown_report(summary: dict, monthly_summaries: list[dict], reports_dir: str) -> None:
    lines = [
        "# Data Quality Report",
        "",
        "## Dataset Overview",
        "",
        f"- **Study period**: 2024 (12 months)",
        f"- **Source**: U.S. Bureau of Transportation Statistics (BTS) On-Time Performance",
        f"- **Total source rows**: {summary['total_source_rows']:,}",
        f"- **Total cleaned rows**: {summary['total_parsed_rows']:,}",
        f"- **Total exact duplicates removed**: {summary['total_exact_duplicates']:,}",
        f"- **Total cancelled flights**: {summary['total_cancelled']:,}",
        f"- **Total confirmed diverted**: {summary['total_confirmed_diverted']:,}",
        f"- **Total unknown diversion status**: {summary['total_unknown_diversion']:,}",
        f"- **Strict rotation-eligible**: {summary['total_strict_rotation_eligible']:,}",
        f"- **Sensitivity cohort (unknown diversion)**: {summary['total_sensitivity_eligible']:,}",
        "",
        "## Schema Variations by Month",
        "",
        "| Month | DIVERTED | WHEELS_OFF/ON | DISTANCE |",
        "|-------|----------|---------------|----------|",
    ]
    diverted_months = ["January", "June", "July", "August"]
    wheels_months = ["January"]
    no_distance_months = ["January"]
    for ms in monthly_summaries:
        m = ms.get("month", "?")
        div = "Yes" if m in diverted_months else "No"
        wh = "Yes" if m in wheels_months else "No"
        dist = "No" if m in no_distance_months else "Yes"
        lines.append(f"| {m} | {div} | {wh} | {dist} |")

    lines += [
        "",
        "## Monthly Row Counts",
        "",
        "| Month | Source Rows | Cleaned Rows | Duplicates | Cancelled | Confirmed Diverted | Unknown Diversion | Strict Rotation | Sensitivity |",
        "|-------|-------------|--------------|------------|-----------|-------------------|-------------------|-----------------|-------------||",
    ]
    for ms in monthly_summaries:
        if "error" in ms:
            lines.append(f"| {ms.get('month','?')} | ERROR | ERROR | - | - | - | - | - | - |")
        else:
            lines.append(
                f"| {ms.get('month','?')} "
                f"| {ms.get('source_rows',0):,} "
                f"| {ms.get('parsed_rows',0):,} "
                f"| {ms.get('duplicate_rows',0):,} "
                f"| {ms.get('cancelled',0):,} "
                f"| {ms.get('confirmed_diverted',0):,} "
                f"| {ms.get('unknown_diversion',0):,} "
                f"| {ms.get('strict_rotation_eligible',0):,} "
                f"| {ms.get('sensitivity_eligible',0):,} |"
            )

    lines += [
        "",
        "## DIVERTED Column Availability",
        "",
        "The BTS On-Time Performance download for 2024 does not include the DIVERTED",
        "column in all months. Based on inspection of the raw files:",
        "",
        "- **DIVERTED present**: January, June, July, August",
        "- **DIVERTED absent**: February, March, April, May, September, October, November, December",
        "",
        "For months without DIVERTED, all flights are assigned unknown diversion status.",
        "These flights appear in the sensitivity cohort but NOT in the strict rotation-eligible cohort.",
        "Downstream analysis teams must decide which cohort to use.",
        "",
        "## Timestamp Reconstruction",
        "",
        "BTS stores times as LOCAL airport clock times (hhmm format).",
        "This pipeline converts them to UTC instants using IANA timezone data from OurAirports.",
        "",
        "- Departure times are interpreted in the origin airport's timezone",
        "- Arrival instants (UTC) are derived from UTC departure + actual elapsed time",
        "- Destination-local arrival clock time (raw arr_time) is preserved separately",
        "- Overnight flights are handled correctly via UTC arithmetic",
        "- DST ambiguous/nonexistent timestamps are detected and flagged",
        "",
        "## Known Limitations",
        "",
        "1. **DIVERTED absent from 8 months**: Cannot distinguish diverted vs non-diverted flights",
        "   for Feb, Mar, Apr, May, Sep, Oct, Nov, Dec. Use sensitivity cohort with caution.",
        "2. **All timestamps are local clock times**: UTC conversion requires the airport timezone map.",
        "   Airports not in the map produce local-only timestamps (flagged `tz_origin_known=False`).",
        "3. **DISTANCE absent from January**: Distance-based analysis requires special handling for January.",
        "4. **WHEELS_OFF/ON only in January**: Ground time analysis is limited to January.",
        "5. **This is an observational study**: No causal claims about delay propagation are supported by the data alone.",
        "",
        f"*Report generated from pipeline run on {pd.Timestamp.now().strftime('%Y-%m-%d')}.*",
    ]

    md_path = os.path.join(reports_dir, "data_quality_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {md_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the full 12-month BTS On-Time Performance cleaning pipeline."
    )
    parser.add_argument(
        "--raw-dir",
        default=r"c:\Users\thrup\OneDrive\Desktop\Sem 5\ADA\ADA-Flight-delay-propagation\data\raw",
        help="Root directory containing monthly subdirectories with T_ONTIME_REPORTING.csv files.",
    )
    parser.add_argument(
        "--out-dir",
        default=r"c:\Users\thrup\OneDrive\Desktop\Sem 5\ADA\ADA-Flight-delay-propagation\data\processed",
        help="Root output directory for processed CSVs.",
    )
    parser.add_argument(
        "--tz-csv",
        default=r"c:\Users\thrup\OneDrive\Desktop\Sem 5\ADA\ADA-Flight-delay-propagation\data\airport_timezones.csv",
        help="Path to the airport_timezones.csv mapping file.",
    )
    parser.add_argument(
        "--reports-dir",
        default=r"c:\Users\thrup\OneDrive\Desktop\Sem 5\ADA\ADA-Flight-delay-propagation\reports",
        help="Directory for Markdown and JSON reports.",
    )
    args = parser.parse_args(argv)
    run_pipeline(
        raw_dir=args.raw_dir,
        out_dir=args.out_dir,
        tz_csv=args.tz_csv,
        reports_dir=args.reports_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
