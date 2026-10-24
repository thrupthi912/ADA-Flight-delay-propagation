"""Tests for the cleaning pipeline (src.preprocessing)."""
import os

import numpy as np
import pandas as pd
import pytest

from src.preprocessing import (
    build_timestamps, clean, coerce_types, detect_duplicates,
    generate_quality_report, parse_hhmm, standardize_columns, validate_records,
)
from src.data_ingestion import load_raw
from conftest import SAMPLE_FLIGHTS, SAMPLE_OLD_NAMES


def _clean_df():
    """Run the full pipeline on the fixture and return the cleaned frame."""
    raw = load_raw(SAMPLE_FLIGHTS)
    cleaned, removed, summary = clean(raw)
    return cleaned, removed, summary


# ---------------------------------------------------------------------------
# parse_hhmm
# ---------------------------------------------------------------------------
def test_parse_hhmm_basic():
    assert parse_hhmm("735") == 455
    assert parse_hhmm("1430") == 870
    assert parse_hhmm(1430) == 870
    assert parse_hhmm("0000") == 0
    assert parse_hhmm(0) == 0


def test_parse_hhmm_midnight_2400():
    # 2400 -> 1440 minutes (start of next day)
    assert parse_hhmm("2400") == 1440


def test_parse_hhmm_invalid_and_missing():
    assert np.isnan(parse_hhmm(""))
    assert np.isnan(parse_hhmm("nan"))
    assert np.isnan(parse_hhmm(None))
    assert np.isnan(parse_hhmm("2500"))     # hh=25
    assert np.isnan(parse_hhmm("1060"))     # mm=60
    assert np.isnan(parse_hhmm("ABC"))


def test_parse_hhmm_handles_float_strings():
    # BTS downloads sometimes store times like "735.0"
    assert parse_hhmm("735.0") == 455


# ---------------------------------------------------------------------------
# standardize_columns
# ---------------------------------------------------------------------------
def test_standardize_columns_current_names():
    raw = load_raw(SAMPLE_FLIGHTS)
    std, mapping = standardize_columns(raw)
    assert "tail_number" in std.columns
    assert "crs_dep_time" in std.columns
    assert "flight_date" in std.columns
    assert mapping["Tail_Number"] == "tail_number"
    assert mapping["CRSDepTime"] == "crs_dep_time"


def test_standardize_columns_old_aliases():
    raw = load_raw(SAMPLE_OLD_NAMES)
    std, mapping = standardize_columns(raw)
    # Old BTS names are resolved to the same standardized names.
    assert mapping["UniqueCarrier"] == "reporting_airline"
    assert mapping["TailNum"] == "tail_number"
    assert mapping["FlightNum"] == "flight_number"
    assert "tail_number" in std.columns


# ---------------------------------------------------------------------------
# coerce_types
# ---------------------------------------------------------------------------
def test_coerce_types_converts_numeric_and_date():
    raw = load_raw(SAMPLE_FLIGHTS)
    std, _ = standardize_columns(raw)
    out = coerce_types(std)
    assert pd.api.types.is_datetime64_any_dtype(out["flight_date"])
    assert pd.api.types.is_numeric_dtype(out["dep_delay"])
    assert pd.api.types.is_numeric_dtype(out["distance"])
    # Cancelled -> nullable Int64, not object.
    assert str(out["cancelled"].dtype) == "Int64"


def test_coerce_types_blanks_become_nan():
    raw = load_raw(SAMPLE_FLIGHTS)
    std, _ = standardize_columns(raw)
    out = coerce_types(std)
    # The cancelled flight (row 2) has blank dep_delay -> NaN.
    cancelled_row = out[out["cancelled"] == 1].iloc[0]
    assert pd.isna(cancelled_row["dep_delay"])


# ---------------------------------------------------------------------------
# build_timestamps (the critical correctness tests)
# ---------------------------------------------------------------------------
def _with_ts():
    raw = load_raw(SAMPLE_FLIGHTS)
    return build_timestamps(coerce_types(standardize_columns(raw)[0]))


def test_timestamps_midnight_crossing():
    ts = _with_ts()
    r1 = ts.iloc[0]  # dep 22:30, arr next day 00:40
    assert r1["crs_dep_dt"] == pd.Timestamp("2024-01-05 22:30:00")
    assert r1["dep_dt"] == pd.Timestamp("2024-01-05 22:35:00")
    # Arrival correctly lands on the next calendar day.
    assert r1["crs_arr_dt"] == pd.Timestamp("2024-01-06 00:30:00")
    assert r1["arr_dt"] == pd.Timestamp("2024-01-06 00:40:00")


def test_timestamps_cancelled_flight_has_no_actual_times():
    ts = _with_ts()
    cancelled = ts[ts["cancelled"] == 1].iloc[0]
    assert pd.isna(cancelled["dep_dt"])
    assert pd.isna(cancelled["arr_dt"])
    # Scheduled times are still reconstructed.
    assert pd.notna(cancelled["crs_dep_dt"])
    assert pd.notna(cancelled["crs_arr_dt"])


def test_timestamps_diverted_flight_has_no_arrival():
    ts = _with_ts()
    diverted = ts[ts["diverted"] == 1].iloc[0]
    assert pd.notna(diverted["dep_dt"])          # it did depart
    assert pd.isna(diverted["arr_dt"])           # but did not arrive at dest


def test_timestamp_inconsistency_detected():
    ts = _with_ts()
    inconsistent = ts[ts["tail_number"] == "N222YY"].iloc[0]
    assert bool(inconsistent["timestamp_inconsistent"]) is True
    # The normal flight is NOT flagged.
    assert bool(ts.iloc[0]["timestamp_inconsistent"]) is False


# ---------------------------------------------------------------------------
# validate_records
# ---------------------------------------------------------------------------
def _validated():
    raw = load_raw(SAMPLE_FLIGHTS)
    return validate_records(build_timestamps(coerce_types(standardize_columns(raw)[0])))


def test_validate_flags_cancelled_and_diverted():
    v = _validated()
    assert int(v["is_cancelled"].sum()) == 1
    assert int(v["is_diverted"].sum()) == 1


def test_validate_flags_missing_tail():
    v = _validated()
    missing_tail = v[~v["valid_tail_number"]]
    # One row has a blank tail number.
    assert len(missing_tail) == 1
    assert pd.isna(missing_tail.iloc[0]["tail_number"])


def test_validate_flags_invalid_scheduled_time():
    v = _validated()
    invalid = v[v["tail_number"] == "N000X"]
    assert bool(invalid.iloc[0]["valid_crs_dep_time"]) is False
    assert bool(invalid.iloc[0]["invalid_timestamp"]) is True


def test_validate_flags_implausible_distance():
    v = _validated()
    bad = v[v["tail_number"] == "N111ZZ"]
    assert bool(bad.iloc[0]["implausible_record"]) is True


def test_rotation_eligible_only_for_clean_normal_flight():
    v = _validated()
    eligible = v[v["rotation_eligible"]]
    # Before dedup there are two copies of the normal flight -> both eligible.
    assert len(eligible) == 2
    assert set(eligible["tail_number"]) == {"N123AB"}


def test_exclusion_reasons_assigned():
    v = _validated()
    reasons = set(v.loc[v["exclusion_reason"] != "", "exclusion_reason"])
    assert "cancelled" in reasons
    assert "diverted" in reasons
    assert "missing_or_invalid_tail_number" in reasons
    assert "invalid_scheduled_departure_time" in reasons
    assert "inconsistent_timestamps" in reasons
    assert "implausible_value" in reasons


# ---------------------------------------------------------------------------
# detect_duplicates
# ---------------------------------------------------------------------------
def test_detect_duplicates_removes_exact_copy():
    raw = load_raw(SAMPLE_FLIGHTS)
    flagged = validate_records(build_timestamps(coerce_types(standardize_columns(raw)[0])))
    kept, removed = detect_duplicates(flagged)
    assert len(removed) == 1
    assert len(kept) == len(flagged) - 1


# ---------------------------------------------------------------------------
# clean (end-to-end)
# ---------------------------------------------------------------------------
def test_clean_removes_one_duplicate():
    cleaned, removed, summary = _clean_df()
    assert summary["raw_rows"] == 8
    assert summary["exact_duplicates_removed"] == 1
    assert summary["rows_after_dedup"] == 7
    assert len(cleaned) == 7
    assert len(removed) == 1


def test_clean_rotation_eligible_count():
    cleaned, removed, summary = _clean_df()
    # After dedup, only the single normal flight is rotation-eligible.
    assert summary["n_rotation_eligible"] == 1
    eligible = cleaned[cleaned["rotation_eligible"]]
    assert len(eligible) == 1


def test_clean_summary_counts():
    cleaned, removed, summary = _clean_df()
    assert summary["n_cancelled"] == 1
    assert summary["n_diverted"] == 1
    assert summary["n_missing_tail"] == 1
    assert summary["n_invalid_timestamp"] == 1
    assert summary["n_timestamp_inconsistent"] == 1
    assert summary["n_implausible"] == 1


def test_clean_preserves_raw_unchanged():
    raw = load_raw(SAMPLE_FLIGHTS)
    raw_before = raw.copy()
    clean(raw)
    pd.testing.assert_frame_equal(raw, raw_before)


def test_clean_writes_files_and_report(tmp_path):
    raw = load_raw(SAMPLE_FLIGHTS)
    cleaned, removed, summary = clean(raw)
    out_dir = tmp_path / "processed"
    res_dir = tmp_path / "results"
    out_dir.mkdir(); res_dir.mkdir()

    cleaned.to_csv(out_dir / "flights_clean.csv", index=False)
    cleaned[cleaned["rotation_eligible"]].to_csv(out_dir / "flights_rotation_eligible.csv", index=False)
    removed.to_csv(out_dir / "duplicate_records.csv", index=False)

    report = generate_quality_report(
        raw, cleaned, summary,
        str(res_dir / "data_quality_report.md"),
        str(res_dir / "data_quality_summary.json"),
        source=SAMPLE_FLIGHTS,
    )
    assert os.path.exists(res_dir / "data_quality_report.md")
    assert os.path.exists(res_dir / "data_quality_summary.json")
    assert report["raw_shape"][0] == 8
    assert report["cleaned_shape"][0] == 7
    assert report["exact_duplicates_removed"] == 1
    assert "exclusion_reasons" in report


def test_cleaned_columns_include_standardized_names():
    cleaned, _, _ = _clean_df()
    for col in ["flight_date", "tail_number", "origin", "dest",
                "crs_dep_dt", "dep_dt", "crs_arr_dt", "arr_dt",
                "is_cancelled", "is_diverted", "rotation_eligible",
                "exclusion_reason"]:
        assert col in cleaned.columns, f"missing cleaned column: {col}"
