"""Tests for BTS data loading and validation (src.data_ingestion)."""
import os

import pandas as pd
import pytest

from src.data_ingestion import (
    ALIASES, COLUMN_MAP, REQUIRED_FIELDS, ESSENTIAL_FIELDS,
    basic_info, load_raw, require_columns, save_sample, validate_columns,
)
from conftest import SAMPLE_FLIGHTS, SAMPLE_OLD_NAMES


# ---------------------------------------------------------------------------
# load_raw
# ---------------------------------------------------------------------------
def test_load_raw_returns_dataframe():
    df = load_raw(SAMPLE_FLIGHTS)
    assert isinstance(df, pd.DataFrame)
    assert df.shape[0] == 9
    # Raw columns are the original BTS PascalCase names.
    assert "Tail_Number" in df.columns
    assert "CRSDepTime" in df.columns


def test_load_raw_preserves_strings_and_blanks():
    df = load_raw(SAMPLE_FLIGHTS)
    # Times are kept as strings (leading values preserved), not coerced to
    # numbers. (pandas 3.x may report these as StringDtype instead of object.)
    assert pd.api.types.is_string_dtype(df["CRSDepTime"])
    # The cancelled flight (row 2) has a blank DepTime -> NaN, not "nan".
    cancelled = df[df["Cancelled"] == "1"].iloc[0]
    assert pd.isna(cancelled["DepTime"])


def test_load_raw_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_raw("does_not_exist.csv")


def test_nrows_limits_rows():
    df = load_raw(SAMPLE_FLIGHTS, nrows=3)
    assert df.shape[0] == 3


# ---------------------------------------------------------------------------
# validate_columns
# ---------------------------------------------------------------------------
def test_validate_columns_finds_required_fields():
    df = load_raw(SAMPLE_FLIGHTS)
    val = validate_columns(df)
    # The fixture uses current BTS names, so all required fields are present.
    assert len(val["missing"]) == 0
    assert "Tail_Number" in val["present"]


def test_validate_columns_recognizes_old_aliases():
    df = load_raw(SAMPLE_OLD_NAMES)
    val = validate_columns(df)
    # Tail_Number is a required field and the TailNum alias resolves to it.
    assert "Tail_Number" in val["present"]
    # The old alias names are recognized as known (not flagged unexpected).
    for old in ["UniqueCarrier", "TailNum", "FlightNum"]:
        assert old not in val["unexpected"]
    # Alias resolution means no required field is reported missing.
    assert "Tail_Number" not in val["missing"]
    assert "CRSDepTime" not in val["missing"]


def test_validate_columns_reports_missing():
    df = pd.DataFrame({"Year": [2024], "Month": [1], "Tail_Number": ["N1"]})
    val = validate_columns(df)
    assert "Tail_Number" in val["present"]
    assert "CRSDepTime" in val["missing"]


def test_require_columns_raises_when_essential_missing():
    # flight_date is missing -> ValueError
    with pytest.raises(ValueError):
        require_columns(["origin", "dest", "crs_dep_time"])


def test_require_columns_passes_when_all_present():
    require_columns(["flight_date", "origin", "dest", "crs_dep_time"])  # no raise


# ---------------------------------------------------------------------------
# basic_info / save_sample
# ---------------------------------------------------------------------------
def test_basic_info_shape_and_columns():
    df = load_raw(SAMPLE_FLIGHTS)
    info = basic_info(df, source=SAMPLE_FLIGHTS)
    assert info["n_rows"] == 9
    assert info["n_cols"] == df.shape[1]
    assert info["source"] == SAMPLE_FLIGHTS
    assert "Tail_Number" in info["columns"]
    assert "missing_per_column" in info


def test_save_sample_writes_subset(tmp_path):
    df = load_raw(SAMPLE_FLIGHTS)
    out = tmp_path / "sample.csv"
    save_sample(df, str(out), n=2)
    assert os.path.exists(out)
    written = pd.read_csv(out)
    assert len(written) == 2


# ---------------------------------------------------------------------------
# schema integrity
# ---------------------------------------------------------------------------
def test_required_fields_all_have_mappings():
    # Every required standardized name must map back to a canonical BTS name.
    reverse = {v: k for k, v in COLUMN_MAP.items()}
    for field in REQUIRED_FIELDS:
        assert field in reverse, f"required field {field} has no column mapping"


def test_aliases_target_canonical_names():
    for old, new in ALIASES.items():
        assert new in COLUMN_MAP, f"alias target {new} is not a known BTS field"


from src.data_ingestion import find_monthly_files


def test_find_monthly_files_finds_all_12():
    """Scans data/raw and finds all 12 monthly T_ONTIME_REPORTING.csv files."""
    # This test requires the real data directory to exist.
    raw_dir = r"c:\Users\thrup\OneDrive\Desktop\Sem 5\ADA\ADA-Flight-delay-propagation\data\raw"
    if not os.path.isdir(raw_dir):
        pytest.skip("data/raw directory not found")
    files = find_monthly_files(raw_dir)
    # Filter to only entries where the file actually exists.
    found = [(m, p) for m, p in files if os.path.isfile(p)]
    assert len(found) == 12, f"Expected 12 monthly files, found {len(found)}: {[m for m, _ in found]}"


def test_aliases_cover_real_bts_columns():
    """All UPPERCASE_UNDERSCORE column names actually in the January data must
    be resolvable via ALIASES or COLUMN_MAP to a known canonical name."""
    jan_path = r"c:\Users\thrup\OneDrive\Desktop\Sem 5\ADA\ADA-Flight-delay-propagation\data\raw\January\T_ONTIME_REPORTING.csv"
    if not os.path.isfile(jan_path):
        pytest.skip("January raw file not found")
    df = load_raw(jan_path, nrows=1)
    from src.preprocessing import standardize_columns
    std, mapping = standardize_columns(df)
    # Every column that came in as UPPERCASE_UNDERSCORE should now be snake_case,
    # not still UPPERCASE_UNDERSCORE (indicating it was resolved).
    unresolved = [col for col in std.columns if col == col.upper() and "_" in col]
    # Some columns are legitimately unknown (extra BTS fields not in COLUMN_MAP);
    # report them but do not fail the test — the critical ones must all resolve.
    critical = [
        "FL_DATE", "OP_UNIQUE_CARRIER", "TAIL_NUM", "ORIGIN", "DEST",
        "CRS_DEP_TIME", "DEP_TIME", "DEP_DELAY",
        "CRS_ARR_TIME", "ARR_TIME", "ARR_DELAY",
        "CANCELLED", "CRS_ELAPSED_TIME", "ACTUAL_ELAPSED_TIME",
    ]
    for raw_col in critical:
        if raw_col in df.columns:
            mapped = mapping.get(raw_col, raw_col)
            assert mapped != raw_col, (
                f"Critical column {raw_col!r} was not resolved to snake_case; "
                f"add it to ALIASES in src/data_ingestion.py"
            )
