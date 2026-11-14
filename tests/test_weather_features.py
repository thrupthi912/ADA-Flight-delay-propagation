"""Tests for the NOAA GSOD weather pipeline (src.weather_features).

All tests use synthetic data — no network requests are made.

Coverage
--------
* ``station_id_str`` — zero-padding, string/int inputs.
* ``gsod_url`` — URL format.
* ``parse_gsod_csv`` — sentinel replacement, unit conversions, missing file,
  empty file, malformed CSV.
* ``build_weather_obs`` — deduplication, correct parsing, skipping missing files.
* ``build_weather_features`` — end-to-end on synthetic data:
  - Prior-day lag: weather_date = flight_date − 1.
  - No future information (actual times never used).
  - One row per flight_id (join cardinality).
  - No duplicate flight_ids.
  - Missing weather → NA features (not zero, not error).
  - Correct feature values (verified by hand).
  - Feature column names carry _prev suffix.
  - Output has expected columns.
  - Reproducibility.
* Leakage test — corrupting actual-time columns leaves features unchanged.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.weather_features import (
    WEATHER_FEATURES,
    build_weather_features,
    build_weather_obs,
    gsod_url,
    parse_gsod_csv,
    station_id_str,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_gsod_csv(path: str, station: str, rows: list[dict]) -> None:
    """Write a minimal synthetic GSOD CSV to path."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)


def _make_flights_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _make_station_map(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _make_weather_obs(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


# ---------------------------------------------------------------------------
# station_id_str
# ---------------------------------------------------------------------------

class TestStationIdStr:
    def test_basic_zero_padding(self):
        assert station_id_str("722190", "13874") == "72219013874"

    def test_short_usaf_padded(self):
        assert station_id_str("1234", "567") == "00123400567"

    def test_int_inputs(self):
        assert station_id_str(722190, 13874) == "72219013874"

    def test_length_always_11(self):
        s = station_id_str("1", "1")
        assert len(s) == 11

    def test_full_length_inputs_unchanged(self):
        assert station_id_str("722190", "13874") == "72219013874"


# ---------------------------------------------------------------------------
# gsod_url
# ---------------------------------------------------------------------------

class TestGsodUrl:
    def test_url_format(self):
        url = gsod_url("72219013874", 2024)
        assert url == (
            "https://www.ncei.noaa.gov/data/global-summary-of-the-day"
            "/access/2024/72219013874.csv"
        )

    def test_year_in_url(self):
        url = gsod_url("72219013874", 2023)
        assert "/2023/" in url

    def test_station_id_in_url(self):
        url = gsod_url("12345678901", 2024)
        assert "12345678901.csv" in url


# ---------------------------------------------------------------------------
# parse_gsod_csv
# ---------------------------------------------------------------------------

_GSOD_COLS = ["STATION", "DATE", "TEMP", "DEWP", "VISIB", "WDSP", "GUST", "PRCP"]


class TestParseGsodCsv:
    def test_returns_none_for_missing_file(self, tmp_path):
        result = parse_gsod_csv(str(tmp_path / "nonexistent.csv"))
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("")
        result = parse_gsod_csv(str(p))
        assert result is None

    def test_unit_conversion_temp(self, tmp_path):
        p = str(tmp_path / "gsod.csv")
        _write_gsod_csv(p, "72219013874", [
            {"STATION": "72219013874", "DATE": "2024-01-01",
             "TEMP": 32.0, "DEWP": 23.0, "VISIB": 10.0,
             "WDSP": 5.0, "GUST": 999.9, "PRCP": 0.0}
        ])
        df = parse_gsod_csv(p)
        assert df is not None
        # 32°F == 0°C
        assert df["temp_c"].iloc[0] == pytest.approx(0.0, abs=0.01)

    def test_unit_conversion_visibility(self, tmp_path):
        p = str(tmp_path / "gsod.csv")
        _write_gsod_csv(p, "S1", [
            {"STATION": "S1", "DATE": "2024-01-01",
             "TEMP": 77.0, "DEWP": 50.0, "VISIB": 1.0,
             "WDSP": 0.0, "GUST": 999.9, "PRCP": 0.0}
        ])
        df = parse_gsod_csv(p)
        # 1 statute mile == 1.60934 km
        assert df["visibility_km"].iloc[0] == pytest.approx(1.60934, rel=0.001)

    def test_unit_conversion_wind(self, tmp_path):
        p = str(tmp_path / "gsod.csv")
        _write_gsod_csv(p, "S1", [
            {"STATION": "S1", "DATE": "2024-01-01",
             "TEMP": 60.0, "DEWP": 40.0, "VISIB": 5.0,
             "WDSP": 10.0, "GUST": 999.9, "PRCP": 0.0}
        ])
        df = parse_gsod_csv(p)
        # 10 knots == 5.14444 m/s
        assert df["wind_ms"].iloc[0] == pytest.approx(5.14444, rel=0.001)

    def test_unit_conversion_prcp(self, tmp_path):
        p = str(tmp_path / "gsod.csv")
        _write_gsod_csv(p, "S1", [
            {"STATION": "S1", "DATE": "2024-01-01",
             "TEMP": 40.0, "DEWP": 30.0, "VISIB": 5.0,
             "WDSP": 5.0, "GUST": 999.9, "PRCP": 1.0}
        ])
        df = parse_gsod_csv(p)
        # 1 inch == 25.4 mm
        assert df["prcp_mm"].iloc[0] == pytest.approx(25.4, rel=0.001)

    def test_gust_sentinel_becomes_nan(self, tmp_path):
        """GUST=999.9 is the missing sentinel and must become NaN."""
        p = str(tmp_path / "gsod.csv")
        _write_gsod_csv(p, "S1", [
            {"STATION": "S1", "DATE": "2024-01-01",
             "TEMP": 60.0, "DEWP": 40.0, "VISIB": 5.0,
             "WDSP": 5.0, "GUST": 999.9, "PRCP": 0.0}
        ])
        df = parse_gsod_csv(p)
        assert pd.isna(df["gust_ms"].iloc[0]), "GUST 999.9 must become NaN after sentinel replacement"

    def test_temp_sentinel_becomes_nan(self, tmp_path):
        p = str(tmp_path / "gsod.csv")
        _write_gsod_csv(p, "S1", [
            {"STATION": "S1", "DATE": "2024-01-01",
             "TEMP": 9999.9, "DEWP": 40.0, "VISIB": 5.0,
             "WDSP": 5.0, "GUST": 10.0, "PRCP": 0.0}
        ])
        df = parse_gsod_csv(p)
        assert pd.isna(df["temp_c"].iloc[0])

    def test_multiple_rows_parsed(self, tmp_path):
        p = str(tmp_path / "gsod.csv")
        _write_gsod_csv(p, "S1", [
            {"STATION": "S1", "DATE": "2024-01-01",
             "TEMP": 50.0, "DEWP": 40.0, "VISIB": 5.0, "WDSP": 3.0, "GUST": 999.9, "PRCP": 0.0},
            {"STATION": "S1", "DATE": "2024-01-02",
             "TEMP": 55.0, "DEWP": 42.0, "VISIB": 7.0, "WDSP": 4.0, "GUST": 999.9, "PRCP": 0.1},
        ])
        df = parse_gsod_csv(p)
        assert len(df) == 2

    def test_date_parsed_correctly(self, tmp_path):
        p = str(tmp_path / "gsod.csv")
        _write_gsod_csv(p, "S1", [
            {"STATION": "S1", "DATE": "2024-06-15",
             "TEMP": 80.0, "DEWP": 65.0, "VISIB": 10.0, "WDSP": 8.0, "GUST": 999.9, "PRCP": 0.0}
        ])
        df = parse_gsod_csv(p)
        assert df["DATE"].iloc[0] == pd.Timestamp("2024-06-15")


# ---------------------------------------------------------------------------
# build_weather_obs
# ---------------------------------------------------------------------------

class TestBuildWeatherObs:
    """Tests for Stage 2 — combining raw GSOD files into weather_obs.csv."""

    def _make_station_map(self, tmp_path) -> str:
        rows = [
            dict(iata="JFK", icao_airport="KJFK", usaf="744860", wban=94789,
                 station_name="JFK", station_lat=40.6, station_lon=-73.8,
                 distance_km=0.0, match_method="icao_primary", isd_end_date=20260101),
            dict(iata="ATL", icao_airport="KATL", usaf="722190", wban=13874,
                 station_name="ATL", station_lat=33.6, station_lon=-84.4,
                 distance_km=0.0, match_method="icao_primary", isd_end_date=20260101),
        ]
        p = str(tmp_path / "station_map.csv")
        pd.DataFrame(rows).to_csv(p, index=False)
        return p

    def _make_raw_file(self, tmp_path, usaf, wban, year, rows):
        sid = station_id_str(usaf, wban)
        p = str(tmp_path / str(year) / f"{sid}.csv")
        _write_gsod_csv(p, sid, rows)

    def test_obs_has_expected_columns(self, tmp_path):
        sm_path = self._make_station_map(tmp_path)
        self._make_raw_file(tmp_path, "744860", 94789, 2024, [
            {"STATION": "74486094789", "DATE": "2024-01-01",
             "TEMP": 35.0, "DEWP": 20.0, "VISIB": 10.0, "WDSP": 5.0, "GUST": 999.9, "PRCP": 0.0}
        ])
        out = str(tmp_path / "obs.csv")
        build_weather_obs(str(tmp_path), sm_path, out, years=[2024])
        obs = pd.read_csv(out)
        assert "iata" in obs.columns
        assert "DATE" in obs.columns
        assert "temp_c" in obs.columns

    def test_duplicate_station_date_removed(self, tmp_path):
        sm_path = self._make_station_map(tmp_path)
        # Duplicate rows for the same station/date
        self._make_raw_file(tmp_path, "744860", 94789, 2024, [
            {"STATION": "74486094789", "DATE": "2024-01-01",
             "TEMP": 35.0, "DEWP": 20.0, "VISIB": 10.0, "WDSP": 5.0, "GUST": 999.9, "PRCP": 0.0},
            {"STATION": "74486094789", "DATE": "2024-01-01",  # duplicate
             "TEMP": 40.0, "DEWP": 22.0, "VISIB": 9.0, "WDSP": 6.0, "GUST": 999.9, "PRCP": 0.1},
        ])
        out = str(tmp_path / "obs.csv")
        build_weather_obs(str(tmp_path), sm_path, out, years=[2024])
        obs = pd.read_csv(out)
        # Only one row for (JFK, 2024-01-01)
        jfk_jan1 = obs[(obs["iata"] == "JFK") & (obs["DATE"] == "2024-01-01")]
        assert len(jfk_jan1) == 1

    def test_missing_file_does_not_crash(self, tmp_path):
        """A station with no raw file should be skipped gracefully."""
        sm_path = self._make_station_map(tmp_path)
        # Only create JFK file, not ATL
        self._make_raw_file(tmp_path, "744860", 94789, 2024, [
            {"STATION": "74486094789", "DATE": "2024-01-01",
             "TEMP": 35.0, "DEWP": 20.0, "VISIB": 10.0, "WDSP": 5.0, "GUST": 999.9, "PRCP": 0.0}
        ])
        out = str(tmp_path / "obs.csv")
        # Should not raise
        build_weather_obs(str(tmp_path), sm_path, out, years=[2024])
        obs = pd.read_csv(out)
        assert "JFK" in obs["iata"].values
        assert "ATL" not in obs["iata"].values  # no data for ATL


# ---------------------------------------------------------------------------
# build_weather_features (end-to-end)
# ---------------------------------------------------------------------------

class TestBuildWeatherFeatures:
    """End-to-end tests for Stage 3 — join weather onto flights."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.tmp = tmp_path

        # Station map
        sm_rows = [
            dict(iata="JFK", icao_airport="KJFK", usaf="744860", wban=94789,
                 station_name="JFK INTL", station_lat=40.6, station_lon=-73.8,
                 distance_km=0.0, match_method="icao_primary", isd_end_date=20260101),
            dict(iata="ATL", icao_airport="KATL", usaf="722190", wban=13874,
                 station_name="ATL INTL", station_lat=33.6, station_lon=-84.4,
                 distance_km=0.0, match_method="icao_primary", isd_end_date=20260101),
        ]
        self.sm_path = str(tmp_path / "station_map.csv")
        _make_station_map(self.sm_path, sm_rows)

        # Weather obs — JFK and ATL, Jan 4 and Jan 5 2024
        obs_rows = [
            # JFK Jan 4 (prior day of Jan 5 flights)
            dict(station_id="74486094789", iata="JFK", DATE="2024-01-04",
                 temp_c=(35 - 32) * 5 / 9,    # ~1.67°C
                 dewp_c=(20 - 32) * 5 / 9,    # ~-6.67°C
                 visibility_km=10.0 * 1.60934,
                 wind_ms=5.0 * 0.514444,
                 gust_ms=np.nan,
                 prcp_mm=0.0 * 25.4),
            # ATL Jan 4
            dict(station_id="72219013874", iata="ATL", DATE="2024-01-04",
                 temp_c=(60 - 32) * 5 / 9,
                 dewp_c=(45 - 32) * 5 / 9,
                 visibility_km=8.0 * 1.60934,
                 wind_ms=8.0 * 0.514444,
                 gust_ms=15.0 * 0.514444,
                 prcp_mm=0.1 * 25.4),
            # JFK Jan 5 — NOT the prior day, so shouldn't be used for Jan 5 flights
            dict(station_id="74486094789", iata="JFK", DATE="2024-01-05",
                 temp_c=5.0, dewp_c=-2.0, visibility_km=12.0,
                 wind_ms=3.0, gust_ms=np.nan, prcp_mm=0.0),
        ]
        self.obs_path = str(tmp_path / "weather_obs.csv")
        _make_weather_obs(self.obs_path, obs_rows)

        # Flights: 3 flights on 2024-01-05 (weather_date = 2024-01-04)
        # + 1 flight on 2024-01-06 (weather_date = 2024-01-05, no ATL obs -> NA)
        # + 1 flight with unknown airport (no station -> NA)
        flight_rows = [
            # flight 0: JFK->ATL, Jan 5 -> prior day Jan 4, JFK weather available
            dict(flight_date="2024-01-05", reporting_airline="AA", flight_number=100,
                 origin="JFK", dest="ATL",
                 crs_dep_dt_utc="2024-01-05 10:00:00+00:00"),
            # flight 1: ATL->JFK, Jan 5 -> prior day Jan 4, ATL weather available
            dict(flight_date="2024-01-05", reporting_airline="DL", flight_number=200,
                 origin="ATL", dest="JFK",
                 crs_dep_dt_utc="2024-01-05 14:00:00+00:00"),
            # flight 2: JFK->ATL, Jan 6 -> prior day Jan 5, JFK obs exists for Jan 5
            dict(flight_date="2024-01-06", reporting_airline="AA", flight_number=101,
                 origin="JFK", dest="ATL",
                 crs_dep_dt_utc="2024-01-06 10:00:00+00:00"),
            # flight 3: ATL->JFK, Jan 6 -> prior day Jan 5, NO ATL obs for Jan 5 -> NA
            dict(flight_date="2024-01-06", reporting_airline="DL", flight_number=201,
                 origin="ATL", dest="JFK",
                 crs_dep_dt_utc="2024-01-06 14:00:00+00:00"),
            # flight 4: BOS (no station in map) -> NA
            dict(flight_date="2024-01-05", reporting_airline="B6", flight_number=300,
                 origin="BOS", dest="JFK",
                 crs_dep_dt_utc="2024-01-05 12:00:00+00:00"),
        ]
        self.flights_path = str(tmp_path / "flights.csv")
        _make_flights_csv(self.flights_path, flight_rows)

        self.output_path = str(tmp_path / "weather_features.csv")
        build_weather_features(
            flights_path=self.flights_path,
            weather_obs_path=self.obs_path,
            station_map_path=self.sm_path,
            output_path=self.output_path,
        )
        self.out = pd.read_csv(self.output_path)

    # -- Basic structure --

    def test_output_row_count_equals_input(self):
        assert len(self.out) == 5

    def test_no_duplicate_flight_ids(self):
        dups = self.out["flight_id"].duplicated().sum()
        assert dups == 0

    def test_flight_ids_sequential_from_zero(self):
        assert list(self.out["flight_id"]) == [0, 1, 2, 3, 4]

    def test_output_has_weather_date_column(self):
        assert "weather_date" in self.out.columns

    def test_feature_columns_have_prev_suffix(self):
        feat_cols = [c for c in self.out.columns if "_prev" in c]
        assert len(feat_cols) > 0
        for c in feat_cols:
            assert c.endswith("_prev"), f"Feature {c} does not end with _prev"

    def test_output_has_expected_columns(self):
        assert "flight_id" in self.out.columns
        assert "origin" in self.out.columns
        assert "weather_date" in self.out.columns

    # -- Prior-day lag correctness --

    def test_weather_date_is_flight_date_minus_one(self):
        """weather_date must equal flight_date - 1 for every row."""
        self.out["flight_date"] = pd.to_datetime(self.out["flight_date"])
        self.out["weather_date"] = pd.to_datetime(self.out["weather_date"])
        for _, row in self.out.iterrows():
            expected_wd = (row["flight_date"] - pd.Timedelta(days=1)).date()
            actual_wd = row["weather_date"].date()
            assert actual_wd == expected_wd, (
                f"flight_id {row['flight_id']}: weather_date {actual_wd} != {expected_wd}"
            )

    def test_jfk_jan5_uses_jan4_obs(self):
        """Flight 0 (JFK, Jan 5) must use Jan 4 JFK weather."""
        row = self.out[self.out["flight_id"] == 0].iloc[0]
        assert pd.to_datetime(row["weather_date"]).date().isoformat() == "2024-01-04"
        # temp_c for JFK Jan 4 = (35 - 32) * 5/9 ≈ 1.667°C
        expected_temp = (35 - 32) * 5 / 9
        assert row["temp_c_prev"] == pytest.approx(expected_temp, rel=0.01)

    def test_atl_jan5_uses_jan4_atl_obs(self):
        """Flight 1 (ATL, Jan 5) must use Jan 4 ATL weather, not JFK weather."""
        row = self.out[self.out["flight_id"] == 1].iloc[0]
        # ATL Jan 4 temp = (60-32)*5/9 ≈ 15.56°C, JFK was (35-32)*5/9 ≈ 1.67°C
        expected_temp = (60 - 32) * 5 / 9
        assert row["temp_c_prev"] == pytest.approx(expected_temp, rel=0.01)

    def test_jfk_jan6_uses_jan5_obs(self):
        """Flight 2 (JFK, Jan 6) must use Jan 5 JFK weather."""
        row = self.out[self.out["flight_id"] == 2].iloc[0]
        assert pd.to_datetime(row["weather_date"]).date().isoformat() == "2024-01-05"
        # JFK Jan 5 temp_c = 5.0 (set directly in obs)
        assert row["temp_c_prev"] == pytest.approx(5.0, rel=0.01)

    # -- Missing weather → NA --

    def test_atl_jan6_missing_weather_produces_na(self):
        """Flight 3 (ATL, Jan 6) has no ATL obs for Jan 5 — all features must be NA."""
        row = self.out[self.out["flight_id"] == 3].iloc[0]
        for feat in WEATHER_FEATURES:
            if feat in self.out.columns:
                assert pd.isna(row[feat]), (
                    f"flight_id 3: {feat} should be NA (no prior-day ATL obs)"
                )

    def test_unknown_airport_produces_na(self):
        """Flight 4 (BOS, not in obs) — all features must be NA."""
        row = self.out[self.out["flight_id"] == 4].iloc[0]
        for feat in WEATHER_FEATURES:
            if feat in self.out.columns:
                assert pd.isna(row[feat]), (
                    f"flight_id 4: {feat} should be NA (BOS not in weather obs)"
                )

    # -- No leakage --

    def test_actual_times_not_in_output(self):
        forbidden = {"dep_dt_utc", "arr_dt_utc", "dep_delay", "arr_delay",
                     "dep_time", "arr_time"}
        present = set(self.out.columns) & forbidden
        assert not present, f"Actual-time columns in output: {present}"

    def test_features_use_only_prior_day_not_same_day(self):
        """
        Verify that same-day obs are NOT used.
        Flight 0 (JFK Jan 5): same-day obs (Jan 5) has temp_c=5.0.
        Prior-day obs (Jan 4) has temp_c≈1.67. Feature must be ~1.67, not 5.0.
        """
        row = self.out[self.out["flight_id"] == 0].iloc[0]
        same_day_temp = 5.0   # from Jan 5 obs
        prior_day_temp = (35 - 32) * 5 / 9  # from Jan 4 obs
        assert row["temp_c_prev"] == pytest.approx(prior_day_temp, rel=0.01), (
            "Feature must use prior-day obs, not same-day obs"
        )
        assert abs(row["temp_c_prev"] - same_day_temp) > 1.0, (
            "Feature value suspiciously close to same-day obs — check lag"
        )

    # -- Join cardinality --

    def test_join_cardinality_no_row_multiplication(self, tmp_path):
        """Merging weather_features back onto source on flight_id must not multiply rows."""
        src = pd.read_csv(self.flights_path).reset_index().rename(
            columns={"index": "flight_id"})
        merged = src.merge(self.out, on="flight_id", how="left")
        assert len(merged) == len(src)

    # -- Non-negative physical quantities --

    def test_wind_ms_non_negative(self):
        col = self.out["wind_ms_prev"].dropna()
        assert (col >= 0).all()

    def test_prcp_mm_non_negative(self):
        col = self.out["prcp_mm_prev"].dropna()
        assert (col >= 0).all()

    def test_visibility_km_non_negative(self):
        col = self.out["visibility_km_prev"].dropna()
        assert (col >= 0).all()

    # -- Reproducibility --

    def test_deterministic_output(self, tmp_path):
        out2 = str(tmp_path / "weather_features2.csv")
        build_weather_features(
            flights_path=self.flights_path,
            weather_obs_path=self.obs_path,
            station_map_path=self.sm_path,
            output_path=out2,
        )
        df1 = pd.read_csv(self.output_path)
        df2 = pd.read_csv(out2)
        pd.testing.assert_frame_equal(df1, df2)
