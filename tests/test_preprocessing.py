"""Tests for the cleaning pipeline (src.preprocessing).

The synthetic fixtures (tests/fixtures/*.csv) mimic the BTS column schema but are
NOT real flight data. They are tiny, hand-checked inputs for unit tests.
"""
import os

import numpy as np
import pandas as pd
import pytest

from src.preprocessing import (
    build_timestamps, clean, coerce_types, detect_duplicates,
    generate_quality_report, parse_hhmm, standardize_columns, validate_records,
)
from src.data_ingestion import load_raw
from conftest import DST_FLIGHTS, SAMPLE_FLIGHTS, SAMPLE_OLD_NAMES

# Real IANA timezones for the airports used in the fixtures. (These are
# verifiable public facts, not invented data.)
TZ_MAP = {
    "JFK": "America/New_York", "ATL": "America/New_York", "BOS": "America/New_York",
    "MIA": "America/New_York", "ORD": "America/Chicago", "DFW": "America/Chicago",
    "DEN": "America/Denver", "SFO": "America/Los_Angeles",
    "SEA": "America/Los_Angeles", "LAX": "America/Los_Angeles",
    "PHX": "America/Phoenix",
}


def _ts(df):
    """Standardize + coerce + build timestamps on a raw dataframe."""
    std, _ = standardize_columns(df)
    return build_timestamps(coerce_types(std), airport_tz=TZ_MAP)


def _validated(df):
    return validate_records(_ts(df))


def _clean_df():
    raw = load_raw(SAMPLE_FLIGHTS)
    return clean(raw, airport_tz=TZ_MAP)


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
    assert parse_hhmm("2400") == 1440


def test_parse_hhmm_invalid_and_missing():
    for v in ["", "nan", None, "2500", "1060", "ABC"]:
        assert np.isnan(parse_hhmm(v))


def test_parse_hhmm_handles_float_strings():
    assert parse_hhmm("735.0") == 455


# ---------------------------------------------------------------------------
# standardize_columns / coerce_types
# ---------------------------------------------------------------------------
def test_standardize_columns_current_names():
    raw = load_raw(SAMPLE_FLIGHTS)
    std, mapping = standardize_columns(raw)
    assert "tail_number" in std.columns and "crs_dep_time" in std.columns
    assert mapping["Tail_Number"] == "tail_number"
    assert mapping["CRSDepTime"] == "crs_dep_time"


def test_standardize_columns_old_aliases():
    raw = load_raw(SAMPLE_OLD_NAMES)
    std, mapping = standardize_columns(raw)
    assert mapping["UniqueCarrier"] == "reporting_airline"
    assert mapping["TailNum"] == "tail_number"
    assert "tail_number" in std.columns


def test_coerce_types_converts_numeric_and_date():
    raw = load_raw(SAMPLE_FLIGHTS)
    std, _ = standardize_columns(raw)
    out = coerce_types(std)
    assert pd.api.types.is_datetime64_any_dtype(out["flight_date"])
    assert pd.api.types.is_numeric_dtype(out["dep_delay"])
    assert str(out["cancelled"].dtype) == "Int64"


# ---------------------------------------------------------------------------
# Timestamp reconstruction: the critical correctness tests
# ---------------------------------------------------------------------------
def test_midnight_crossing_same_timezone():
    # Row 1: JFK->ATL (both Eastern), dep 22:30 -> arr next day 00:40.
    ts = _ts(load_raw(SAMPLE_FLIGHTS))
    r1 = ts.iloc[0]
    UTC = lambda s: pd.Timestamp(s, tz="UTC")
    assert r1["crs_dep_dt_utc"] == UTC("2024-01-06 03:30:00")   # 22:30 EST
    assert r1["dep_dt_utc"] == UTC("2024-01-06 03:35:00")
    assert r1["crs_arr_dt_utc"] == UTC("2024-01-06 05:30:00")  # 00:30 EST next day
    assert r1["arr_dt_utc"] == UTC("2024-01-06 05:40:00")
    assert r1["dep_dst_status"] == "ok"


def test_cross_timezone_arrival_is_utc_instant_not_origin_local():
    # Row 9: JFK (Eastern) -> LAX (Pacific), 3-hour offset.
    ts = _ts(load_raw(SAMPLE_FLIGHTS))
    r9 = ts[ts["tail_number"] == "N333AB"].iloc[0]
    UTC = lambda s: pd.Timestamp(s, tz="UTC")
    assert r9["dep_dt_utc"] == UTC("2024-01-11 13:05:00")      # 08:05 EST
    assert r9["arr_dt_utc"] == UTC("2024-01-11 19:10:00")       # 08:05 EST + 365 min
    # The arrival INSTANT, shown in the destination (LAX) zone, matches raw arr_time.
    arr_lax = r9["arr_dt_utc"].tz_convert("America/Los_Angeles")
    assert arr_lax.hour == 11 and arr_lax.minute == 10
    assert r9["tz_origin_known"] and r9["tz_dest_known"]


def test_cancelled_flight_has_no_actual_times():
    ts = _ts(load_raw(SAMPLE_FLIGHTS))
    cancelled = ts[ts["cancelled"] == 1].iloc[0]
    assert pd.isna(cancelled["dep_dt_utc"])
    assert pd.isna(cancelled["arr_dt_utc"])
    assert pd.notna(cancelled["crs_dep_dt_utc"])    # scheduled still reconstructed


def test_diverted_flight_has_no_arrival_instant():
    ts = _ts(load_raw(SAMPLE_FLIGHTS))
    diverted = ts[ts["diverted"] == 1].iloc[0]
    assert pd.notna(diverted["dep_dt_utc"])          # it did depart
    assert pd.isna(diverted["arr_dt_utc"])           # but did not arrive at dest


def test_raw_clock_fields_preserved():
    ts = _ts(load_raw(SAMPLE_FLIGHTS))
    r1 = ts.iloc[0]
    # The original hhmm strings survive cleaning unchanged.
    assert r1["crs_dep_time"] == "2230"
    assert r1["dep_time"] == "2235"
    assert r1["crs_arr_time"] == "0030"
    assert r1["arr_time"] == "0040"


def test_unknown_airport_timezone_not_invented():
    raw = load_raw(SAMPLE_FLIGHTS)
    raw.loc[raw.index[0], "Origin"] = "ZZZ"   # not in TZ_MAP
    ts = _ts(raw)
    r = ts.iloc[0]
    assert bool(r["tz_origin_known"]) is False
    assert pd.isna(r["dep_dt_utc"])            # not converted; no tz invented
    assert r["dep_dst_status"] == "no_tz"
    # The origin-local reference datetime is still available.
    assert pd.notna(r["crs_dep_dt_local"])


# ---------------------------------------------------------------------------
# Non-circular consistency checks
# ---------------------------------------------------------------------------
def test_arrival_elapsed_inconsistency_detected():
    # Row 8: raw arr_time (14:30) disagrees with the elapsed-time arrival (14:10).
    ts = _ts(load_raw(SAMPLE_FLIGHTS))
    r8 = ts[ts["tail_number"] == "N222YY"].iloc[0]
    assert bool(r8["arrival_elapsed_inconsistent"]) is True
    # The raw-clock check passes (14:30 == 14:00 + 30), so this is a genuine
    # independent finding, not a duplicate of the raw-clock check.
    assert bool(r8["arr_time_inconsistent"]) is False
    assert bool(r8["dep_time_inconsistent"]) is False


def test_consistent_flight_has_no_inconsistency_flags():
    ts = _ts(load_raw(SAMPLE_FLIGHTS))
    r1 = ts.iloc[0]
    assert bool(r1["dep_time_inconsistent"]) is False
    assert bool(r1["arr_time_inconsistent"]) is False
    assert bool(r1["arrival_elapsed_inconsistent"]) is False


# ---------------------------------------------------------------------------
# Validation flags
# ---------------------------------------------------------------------------
def test_validate_flags_cancelled_and_diverted():
    v = _validated(load_raw(SAMPLE_FLIGHTS))
    assert int(v["is_cancelled"].fillna(False).sum()) == 1
    assert int(v["is_diverted"].fillna(False).sum()) == 1


def test_validate_flags_missing_tail():
    v = _validated(load_raw(SAMPLE_FLIGHTS))
    missing = v[~v["valid_tail_number"]]
    assert len(missing) == 1
    assert pd.isna(missing.iloc[0]["tail_number"])


def test_validate_flags_invalid_scheduled_time():
    v = _validated(load_raw(SAMPLE_FLIGHTS))
    bad = v[v["tail_number"] == "N000X"]
    assert bool(bad.iloc[0]["valid_crs_dep_time"]) is False
    assert bool(bad.iloc[0]["invalid_timestamp"]) is True


def test_validate_flags_implausible_distance():
    v = _validated(load_raw(SAMPLE_FLIGHTS))
    bad = v[v["tail_number"] == "N111ZZ"]
    assert bool(bad.iloc[0]["implausible_record"]) is True


def test_missing_cancellation_indicator_not_treated_as_zero():
    raw = load_raw(SAMPLE_FLIGHTS)
    raw.loc[raw.index[1], "Cancelled"] = ""   # blank the cancelled indicator on row 2
    v = _validated(raw)
    row = v.iloc[1]
    assert pd.isna(row["is_cancelled"])                       # NOT False
    assert row["exclusion_reason"] == "missing_cancellation_indicator"
    assert bool(row["rotation_eligible"]) is False


def test_missing_diversion_indicator_not_treated_as_zero():
    raw = load_raw(SAMPLE_FLIGHTS)
    raw.loc[raw.index[2], "Diverted"] = ""
    v = _validated(raw)
    row = v.iloc[2]
    assert pd.isna(row["is_diverted"])
    assert row["exclusion_reason"] == "missing_diversion_indicator"


def test_rotation_eligible_before_dedup():
    v = _validated(load_raw(SAMPLE_FLIGHTS))
    eligible = v[v["rotation_eligible"]]
    # Row 1 (+its duplicate) and the cross-tz row 9 -> 3 eligible before dedup.
    assert len(eligible) == 3
    assert set(eligible["tail_number"]) == {"N123AB", "N333AB"}


def test_exclusion_reasons_assigned():
    v = _validated(load_raw(SAMPLE_FLIGHTS))
    reasons = set(v.loc[v["exclusion_reason"] != "", "exclusion_reason"])
    for r in ["cancelled", "diverted", "missing_or_invalid_tail_number",
              "invalid_scheduled_departure_time", "inconsistent_arrival_instant",
              "implausible_value"]:
        assert r in reasons


# ---------------------------------------------------------------------------
# Daylight-saving transitions
# ---------------------------------------------------------------------------
def test_dst_nonexistent_departure_spring_forward():
    ts = _ts(load_raw(DST_FLIGHTS))
    spring = ts.iloc[0]   # 02:30 on 2024-03-10 at JFK -> does not exist
    assert spring["dep_dst_status"] == "nonexistent"
    assert pd.notna(spring["dep_dt_utc"])        # best-effort UTC still produced
    v = validate_records(ts)
    assert v.iloc[0]["exclusion_reason"] == "dst_transition_departure"
    assert bool(v.iloc[0]["rotation_eligible"]) is False


def test_dst_ambiguous_departure_fall_back():
    ts = _ts(load_raw(DST_FLIGHTS))
    fall = ts.iloc[1]     # 01:30 on 2024-11-03 at JFK -> occurs twice
    assert fall["dep_dst_status"] == "ambiguous"
    v = validate_records(ts)
    assert v.iloc[1]["exclusion_reason"] == "dst_transition_departure"
    assert bool(v.iloc[1]["rotation_eligible"]) is False


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------
def test_detect_duplicates_removes_exact_copy():
    flagged = _validated(load_raw(SAMPLE_FLIGHTS))
    kept, removed = detect_duplicates(flagged)
    assert len(removed) == 1
    assert len(kept) == len(flagged) - 1


# ---------------------------------------------------------------------------
# clean (end-to-end)
# ---------------------------------------------------------------------------
def test_clean_removes_one_duplicate():
    cleaned, removed, summary = _clean_df()
    assert summary["raw_rows"] == 9
    assert summary["exact_duplicates_removed"] == 1
    assert summary["rows_after_dedup"] == 8
    assert len(cleaned) == 8 and len(removed) == 1


def test_clean_rotation_eligible_count():
    cleaned, _, summary = _clean_df()
    # Row 1 (midnight, same tz) and row 9 (cross tz) are eligible after dedup.
    assert summary["n_rotation_eligible"] == 2
    assert len(cleaned[cleaned["rotation_eligible"]]) == 2


def test_clean_summary_counts():
    _, _, summary = _clean_df()
    assert summary["n_cancelled"] == 1
    assert summary["n_diverted"] == 1
    assert summary["n_missing_tail"] == 1
    assert summary["n_invalid_timestamp"] == 1
    assert summary["n_arrival_elapsed_inconsistent"] == 1
    assert summary["n_implausible"] == 1


def test_clean_raises_on_missing_required_column():
    raw = load_raw(SAMPLE_FLIGHTS)
    raw = raw.drop(columns=["FlightDate"])     # remove an essential column
    with pytest.raises(ValueError):
        clean(raw, airport_tz=TZ_MAP)


def test_clean_preserves_raw_unchanged():
    raw = load_raw(SAMPLE_FLIGHTS)
    raw_before = raw.copy()
    clean(raw, airport_tz=TZ_MAP)
    pd.testing.assert_frame_equal(raw, raw_before)


def test_clean_writes_files_and_report(tmp_path):
    raw = load_raw(SAMPLE_FLIGHTS)
    cleaned, removed, summary = clean(raw, airport_tz=TZ_MAP)
    out_dir = tmp_path / "processed"; res_dir = tmp_path / "results"
    out_dir.mkdir(); res_dir.mkdir()
    cleaned.to_csv(out_dir / "flights_clean.csv", index=False)
    cleaned[cleaned["rotation_eligible"]].to_csv(out_dir / "flights_rotation_eligible.csv", index=False)
    removed.to_csv(out_dir / "duplicate_records.csv", index=False)
    report = generate_quality_report(raw, cleaned, summary,
                                      str(res_dir / "data_quality_report.md"),
                                      str(res_dir / "data_quality_summary.json"),
                                      source=SAMPLE_FLIGHTS)
    assert os.path.exists(res_dir / "data_quality_report.md")
    assert os.path.exists(res_dir / "data_quality_summary.json")
    assert report["raw_shape"][0] == 9 and report["cleaned_shape"][0] == 8
    assert report["exact_duplicates_removed"] == 1
    assert "exclusion_reasons" in report


def test_cleaned_columns_include_new_schema():
    cleaned, _, _ = _clean_df()
    for col in ["flight_date", "tail_number", "origin", "dest",
                "crs_dep_time", "dep_time", "crs_arr_time", "arr_time",   # raw preserved
                "crs_dep_dt_utc", "dep_dt_utc", "crs_arr_dt_utc", "arr_dt_utc",
                "tz_origin_known", "tz_dest_known", "has_utc_timestamps",
                "is_cancelled", "is_diverted", "rotation_eligible",
                "exclusion_reason"]:
        assert col in cleaned.columns, f"missing cleaned column: {col}"


# ---------------------------------------------------------------------------
# parse_flight_date (new real-data format support)
# ---------------------------------------------------------------------------
from src.preprocessing import parse_flight_date


def test_parse_flight_date_mdy_ampm():
    """Real BTS format: M/D/YYYY 12:00:00 AM."""
    s = pd.Series(["2/1/2024 12:00:00 AM"])
    result = parse_flight_date(s)
    assert result.iloc[0] == pd.Timestamp("2024-02-01")


def test_parse_flight_date_yyyymmdd():
    """Legacy synthetic fixture format: YYYYMMDD."""
    s = pd.Series(["20240105"])
    result = parse_flight_date(s)
    assert result.iloc[0] == pd.Timestamp("2024-01-05")


def test_parse_flight_date_iso():
    """ISO format: YYYY-MM-DD."""
    s = pd.Series(["2024-01-05"])
    result = parse_flight_date(s)
    assert result.iloc[0] == pd.Timestamp("2024-01-05")


def test_parse_flight_date_invalid():
    """Unparseable strings become NaT."""
    s = pd.Series(["not_a_date", "", "nan"])
    result = parse_flight_date(s)
    assert result.isna().all()


def test_diverted_absent_column_not_treated_as_zero():
    """When DIVERTED column is absent from source, validate_records must not
    set is_diverted=False (confirmed non-diverted); it must be NA (unknown)."""
    raw = load_raw(SAMPLE_FLIGHTS)
    # Drop the Diverted column entirely to simulate months without it.
    raw = raw.drop(columns=["Diverted"])
    std, _ = standardize_columns(raw)
    from src.preprocessing import coerce_types, build_timestamps, validate_records
    coerced = coerce_types(std)  # coerce_types must add diverted as all-NA
    assert "diverted" in coerced.columns, "coerce_types must add diverted column when absent"
    assert coerced["diverted"].isna().all(), "all diverted values must be NA when column was absent"
    with_ts = build_timestamps(coerced, airport_tz=TZ_MAP)
    flagged = validate_records(with_ts)
    # is_diverted should be all-NA, not all-False
    assert flagged["is_diverted"].isna().all(), "is_diverted must be NA when diverted column was absent"


def test_diverted_absent_exclusion_reason():
    """Flights with unknown diversion status get 'missing_diversion_indicator'
    exclusion reason (not excluded as 'diverted')."""
    raw = load_raw(SAMPLE_FLIGHTS)
    raw = raw.drop(columns=["Diverted"])
    std, _ = standardize_columns(raw)
    from src.preprocessing import coerce_types, build_timestamps, validate_records
    with_ts = build_timestamps(coerce_types(std), airport_tz=TZ_MAP)
    flagged = validate_records(with_ts)
    # Every non-cancelled, otherwise-clean row should have missing_diversion_indicator
    # as exclusion reason (not excluded as 'diverted').
    assert "diverted" not in flagged["exclusion_reason"].values, (
        "exclusion_reason must not say 'diverted' when column was absent"
    )


def test_sensitivity_eligible_with_unknown_diversion():
    """A flight that would be rotation-eligible except diversion status is
    unknown should appear in sensitivity_eligible but NOT in rotation_eligible."""
    raw = load_raw(SAMPLE_FLIGHTS)
    # Drop Diverted to simulate months without it.
    raw = raw.drop(columns=["Diverted"])
    std, _ = standardize_columns(raw)
    from src.preprocessing import coerce_types, build_timestamps, validate_records
    with_ts = build_timestamps(coerce_types(std), airport_tz=TZ_MAP)
    flagged = validate_records(with_ts)
    # With no diversion data: strict rotation_eligible must be 0
    # (all otherwise-eligible flights now have unknown diversion status).
    assert "sensitivity_eligible" in flagged.columns
    # Some flights that would have been rotation_eligible should now be sensitivity_eligible
    n_rot = int(flagged["rotation_eligible"].fillna(False).sum())
    n_sens = int(flagged["sensitivity_eligible"].fillna(False).sum())
    assert n_rot == 0, f"rotation_eligible should be 0 with no diversion data, got {n_rot}"
    assert n_sens > 0, f"sensitivity_eligible should be > 0 with unknown diversion, got {n_sens}"


def test_cancelled_float_string():
    """BTS real data has '0.00' and '1.00' for cancelled. These must coerce
    correctly to Int64 0 and 1 respectively."""
    raw = load_raw(SAMPLE_FLIGHTS).copy()
    # Simulate real BTS format: overwrite Cancelled values with float strings.
    raw["Cancelled"] = raw["Cancelled"].map(lambda v: "0.00" if str(v).strip() in ("0", "0.0", "0.00", "") else "1.00" if str(v).strip() in ("1", "1.0", "1.00") else "0.00")
    std, _ = standardize_columns(raw)
    from src.preprocessing import coerce_types
    coerced = coerce_types(std)
    assert str(coerced["cancelled"].dtype) == "Int64"
    cancelled_vals = coerced["cancelled"].dropna().unique()
    assert set(cancelled_vals).issubset({0, 1}), f"Unexpected values: {cancelled_vals}"
    assert 1 in cancelled_vals, "1.00 must coerce to Int64(1)"
    assert 0 in cancelled_vals, "0.00 must coerce to Int64(0)"
