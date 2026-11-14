"""Integration tests for external_features.csv and build_external_features.py.

These tests validate the final integrated dataset:
  - Row-count preservation (1:1 cardinality)
  - Unique key (no duplicate flight_ids)
  - Identity column alignment across all sources
  - weather_date = flight_date - 1 day (prior-day lag)
  - No unexpected column overlap with flight outcome variables
  - Missing value counts within expected ranges
  - No negative feature values
  - Correct column set (19 columns)
  - Deterministic / reproducible output
  - build_external_features CLI end-to-end on synthetic data
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from scripts.build_external_features import build_external_features, FINAL_COLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_congestion(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _make_weather(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _synthetic_cong(n=5):
    rows = []
    for i in range(n):
        rows.append(dict(
            flight_id=i,
            flight_date="2024-01-05",
            reporting_airline="AA",
            flight_number=100 + i,
            origin="JFK",
            dest="ATL",
            crs_dep_dt_utc="2024-01-05 10:00:00+00:00",
            crs_arr_dt_utc="2024-01-05 12:00:00+00:00",
            dep_count_origin_1h=i,
            dep_count_origin_3h=i * 2,
            arr_count_dest_1h=i + 1,
            arr_count_dest_3h=(i + 1) * 2,
            total_traffic_origin_1h=i * 2 + 1,
        ))
    return rows


def _synthetic_wx(n=5, missing_last=False):
    rows = []
    for i in range(n):
        rows.append(dict(
            flight_id=i,
            flight_date="2024-01-05",
            reporting_airline="AA",
            flight_number=100 + i,
            origin="JFK",
            dest="ATL",
            crs_dep_dt_utc="2024-01-05 10:00:00+00:00",
            weather_date="2024-01-04",        # flight_date - 1
            temp_c_prev=None if (missing_last and i == n - 1) else 2.0 + i,
            dewp_c_prev=-5.0 + i,
            visibility_km_prev=14.0,
            wind_ms_prev=3.0,
            gust_ms_prev=None,                # always null (sentinel)
            prcp_mm_prev=0.0,
        ))
    return rows


# ---------------------------------------------------------------------------
# build_external_features — end-to-end
# ---------------------------------------------------------------------------

class TestBuildExternalFeatures:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.cong_path = str(tmp_path / "cong.csv")
        self.wx_path   = str(tmp_path / "wx.csv")
        self.out_path  = str(tmp_path / "ext.csv")

        _make_congestion(self.cong_path, _synthetic_cong(5))
        _make_weather(self.wx_path, _synthetic_wx(5))
        build_external_features(self.cong_path, self.wx_path, self.out_path)
        self.out = pd.read_csv(self.out_path)

    def test_row_count_preserved(self):
        assert len(self.out) == 5

    def test_no_duplicate_flight_ids(self):
        assert self.out["flight_id"].duplicated().sum() == 0

    def test_flight_ids_sequential(self):
        assert list(self.out["flight_id"]) == [0, 1, 2, 3, 4]

    def test_column_set_exact(self):
        assert list(self.out.columns) == FINAL_COLS

    def test_column_count(self):
        assert len(self.out.columns) == 19

    def test_weather_date_is_flight_date_minus_one(self):
        self.out["flight_date"] = pd.to_datetime(self.out["flight_date"])
        self.out["weather_date"] = pd.to_datetime(self.out["weather_date"])
        lag_ok = ((self.out["flight_date"] - self.out["weather_date"]).dt.days == 1).all()
        assert lag_ok

    def test_congestion_values_correct(self):
        for i in range(5):
            row = self.out[self.out["flight_id"] == i].iloc[0]
            assert row["dep_count_origin_1h"] == i
            assert row["dep_count_origin_3h"] == i * 2
            assert row["arr_count_dest_1h"] == i + 1
            assert row["total_traffic_origin_1h"] == i * 2 + 1

    def test_weather_values_correct(self):
        for i in range(5):
            row = self.out[self.out["flight_id"] == i].iloc[0]
            assert row["temp_c_prev"] == pytest.approx(2.0 + i, rel=1e-6)
            assert pd.isna(row["gust_ms_prev"]), "gust_ms_prev should be NaN"

    def test_missing_weather_propagates_as_na(self, tmp_path):
        """Last row with missing weather should have NA in weather features."""
        cong = str(tmp_path / "c2.csv")
        wx   = str(tmp_path / "w2.csv")
        out  = str(tmp_path / "e2.csv")
        _make_congestion(cong, _synthetic_cong(5))
        _make_weather(wx, _synthetic_wx(5, missing_last=True))
        build_external_features(cong, wx, out)
        df = pd.read_csv(out)
        last = df[df["flight_id"] == 4].iloc[0]
        assert pd.isna(last["temp_c_prev"])

    def test_actual_outcome_columns_absent(self):
        forbidden = {"dep_delay", "arr_delay", "dep_dt_utc", "arr_dt_utc",
                     "actual_elapsed_time", "taxi_out", "taxi_in"}
        present = set(self.out.columns) & forbidden
        assert not present, f"Outcome columns leaked into output: {present}"

    def test_no_negative_congestion(self):
        cong_cols = ["dep_count_origin_1h","dep_count_origin_3h",
                     "arr_count_dest_1h","arr_count_dest_3h","total_traffic_origin_1h"]
        for c in cong_cols:
            assert (self.out[c] >= 0).all()

    def test_no_negative_wind(self):
        valid_wind = self.out["wind_ms_prev"].dropna()
        assert (valid_wind >= 0).all()

    def test_deterministic_output(self, tmp_path):
        out2 = str(tmp_path / "ext2.csv")
        build_external_features(self.cong_path, self.wx_path, out2)
        df1 = pd.read_csv(self.out_path)
        df2 = pd.read_csv(out2)
        pd.testing.assert_frame_equal(df1, df2)

    def test_join_cardinality_1to1(self, tmp_path):
        """Merging output back to congestion source must produce no extra rows."""
        src = pd.read_csv(self.cong_path)
        merged = src.merge(self.out, on="flight_id", how="left")
        assert len(merged) == len(src)


# ---------------------------------------------------------------------------
# Integration tests against the REAL external_features.csv (if present)
# ---------------------------------------------------------------------------

REAL_EXT = "data/processed/external_features.csv"
REAL_CONG = "data/processed/congestion_features.csv"
REAL_WX   = "data/processed/weather_features.csv"

real_data_present = pytest.mark.skipif(
    not (os.path.exists(REAL_EXT) and
         os.path.exists(REAL_CONG) and
         os.path.exists(REAL_WX)),
    reason="Real feature files not present (expected in CI/CD or local run)"
)


@real_data_present
class TestRealExternalFeatures:
    """Validation against the actual 2.3M-row dataset."""

    @pytest.fixture(scope="class", autouse=True)
    def load(self, request):
        request.cls.ef = pd.read_csv(REAL_EXT)

    def test_row_count(self):
        assert len(self.ef) == 2_319_468

    def test_no_duplicate_flight_ids(self):
        assert self.ef["flight_id"].duplicated().sum() == 0

    def test_flight_ids_sequential(self):
        assert self.ef["flight_id"].min() == 0
        assert self.ef["flight_id"].max() == 2_319_467
        assert len(self.ef) == self.ef["flight_id"].max() + 1

    def test_column_set(self):
        assert list(self.ef.columns) == FINAL_COLS

    def test_column_count(self):
        assert len(self.ef.columns) == 19

    def test_weather_date_lag(self):
        sample = self.ef.head(100_000)
        fd = pd.to_datetime(sample["flight_date"])
        wd = pd.to_datetime(sample["weather_date"])
        assert ((fd - wd).dt.days == 1).all()

    def test_months_covered(self):
        months = set(pd.to_datetime(self.ef["flight_date"]).dt.month.unique())
        assert months == {1, 6, 7, 8}

    def test_congestion_complete(self):
        cong_cols = ["dep_count_origin_1h","dep_count_origin_3h",
                     "arr_count_dest_1h","arr_count_dest_3h","total_traffic_origin_1h"]
        for c in cong_cols:
            assert self.ef[c].isna().sum() == 0, f"{c} has unexpected nulls"

    def test_weather_missingness_within_bounds(self):
        # temp_c_prev must be < 1% missing
        pct = self.ef["temp_c_prev"].isna().mean() * 100
        assert pct < 1.0, f"temp_c_prev missing {pct:.2f}% — unexpected"

    def test_gust_missingness_range(self):
        # gust is 31.2% missing from GSOD; must be between 25% and 40%
        pct = self.ef["gust_ms_prev"].isna().mean() * 100
        assert 25 <= pct <= 40, f"gust_ms_prev missing {pct:.2f}% — outside expected range"

    def test_no_negative_congestion(self):
        cong_cols = ["dep_count_origin_1h","dep_count_origin_3h",
                     "arr_count_dest_1h","arr_count_dest_3h","total_traffic_origin_1h"]
        for c in cong_cols:
            assert (self.ef[c] >= 0).all(), f"Negative values in {c}"

    def test_no_negative_weather_physical(self):
        for c in ["wind_ms_prev","gust_ms_prev","visibility_km_prev","prcp_mm_prev"]:
            col = self.ef[c].dropna()
            assert (col >= 0).all(), f"Negative values in {c}"

    def test_outcome_columns_absent(self):
        forbidden = {"dep_delay","arr_delay","dep_dt_utc","arr_dt_utc",
                     "actual_elapsed_time","tail_number"}
        present = set(self.ef.columns) & forbidden
        assert not present

    def test_identity_consistent_with_congestion(self):
        cong_sample = pd.read_csv(REAL_CONG,
            usecols=["flight_id","origin","flight_date"], nrows=50_000)
        ext_sample = self.ef[["flight_id","origin","flight_date"]].head(50_000)
        merged = cong_sample.merge(ext_sample, on="flight_id", suffixes=("_c","_e"))
        assert len(merged) == 50_000
        assert (merged["origin_c"] == merged["origin_e"]).all()
        assert (merged["flight_date_c"].astype(str) == merged["flight_date_e"].astype(str)).all()
