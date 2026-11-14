"""Tests for the congestion feature engineering module (src.congestion_features).

All tests use synthetic data so no real BTS files are required.

Coverage
--------
* ``build_airport_index`` — correct timestamp sorting and grouping.
* ``compute_trailing_count`` — boundary correctness (open right, closed left),
  window width variants, empty arrays, exact-boundary timestamps.
* ``compute_congestion_features`` — end-to-end on a synthetic CSV:
  - Correct feature values verified by brute-force manual counts.
  - No future-information leakage (actual times are NOT in the output).
  - No duplicate ``flight_id`` values.
  - Output row count = input row count.
  - Join cardinality: merging on ``flight_id`` produces 1-to-1 rows.
  - Missing-timestamp rows produce NA features (not zeros, not errors).
  - Correct airport identifiers in lookup.
  - ``total_traffic_origin_1h`` = departures + arrivals at origin.
"""
from __future__ import annotations

import io
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from src.congestion_features import (
    WINDOW_1H,
    WINDOW_3H,
    OUTPUT_FEATURES,
    build_airport_index,
    compute_trailing_count,
    compute_congestion_features,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(s: str) -> np.datetime64:
    """Parse an ISO-8601 string to naive datetime64[us] (UTC implied)."""
    return np.datetime64(s, "us")


def _make_csv(rows: list[dict], path: str) -> None:
    """Write a minimal synthetic BTS-schema CSV to *path*."""
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)


# ---------------------------------------------------------------------------
# compute_trailing_count
# ---------------------------------------------------------------------------

class TestComputeTrailingCount:
    """Unit tests for the binary-search counting primitive."""

    def test_empty_array_returns_zero(self):
        arr = np.array([], dtype="datetime64[us]")
        result = compute_trailing_count(arr, _ts("2024-01-01T12:00:00"), WINDOW_1H)
        assert result == 0

    def test_single_element_inside_window(self):
        arr = np.array([_ts("2024-01-01T11:30:00")])  # 30 min before query
        result = compute_trailing_count(arr, _ts("2024-01-01T12:00:00"), WINDOW_1H)
        assert result == 1

    def test_single_element_outside_window(self):
        arr = np.array([_ts("2024-01-01T10:30:00")])  # 90 min before query -> outside 1h
        result = compute_trailing_count(arr, _ts("2024-01-01T12:00:00"), WINDOW_1H)
        assert result == 0

    def test_element_exactly_at_left_boundary_is_included(self):
        # Window is [t-1h, t). t-1h = 11:00. Element at 11:00 should be counted.
        arr = np.array([_ts("2024-01-01T11:00:00")])
        result = compute_trailing_count(arr, _ts("2024-01-01T12:00:00"), WINDOW_1H)
        assert result == 1, "Left boundary is CLOSED — element at t-W must be included"

    def test_element_exactly_at_right_boundary_is_excluded(self):
        # Window is [t-1h, t). Element at t itself should NOT be counted.
        arr = np.array([_ts("2024-01-01T12:00:00")])
        result = compute_trailing_count(arr, _ts("2024-01-01T12:00:00"), WINDOW_1H)
        assert result == 0, "Right boundary is OPEN — element at t must be excluded"

    def test_3h_window_counts_more_than_1h(self):
        arr = np.array([
            _ts("2024-01-01T09:30:00"),  # 2.5h before -> inside 3h, outside 1h
            _ts("2024-01-01T11:30:00"),  # 0.5h before -> inside both
        ])
        r1 = compute_trailing_count(arr, _ts("2024-01-01T12:00:00"), WINDOW_1H)
        r3 = compute_trailing_count(arr, _ts("2024-01-01T12:00:00"), WINDOW_3H)
        assert r1 == 1
        assert r3 == 2

    def test_vectorised_query_array(self):
        arr = np.array([
            _ts("2024-01-01T10:00:00"),
            _ts("2024-01-01T10:30:00"),
            _ts("2024-01-01T11:00:00"),
        ])
        queries = np.array([
            _ts("2024-01-01T11:00:00"),  # window [10:00,11:00) -> 2 elements
            _ts("2024-01-01T11:30:00"),  # window [10:30,11:30) -> 2 elements
        ])
        results = compute_trailing_count(arr, queries, WINDOW_1H)
        assert list(results) == [2, 2]

    def test_no_leakage_self_excluded(self):
        """A flight at time t must not count itself."""
        t = _ts("2024-01-01T12:00:00")
        arr = np.array([t])  # only the flight itself
        result = compute_trailing_count(arr, t, WINDOW_1H)
        assert result == 0, "Flight must not count itself"

    def test_sorted_array_required_gives_correct_result(self):
        # Sorted input is a precondition; verify sorted array gives expected count
        arr = np.sort(np.array([
            _ts("2024-01-01T11:00:00"),
            _ts("2024-01-01T11:15:00"),
            _ts("2024-01-01T11:45:00"),
            _ts("2024-01-01T12:30:00"),  # outside window
        ]))
        result = compute_trailing_count(arr, _ts("2024-01-01T12:00:00"), WINDOW_1H)
        assert result == 3  # 11:00, 11:15, 11:45 all in [11:00, 12:00)


# ---------------------------------------------------------------------------
# build_airport_index
# ---------------------------------------------------------------------------

class TestBuildAirportIndex:
    """Tests for the airport timestamp index builder."""

    def _make_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "origin": ["JFK", "JFK", "ATL", "JFK"],
            "dest":   ["ATL", "LAX", "JFK", "ATL"],
            "crs_dep_dt_utc": pd.to_datetime([
                "2024-01-01 10:00+00:00",
                "2024-01-01 11:00+00:00",
                "2024-01-01 12:00+00:00",
                "2024-01-01 10:30+00:00",
            ]),
            "crs_arr_dt_utc": pd.to_datetime([
                "2024-01-01 11:30+00:00",
                "2024-01-01 14:00+00:00",
                "2024-01-01 13:30+00:00",
                "2024-01-01 12:00+00:00",
            ]),
        })

    def test_origin_airports_present(self):
        df = self._make_df()
        dep_idx, _ = build_airport_index(df)
        assert "JFK" in dep_idx
        assert "ATL" in dep_idx

    def test_dest_airports_present(self):
        df = self._make_df()
        _, arr_idx = build_airport_index(df)
        assert "ATL" in arr_idx
        assert "LAX" in arr_idx
        assert "JFK" in arr_idx

    def test_dep_index_is_sorted(self):
        df = self._make_df()
        dep_idx, _ = build_airport_index(df)
        jfk_ts = dep_idx["JFK"]
        assert np.all(jfk_ts[:-1] <= jfk_ts[1:]), "dep_index must be sorted"

    def test_arr_index_is_sorted(self):
        df = self._make_df()
        _, arr_idx = build_airport_index(df)
        atl_ts = arr_idx["ATL"]
        assert np.all(atl_ts[:-1] <= atl_ts[1:]), "arr_index must be sorted"

    def test_dep_index_correct_counts(self):
        df = self._make_df()
        dep_idx, _ = build_airport_index(df)
        # JFK has 3 departures, ATL has 1
        assert len(dep_idx["JFK"]) == 3
        assert len(dep_idx["ATL"]) == 1

    def test_arr_index_correct_counts(self):
        df = self._make_df()
        _, arr_idx = build_airport_index(df)
        # ATL receives 2 arrivals, LAX 1, JFK 1
        assert len(arr_idx["ATL"]) == 2
        assert len(arr_idx["LAX"]) == 1
        assert len(arr_idx["JFK"]) == 1


# ---------------------------------------------------------------------------
# compute_congestion_features (end-to-end)
# ---------------------------------------------------------------------------

def _make_synthetic_csv(path: str) -> pd.DataFrame:
    """
    Build a small, precisely controlled synthetic dataset.

    Airport layout:
      - JFK: 4 departures, 2 arrivals (from ATL)
      - ATL: 2 departures, 4 arrivals (from JFK)
      - BOS: 1 departure, 0 arrivals

    All times are UTC.  Flight ids (row positions) are 0-7.
    """
    rows = [
        # flight 0: JFK->ATL dep 10:00 arr 11:30
        dict(flight_date="2024-01-05", reporting_airline="AA", flight_number=100,
             origin="JFK", dest="ATL",
             crs_dep_dt_utc="2024-01-05 10:00:00+00:00",
             crs_arr_dt_utc="2024-01-05 11:30:00+00:00"),
        # flight 1: JFK->ATL dep 10:30 arr 12:00
        dict(flight_date="2024-01-05", reporting_airline="AA", flight_number=101,
             origin="JFK", dest="ATL",
             crs_dep_dt_utc="2024-01-05 10:30:00+00:00",
             crs_arr_dt_utc="2024-01-05 12:00:00+00:00"),
        # flight 2: JFK->ATL dep 10:45 arr 12:15
        dict(flight_date="2024-01-05", reporting_airline="DL", flight_number=200,
             origin="JFK", dest="ATL",
             crs_dep_dt_utc="2024-01-05 10:45:00+00:00",
             crs_arr_dt_utc="2024-01-05 12:15:00+00:00"),
        # flight 3: JFK->BOS dep 11:00 arr 12:00  (different dest)
        dict(flight_date="2024-01-05", reporting_airline="B6", flight_number=300,
             origin="JFK", dest="BOS",
             crs_dep_dt_utc="2024-01-05 11:00:00+00:00",
             crs_arr_dt_utc="2024-01-05 12:00:00+00:00"),
        # flight 4: ATL->JFK dep 11:00 arr 12:30
        dict(flight_date="2024-01-05", reporting_airline="DL", flight_number=201,
             origin="ATL", dest="JFK",
             crs_dep_dt_utc="2024-01-05 11:00:00+00:00",
             crs_arr_dt_utc="2024-01-05 12:30:00+00:00"),
        # flight 5: ATL->JFK dep 12:00 arr 13:30
        dict(flight_date="2024-01-05", reporting_airline="DL", flight_number=202,
             origin="ATL", dest="JFK",
             crs_dep_dt_utc="2024-01-05 12:00:00+00:00",
             crs_arr_dt_utc="2024-01-05 13:30:00+00:00"),
        # flight 6: BOS->ATL dep 12:00 arr 14:00
        dict(flight_date="2024-01-05", reporting_airline="B6", flight_number=301,
             origin="BOS", dest="ATL",
             crs_dep_dt_utc="2024-01-05 12:00:00+00:00",
             crs_arr_dt_utc="2024-01-05 14:00:00+00:00"),
        # flight 7: missing timestamps — should produce NA features
        dict(flight_date="2024-01-05", reporting_airline="UA", flight_number=400,
             origin="JFK", dest="LAX",
             crs_dep_dt_utc=None,
             crs_arr_dt_utc=None),
    ]
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


class TestComputeCongestionFeatures:
    """End-to-end tests using a synthetic 8-row CSV."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.input_csv  = str(tmp_path / "flights.csv")
        self.output_csv = str(tmp_path / "congestion.csv")
        self.source_df  = _make_synthetic_csv(self.input_csv)
        compute_congestion_features(self.input_csv, self.output_csv, chunksize=100)
        self.out = pd.read_csv(self.output_csv)

    # -- Basic shape and cardinality --

    def test_output_row_count_equals_input(self):
        assert len(self.out) == len(self.source_df), (
            "Output must have exactly one row per input flight"
        )

    def test_no_duplicate_flight_ids(self):
        dups = self.out["flight_id"].duplicated().sum()
        assert dups == 0, f"Found {dups} duplicate flight_id values"

    def test_flight_ids_are_sequential_from_zero(self):
        expected = list(range(len(self.source_df)))
        assert list(self.out["flight_id"]) == expected

    def test_join_cardinality_1_to_1(self):
        """Joining congestion output back to source on flight_id must not multiply rows."""
        source = pd.read_csv(self.input_csv).reset_index().rename(columns={"index": "flight_id"})
        merged = source.merge(self.out, on="flight_id", how="left")
        assert len(merged) == len(source), (
            "Join on flight_id must not multiply rows"
        )

    # -- Correct feature values (verified by hand) --

    def test_dep_count_origin_1h_flight0(self):
        """
        Flight 0: JFK dep at 10:00.
        Window [09:00, 10:00): no other JFK departures scheduled before 10:00.
        Expected: 0.
        """
        val = self.out.loc[self.out["flight_id"] == 0, "dep_count_origin_1h"].iloc[0]
        assert val == 0

    def test_dep_count_origin_1h_flight2(self):
        """
        Flight 2: JFK dep at 10:45.
        Window [09:45, 10:45): flights 0 (10:00) and 1 (10:30) are in window.
        Expected: 2.
        """
        val = self.out.loc[self.out["flight_id"] == 2, "dep_count_origin_1h"].iloc[0]
        assert val == 2, f"Expected 2, got {val}"

    def test_dep_count_origin_1h_flight3(self):
        """
        Flight 3: JFK dep at 11:00.
        Window [10:00, 11:00): flights 0 (10:00), 1 (10:30), 2 (10:45) are in window.
        Expected: 3.
        """
        val = self.out.loc[self.out["flight_id"] == 3, "dep_count_origin_1h"].iloc[0]
        assert val == 3, f"Expected 3, got {val}"

    def test_dep_count_origin_3h_flight3(self):
        """
        Flight 3: JFK dep at 11:00.
        Window [08:00, 11:00): flights 0 (10:00), 1 (10:30), 2 (10:45) are in window.
        Expected: 3 (same as 1h because all JFK deps are clustered within 1h).
        """
        val = self.out.loc[self.out["flight_id"] == 3, "dep_count_origin_3h"].iloc[0]
        assert val == 3, f"Expected 3, got {val}"

    def test_arr_count_dest_1h_flight1(self):
        """
        Flight 1: ATL arr at 12:00.
        Window [11:00, 12:00): flight 0 arrives ATL at 11:30 -> in window.
        Expected: 1.
        """
        val = self.out.loc[self.out["flight_id"] == 1, "arr_count_dest_1h"].iloc[0]
        assert val == 1, f"Expected 1, got {val}"

    def test_arr_count_dest_1h_flight2(self):
        """
        Flight 2: ATL arr at 12:15.
        Window [11:15, 12:15): flight 0 (11:30) and flight 1 (12:00) are in window.
        Expected: 2.
        """
        val = self.out.loc[self.out["flight_id"] == 2, "arr_count_dest_1h"].iloc[0]
        assert val == 2, f"Expected 2, got {val}"

    def test_arr_count_dest_1h_flight0(self):
        """
        Flight 0: ATL arr at 11:30.
        Window [10:30, 11:30): no other ATL arrivals before 11:30.
        Expected: 0.
        """
        val = self.out.loc[self.out["flight_id"] == 0, "arr_count_dest_1h"].iloc[0]
        assert val == 0, f"Expected 0, got {val}"

    def test_total_traffic_origin_1h_flight3(self):
        """
        Flight 3: JFK dep at 11:00.
        Departures from JFK in [10:00, 11:00): 3 (flights 0, 1, 2).
        Arrivals at JFK in [10:00, 11:00): 0 (no flights arrive at JFK before 11:00).
        Expected total_traffic_origin_1h = 3 + 0 = 3.
        """
        val = self.out.loc[self.out["flight_id"] == 3, "total_traffic_origin_1h"].iloc[0]
        assert val == 3, f"Expected 3, got {val}"

    def test_total_traffic_origin_1h_flight5(self):
        """
        Flight 5: ATL dep at 12:00.
        Departures from ATL in [11:00, 12:00): flight 4 (dep 11:00) -> 1.
        Arrivals at ATL in [11:00, 12:00): flights 0 (arr 11:30) and 1 (arr 12:00, excluded).
        arr at 11:30 -> 1 arrival. arr at 12:00 is at boundary (open right) -> excluded.
        Expected total = 1 + 1 = 2.
        """
        val = self.out.loc[self.out["flight_id"] == 5, "total_traffic_origin_1h"].iloc[0]
        assert val == 2, f"Expected 2, got {val}"

    # -- Missing timestamp handling --

    def test_missing_timestamps_produce_na_features(self):
        """Flight 7 has null timestamps — all 5 features must be NA."""
        row = self.out.loc[self.out["flight_id"] == 7]
        for feat in OUTPUT_FEATURES:
            assert pd.isna(row[feat].iloc[0]), (
                f"Feature {feat} should be NA for flight with missing timestamp"
            )

    # -- No leakage checks --

    def test_actual_times_not_in_output(self):
        """Output must not contain actual departure/arrival time columns."""
        forbidden = {"dep_dt_utc", "arr_dt_utc", "dep_time", "arr_time",
                     "dep_delay", "arr_delay"}
        present = set(self.out.columns) & forbidden
        assert not present, f"Actual-time columns found in output: {present}"

    def test_features_use_only_scheduled_times(self):
        """
        Swap actual times for implausible values and rerun — output must be
        identical because actual times are never read.
        """
        import tempfile, shutil

        # Make a copy of the source with corrupted 'dep_dt_utc' and 'arr_dt_utc'
        src = pd.read_csv(self.input_csv)
        # Add actual-time columns with nonsense values
        src["dep_dt_utc"] = "9999-01-01 00:00:00+00:00"
        src["arr_dt_utc"] = "9999-01-01 00:00:00+00:00"

        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w",
                                         delete=False) as tmp_in:
            src.to_csv(tmp_in.name, index=False)
            tmp_in_path = tmp_in.name

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp_out:
            tmp_out_path = tmp_out.name

        try:
            compute_congestion_features(tmp_in_path, tmp_out_path, chunksize=100)
            new_out = pd.read_csv(tmp_out_path)
            # Feature values must be identical
            for feat in OUTPUT_FEATURES:
                orig = self.out[feat].fillna(-999)
                new  = new_out[feat].fillna(-999)
                pd.testing.assert_series_equal(
                    orig.reset_index(drop=True),
                    new.reset_index(drop=True),
                    check_names=False,
                    obj=f"Feature {feat} changed when actual times were corrupted",
                )
        finally:
            os.unlink(tmp_in_path)
            os.unlink(tmp_out_path)

    # -- Airport identifier checks --

    def test_origin_airport_correctly_used_for_dep_count(self):
        """
        BOS has only 1 departure (flight 6 at 12:00).
        Flight 6 dep window [11:00, 12:00) contains 0 BOS departures.
        Expected dep_count_origin_1h for flight 6 = 0.
        """
        val = self.out.loc[self.out["flight_id"] == 6, "dep_count_origin_1h"].iloc[0]
        assert val == 0, f"Expected 0, got {val}"

    def test_dest_airport_correctly_used_for_arr_count(self):
        """
        Flight 4: JFK arr at 12:30.
        Window [11:30, 12:30): no JFK arrivals in that window
        (ATL->JFK flight 5 arrives at 13:30, outside window).
        Expected arr_count_dest_1h = 0.
        """
        val = self.out.loc[self.out["flight_id"] == 4, "arr_count_dest_1h"].iloc[0]
        assert val == 0, f"Expected 0, got {val}"

    # -- Window boundary correctness --

    def test_left_boundary_is_inclusive(self):
        """
        Flight 1: JFK dep at 10:30.
        Window [09:30, 10:30): flight 0 departs at exactly 10:00 -> inside.
        Expected dep_count_origin_1h = 1.
        (This also confirms 10:30 itself is not counted — open right.)
        """
        val = self.out.loc[self.out["flight_id"] == 1, "dep_count_origin_1h"].iloc[0]
        assert val == 1, f"Expected 1, got {val}"

    def test_right_boundary_is_exclusive(self):
        """
        Flight 5 ATL dep at 12:00.
        Window [11:00, 12:00): flight 4 departs ATL at exactly 11:00 -> at left boundary, included.
        dep_count_origin_1h must be 1.
        """
        val = self.out.loc[self.out["flight_id"] == 5, "dep_count_origin_1h"].iloc[0]
        assert val == 1, f"Expected 1, got {val}"

    # -- Correctness: dep_count_3h >= dep_count_1h --

    def test_3h_window_ge_1h_window(self):
        """3-hour count must be >= 1-hour count for every non-NA row."""
        valid = self.out.dropna(subset=["dep_count_origin_1h", "dep_count_origin_3h"])
        violation = (valid["dep_count_origin_3h"] < valid["dep_count_origin_1h"]).sum()
        assert violation == 0, f"{violation} rows where 3h count < 1h count"

    def test_arr_3h_window_ge_arr_1h_window(self):
        valid = self.out.dropna(subset=["arr_count_dest_1h", "arr_count_dest_3h"])
        violation = (valid["arr_count_dest_3h"] < valid["arr_count_dest_1h"]).sum()
        assert violation == 0

    # -- total_traffic >= dep_count --

    def test_total_traffic_ge_dep_count(self):
        """Total traffic at origin must be >= departure count (arrivals are non-negative)."""
        valid = self.out.dropna(subset=["dep_count_origin_1h", "total_traffic_origin_1h"])
        violation = (valid["total_traffic_origin_1h"] < valid["dep_count_origin_1h"]).sum()
        assert violation == 0

    # -- Non-negative values --

    def test_all_features_non_negative(self):
        for feat in OUTPUT_FEATURES:
            col = self.out[feat].dropna()
            assert (col >= 0).all(), f"Negative values found in {feat}"
