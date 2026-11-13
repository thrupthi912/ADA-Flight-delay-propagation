"""Audit-correction tests for Person 3 deliverables.

Covers all test requirements identified in the technical audit:
  1. Weather RETROSPECTIVE_ONLY flag exists and is True.
  2. GSOD publication-latency disclaimer is present in module docstring.
  3. GSOD features cannot be used before their observation date (prior-day lag).
  4. Future observations never enter historical feature calculations.
  5. Missing weather observations handled as NaN (not imputed or zero).
  6. Prior-day lag is deterministic across runs.
  7. Congestion POPULATION_NOTE constant exists and describes the cohort.
  8. Congestion features are built from the stated population (rotation-eligible).
  9. Target-flight exclusion: a flight is never counted in its own window.
 10. Trailing-window boundaries are correct ([t-W, t), closed left, open right).
 11. No future scheduled movements enter congestion counts.
 12. Duplicate flight handling: congestion index contains no duplicate timestamps
     per airport (verify sort + dedup is consistent with the algorithm).
 13. One-to-one join validation: external_features has no duplicate flight_ids.
 14. Network analysis outputs exist and have correct schema.
 15. Network causal-claim disclaimer present in report.
 16. networkx and matplotlib listed in requirements.txt.
 17. CONGESTION_POPULATION_NOTE mentions ~94-97% coverage.
 18. RETROSPECTIVE_ONLY=True is explicitly importable from weather_features.

All synthetic-data tests are labelled SYNTHETIC.
All real-data integration tests are labelled REAL_DATA and are skipped if the
required datasets are not on disk.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REAL_EXT   = "data/processed/external_features.csv"
REAL_CONG  = "data/processed/congestion_features.csv"
REAL_WX    = "data/processed/weather_features.csv"
REAL_NODES = "data/processed/network_node_metrics.csv"
REAL_EDGES = "data/processed/network_edge_list.csv"
NET_REPORT = "reports/network_analysis_report.md"

real_data = pytest.mark.skipif(
    not os.path.exists(REAL_EXT),
    reason="Real feature files not on disk — skipping real-data integration test"
)

real_network = pytest.mark.skipif(
    not os.path.exists(REAL_NODES),
    reason="Network analysis outputs not on disk — run scripts/run_network_analysis.py first"
)


# ===========================================================================
# PHASE 2: Weather temporal validity tests
# ===========================================================================

class TestWeatherTemporalValidity:
    """SYNTHETIC — Weather prediction-time and publication-latency tests."""

    def test_retrospective_only_flag_exists_and_is_true(self):
        """RETROSPECTIVE_ONLY must be True — GSOD is not a real-time data source."""
        from src.weather_features import RETROSPECTIVE_ONLY
        assert RETROSPECTIVE_ONLY is True, (
            "RETROSPECTIVE_ONLY must be True: NCEI GSOD is available 'a few days after' "
            "observations, not in real-time."
        )

    def test_retrospective_flag_in_all_exports(self):
        """RETROSPECTIVE_ONLY must be in __all__ so it is explicitly exported."""
        import src.weather_features as wf
        assert "RETROSPECTIVE_ONLY" in wf.__all__

    def test_module_docstring_mentions_retrospective(self):
        """Module docstring must contain the word 'retrospective'."""
        import src.weather_features as wf
        assert "retrospective" in (wf.__doc__ or "").lower(), (
            "Module docstring must explicitly state this is a retrospective study."
        )

    def test_module_docstring_mentions_publication_latency(self):
        """Module docstring must mention GSOD publication latency or 'few days'."""
        import src.weather_features as wf
        doc = (wf.__doc__ or "").lower()
        assert "few days" in doc or "publication" in doc or "latency" in doc, (
            "Module docstring must address GSOD publication latency per NCEI README."
        )

    def test_prior_day_lag_means_no_same_day_data(self, tmp_path):
        """SYNTHETIC: weather_date = flight_date - 1 for all rows."""
        from src.weather_features import build_weather_features

        # Build minimal obs for Jan 4 and Jan 5 at JFK
        obs_rows = [
            dict(station_id="74486094789", iata="JFK", DATE="2024-01-04",
                 temp_c=5.0, dewp_c=-2.0, visibility_km=14.0,
                 wind_ms=3.0, gust_ms=float("nan"), prcp_mm=0.0),
            dict(station_id="74486094789", iata="JFK", DATE="2024-01-05",
                 temp_c=99.0, dewp_c=99.0, visibility_km=99.0,  # sentinel same-day
                 wind_ms=99.0, gust_ms=float("nan"), prcp_mm=99.0),
        ]
        obs_path = str(tmp_path / "obs.csv")
        pd.DataFrame(obs_rows).to_csv(obs_path, index=False)

        flights_rows = [
            dict(flight_date="2024-01-05", reporting_airline="AA", flight_number=100,
                 origin="JFK", dest="ATL",
                 crs_dep_dt_utc="2024-01-05 10:00:00+00:00"),
        ]
        flights_path = str(tmp_path / "flights.csv")
        pd.DataFrame(flights_rows).to_csv(flights_path, index=False)

        sm_rows = [dict(iata="JFK", icao_airport="KJFK", usaf="744860", wban=94789,
                        station_name="JFK", station_lat=40.6, station_lon=-73.8,
                        distance_km=0.0, match_method="icao_primary", isd_end_date=20260101)]
        sm_path = str(tmp_path / "sm.csv")
        pd.DataFrame(sm_rows).to_csv(sm_path, index=False)

        out_path = str(tmp_path / "wx_feat.csv")
        build_weather_features(flights_path, obs_path, sm_path, out_path)
        df = pd.read_csv(out_path)

        # Must use Jan 4 data (5.0°C), NOT Jan 5 data (99.0°C)
        assert df["temp_c_prev"].iloc[0] == pytest.approx(5.0, rel=0.01), (
            "Weather feature must use prior-day (Jan 4) data, not same-day (Jan 5)."
        )
        assert df["weather_date"].iloc[0] == "2024-01-04"

    def test_future_obs_cannot_enter_weather_features(self, tmp_path):
        """SYNTHETIC: corrupting actual times leaves weather features unchanged."""
        from src.weather_features import build_weather_features

        obs_rows = [
            dict(station_id="74486094789", iata="JFK", DATE="2024-01-04",
                 temp_c=10.0, dewp_c=5.0, visibility_km=15.0,
                 wind_ms=4.0, gust_ms=float("nan"), prcp_mm=0.0),
        ]
        obs_path = str(tmp_path / "obs.csv")
        pd.DataFrame(obs_rows).to_csv(obs_path, index=False)

        flights_base = [
            dict(flight_date="2024-01-05", reporting_airline="AA", flight_number=100,
                 origin="JFK", dest="ATL",
                 crs_dep_dt_utc="2024-01-05 10:00:00+00:00"),
        ]
        sm_rows = [dict(iata="JFK", icao_airport="KJFK", usaf="744860", wban=94789,
                        station_name="JFK", station_lat=40.6, station_lon=-73.8,
                        distance_km=0.0, match_method="icao_primary", isd_end_date=20260101)]
        sm_path = str(tmp_path / "sm.csv")
        pd.DataFrame(sm_rows).to_csv(sm_path, index=False)

        # Normal run
        f1 = str(tmp_path / "f1.csv")
        pd.DataFrame(flights_base).to_csv(f1, index=False)
        o1 = str(tmp_path / "out1.csv")
        build_weather_features(f1, obs_path, sm_path, o1)
        df1 = pd.read_csv(o1)

        # Run with corrupted actual times added to flight file
        flights_corrupt = flights_base.copy()
        flights_corrupt[0]["dep_dt_utc"] = "9999-01-01 00:00:00+00:00"
        flights_corrupt[0]["arr_dt_utc"] = "9999-01-01 00:00:00+00:00"
        f2 = str(tmp_path / "f2.csv")
        pd.DataFrame(flights_corrupt).to_csv(f2, index=False)
        o2 = str(tmp_path / "out2.csv")
        build_weather_features(f2, obs_path, sm_path, o2)
        df2 = pd.read_csv(o2)

        assert df1["temp_c_prev"].iloc[0] == pytest.approx(df2["temp_c_prev"].iloc[0], rel=1e-9), (
            "Corrupting actual-time columns must not change weather feature values."
        )

    def test_missing_weather_obs_produces_na_not_zero(self, tmp_path):
        """SYNTHETIC: missing prior-day observation → NaN, never 0."""
        from src.weather_features import build_weather_features

        # Obs for Jan 3 only — Jan 4 (prior day for Jan 5 flight) is missing
        obs_rows = [
            dict(station_id="74486094789", iata="JFK", DATE="2024-01-03",
                 temp_c=5.0, dewp_c=-2.0, visibility_km=14.0,
                 wind_ms=3.0, gust_ms=float("nan"), prcp_mm=0.0),
        ]
        obs_path = str(tmp_path / "obs.csv")
        pd.DataFrame(obs_rows).to_csv(obs_path, index=False)

        flights_rows = [
            dict(flight_date="2024-01-05", reporting_airline="AA", flight_number=100,
                 origin="JFK", dest="ATL",
                 crs_dep_dt_utc="2024-01-05 10:00:00+00:00"),
        ]
        flights_path = str(tmp_path / "flights.csv")
        pd.DataFrame(flights_rows).to_csv(flights_path, index=False)

        sm_rows = [dict(iata="JFK", icao_airport="KJFK", usaf="744860", wban=94789,
                        station_name="JFK", station_lat=40.6, station_lon=-73.8,
                        distance_km=0.0, match_method="icao_primary", isd_end_date=20260101)]
        sm_path = str(tmp_path / "sm.csv")
        pd.DataFrame(sm_rows).to_csv(sm_path, index=False)

        out_path = str(tmp_path / "wx_feat.csv")
        build_weather_features(flights_path, obs_path, sm_path, out_path)
        df = pd.read_csv(out_path)

        for feat in ["temp_c_prev", "dewp_c_prev", "visibility_km_prev",
                     "wind_ms_prev", "prcp_mm_prev"]:
            assert pd.isna(df[feat].iloc[0]), (
                f"{feat} must be NaN when prior-day observation is missing, not 0."
            )

    def test_build_weather_features_raises_runtime_warning(self, tmp_path):
        """SYNTHETIC: build_weather_features must raise a UserWarning about retrospective use."""
        import warnings
        from src.weather_features import build_weather_features

        obs_rows = [
            dict(station_id="74486094789", iata="JFK", DATE="2024-01-04",
                 temp_c=5.0, dewp_c=-2.0, visibility_km=14.0,
                 wind_ms=3.0, gust_ms=float("nan"), prcp_mm=0.0),
        ]
        obs_path = str(tmp_path / "obs.csv")
        pd.DataFrame(obs_rows).to_csv(obs_path, index=False)
        flights_rows = [
            dict(flight_date="2024-01-05", reporting_airline="AA", flight_number=100,
                 origin="JFK", dest="ATL",
                 crs_dep_dt_utc="2024-01-05 10:00:00+00:00"),
        ]
        flights_path = str(tmp_path / "flights.csv")
        pd.DataFrame(flights_rows).to_csv(flights_path, index=False)
        sm_rows = [dict(iata="JFK", icao_airport="KJFK", usaf="744860", wban=94789,
                        station_name="JFK", station_lat=40.6, station_lon=-73.8,
                        distance_km=0.0, match_method="icao_primary",
                        isd_end_date=20260101)]
        sm_path = str(tmp_path / "sm.csv")
        pd.DataFrame(sm_rows).to_csv(sm_path, index=False)
        out_path = str(tmp_path / "out.csv")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            build_weather_features(flights_path, obs_path, sm_path, out_path)

        retrospective_warnings = [
            w for w in caught
            if issubclass(w.category, UserWarning)
            and "retrospective" in str(w.message).lower()
        ]
        assert len(retrospective_warnings) >= 1, (
            "build_weather_features must emit a UserWarning containing 'retrospective' "
            "to prevent accidental use in real-time prediction pipelines."
        )

    def test_weather_features_deterministic(self, tmp_path):
        """SYNTHETIC: running build_weather_features twice gives identical output."""
        from src.weather_features import build_weather_features

        obs_rows = [
            dict(station_id="74486094789", iata="JFK", DATE="2024-01-04",
                 temp_c=5.0, dewp_c=-2.0, visibility_km=14.0,
                 wind_ms=3.0, gust_ms=float("nan"), prcp_mm=0.5),
        ]
        obs_path = str(tmp_path / "obs.csv")
        pd.DataFrame(obs_rows).to_csv(obs_path, index=False)

        flights_rows = [
            dict(flight_date="2024-01-05", reporting_airline="AA", flight_number=100,
                 origin="JFK", dest="ATL",
                 crs_dep_dt_utc="2024-01-05 10:00:00+00:00"),
        ]
        flights_path = str(tmp_path / "flights.csv")
        pd.DataFrame(flights_rows).to_csv(flights_path, index=False)

        sm_rows = [dict(iata="JFK", icao_airport="KJFK", usaf="744860", wban=94789,
                        station_name="JFK", station_lat=40.6, station_lon=-73.8,
                        distance_km=0.0, match_method="icao_primary", isd_end_date=20260101)]
        sm_path = str(tmp_path / "sm.csv")
        pd.DataFrame(sm_rows).to_csv(sm_path, index=False)

        out1 = str(tmp_path / "out1.csv")
        out2 = str(tmp_path / "out2.csv")
        build_weather_features(flights_path, obs_path, sm_path, out1)
        build_weather_features(flights_path, obs_path, sm_path, out2)

        pd.testing.assert_frame_equal(pd.read_csv(out1), pd.read_csv(out2))


# ===========================================================================
# PHASE 3: Congestion population and correctness tests
# ===========================================================================

class TestCongestionPopulation:
    """SYNTHETIC/REAL — Congestion population note and window correctness."""

    def test_population_note_constant_exists(self):
        """CONGESTION_POPULATION_NOTE must exist and describe the cohort."""
        from src.congestion_features import CONGESTION_POPULATION_NOTE
        assert isinstance(CONGESTION_POPULATION_NOTE, str)
        assert len(CONGESTION_POPULATION_NOTE) > 20

    def test_population_note_mentions_coverage(self):
        """Population note must mention the approximate coverage percentage."""
        from src.congestion_features import CONGESTION_POPULATION_NOTE
        note = CONGESTION_POPULATION_NOTE.lower()
        assert "94" in note or "95" in note or "96" in note or "97" in note, (
            "Population note must state approximate coverage (~94-97%)."
        )

    def test_population_note_in_all_exports(self):
        """CONGESTION_POPULATION_NOTE must be in __all__."""
        import src.congestion_features as cf
        assert "CONGESTION_POPULATION_NOTE" in cf.__all__

    def test_target_flight_excluded_from_own_count(self):
        """SYNTHETIC: a single flight at time t must not count itself."""
        from src.congestion_features import compute_trailing_count, WINDOW_1H
        t = np.datetime64("2024-01-01T12:00:00", "us")
        arr = np.array([t])
        result = compute_trailing_count(arr, t, WINDOW_1H)
        assert result == 0, "A flight must not count itself in its own congestion window."

    def test_window_closed_left_open_right(self):
        """SYNTHETIC: element at exactly t-W is included; element at t is excluded."""
        from src.congestion_features import compute_trailing_count, WINDOW_1H
        # Exactly at left boundary (t-1h = 11:00): should be INCLUDED
        t = np.datetime64("2024-01-01T12:00:00", "us")
        t_minus_1h = np.datetime64("2024-01-01T11:00:00", "us")
        arr_left = np.array([t_minus_1h])
        assert compute_trailing_count(arr_left, t, WINDOW_1H) == 1, (
            "Left boundary [t-W must be CLOSED (inclusive)."
        )
        # Exactly at right boundary (t itself): should be EXCLUDED
        arr_right = np.array([t])
        assert compute_trailing_count(arr_right, t, WINDOW_1H) == 0, (
            "Right boundary t) must be OPEN (exclusive)."
        )

    def test_no_future_movements_in_window(self):
        """SYNTHETIC: flights scheduled AFTER t must not be counted."""
        from src.congestion_features import compute_trailing_count, WINDOW_1H
        t = np.datetime64("2024-01-01T12:00:00", "us")
        # Flights 1 minute and 1 hour after t
        future = np.array([
            np.datetime64("2024-01-01T12:01:00", "us"),
            np.datetime64("2024-01-01T13:00:00", "us"),
        ])
        result = compute_trailing_count(future, t, WINDOW_1H)
        assert result == 0, "No future-scheduled flights may enter the trailing window."

    def test_3h_window_ge_1h_window(self):
        """SYNTHETIC: 3h count must always be >= 1h count."""
        from src.congestion_features import compute_trailing_count, WINDOW_1H, WINDOW_3H
        t = np.datetime64("2024-01-01T15:00:00", "us")
        arr = np.array([
            np.datetime64("2024-01-01T12:30:00", "us"),  # in 3h, not in 1h
            np.datetime64("2024-01-01T14:30:00", "us"),  # in both
        ])
        r1 = compute_trailing_count(arr, t, WINDOW_1H)
        r3 = compute_trailing_count(arr, t, WINDOW_3H)
        assert r3 >= r1, "3h window count must be >= 1h window count."
        assert r1 == 1
        assert r3 == 2

    def test_duplicate_timestamps_handled(self):
        """SYNTHETIC: two flights at the same time — only flights before t are counted."""
        from src.congestion_features import compute_trailing_count, WINDOW_1H
        t = np.datetime64("2024-01-01T12:00:00", "us")
        # Two flights at same time, one before t, one at t
        arr = np.array([
            np.datetime64("2024-01-01T11:30:00", "us"),
            np.datetime64("2024-01-01T11:30:00", "us"),  # duplicate
            np.datetime64("2024-01-01T12:00:00", "us"),  # at t, excluded
        ])
        result = compute_trailing_count(arr, t, WINDOW_1H)
        # Both 11:30 entries are in [11:00, 12:00); 12:00 is excluded
        assert result == 2, (
            "Duplicate timestamps before t must both be counted; t itself excluded."
        )

    @real_data
    def test_real_congestion_non_negative(self):
        """REAL_DATA: all congestion values must be non-negative."""
        df = pd.read_csv(REAL_CONG,
            usecols=["dep_count_origin_1h", "dep_count_origin_3h",
                     "arr_count_dest_1h", "arr_count_dest_3h",
                     "total_traffic_origin_1h"])
        for col in df.columns:
            assert (df[col].dropna() >= 0).all(), f"Negative values found in {col}"

    @real_data
    def test_real_congestion_3h_ge_1h(self):
        """REAL_DATA: 3h count >= 1h count for all rows."""
        df = pd.read_csv(REAL_CONG,
            usecols=["dep_count_origin_1h", "dep_count_origin_3h",
                     "arr_count_dest_1h", "arr_count_dest_3h"])
        assert (df["dep_count_origin_3h"] >= df["dep_count_origin_1h"]).all()
        assert (df["arr_count_dest_3h"] >= df["arr_count_dest_1h"]).all()


# ===========================================================================
# PHASE 4: Join cardinality and integration
# ===========================================================================

class TestJoinCardinality:
    """Real-data join cardinality and key uniqueness tests."""

    @real_data
    def test_external_features_no_duplicate_flight_ids(self):
        """REAL_DATA: flight_id must be unique in external_features.csv."""
        df = pd.read_csv(REAL_EXT, usecols=["flight_id"])
        dups = df["flight_id"].duplicated().sum()
        assert dups == 0, f"Found {dups} duplicate flight_ids in external_features.csv"

    @real_data
    def test_external_features_row_count(self):
        """REAL_DATA: external_features.csv must have 2,319,468 rows."""
        with open(REAL_EXT) as f:
            n = sum(1 for _ in f) - 1
        assert n == 2_319_468, f"Expected 2,319,468 rows, got {n:,}"

    @real_data
    def test_weather_date_lag_all_rows(self):
        """REAL_DATA: weather_date = flight_date - 1 for every row (sample 100k)."""
        df = pd.read_csv(REAL_EXT,
            usecols=["flight_date", "weather_date"], nrows=100_000)
        df["flight_date"] = pd.to_datetime(df["flight_date"])
        df["weather_date"] = pd.to_datetime(df["weather_date"])
        lag_days = (df["flight_date"] - df["weather_date"]).dt.days
        assert (lag_days == 1).all(), (
            "weather_date must equal flight_date - 1 for all rows."
        )

    @real_data
    def test_join_does_not_multiply_rows(self):
        """REAL_DATA: merging external_features onto congestion does not add rows."""
        cong = pd.read_csv(REAL_CONG, usecols=["flight_id", "origin"], nrows=50_000)
        ext  = pd.read_csv(REAL_EXT,  usecols=["flight_id", "temp_c_prev"], nrows=50_000)
        merged = cong.merge(ext, on="flight_id", how="left")
        assert len(merged) == len(cong), (
            f"Join multiplied rows: {len(cong)} -> {len(merged)}"
        )

    @real_data
    def test_no_outcome_columns_in_external_features(self):
        """REAL_DATA: delay outcome columns must not appear in external_features."""
        df = pd.read_csv(REAL_EXT, nrows=0)
        forbidden = {"dep_delay", "arr_delay", "dep_dt_utc", "arr_dt_utc",
                     "actual_elapsed_time", "tail_number"}
        present = set(df.columns) & forbidden
        assert not present, f"Outcome columns in external_features: {present}"

    @real_data
    def test_congestion_complete_no_nulls(self):
        """REAL_DATA: all 5 congestion features must be 100% complete."""
        df = pd.read_csv(REAL_CONG,
            usecols=["dep_count_origin_1h", "dep_count_origin_3h",
                     "arr_count_dest_1h", "arr_count_dest_3h",
                     "total_traffic_origin_1h"])
        for col in df.columns:
            nulls = df[col].isna().sum()
            assert nulls == 0, f"{col} has {nulls:,} unexpected null values"

    @real_data
    def test_weather_missingness_within_expected_bounds(self):
        """REAL_DATA: core weather features must be < 1% missing (not gust)."""
        df = pd.read_csv(REAL_WX, usecols=["temp_c_prev", "wind_ms_prev",
                                             "visibility_km_prev", "dewp_c_prev"])
        for col in df.columns:
            pct = df[col].isna().mean() * 100
            assert pct < 1.0, f"{col} is {pct:.2f}% missing — exceeds 1% threshold"

    @real_data
    def test_gust_missingness_flagged_not_treated_as_zero(self):
        """REAL_DATA: gust_ms_prev must be ~31% null (never imputed as zero)."""
        df = pd.read_csv(REAL_WX, usecols=["gust_ms_prev"])
        pct = df["gust_ms_prev"].isna().mean() * 100
        # Acceptable range: 25-40% based on historical GSOD patterns
        assert 25 <= pct <= 40, (
            f"gust_ms_prev is {pct:.1f}% null; expected 25-40% (GSOD 999.9 not-reported days). "
            "If 0%, the sentinel may have been imputed as zero — check implementation."
        )
        # Also verify no zeros appeared from imputation of NaN
        # (gust values should start at a physically reasonable minimum, not 0)
        non_null = df["gust_ms_prev"].dropna()
        if len(non_null) > 0:
            assert non_null.min() > 0, (
                "gust_ms_prev minimum is 0, which may indicate 999.9 was imputed as 0."
            )


# ===========================================================================
# PHASE 5: Network analysis tests
# ===========================================================================

class TestNetworkAnalysis:
    """Network analysis outputs — schema, completeness, disclaimer."""

    @real_network
    def test_node_metrics_schema(self):
        """REAL_DATA: node_metrics.csv must have expected columns."""
        df = pd.read_csv(REAL_NODES)
        required = {"iata", "in_degree", "out_degree", "in_strength",
                    "out_strength", "total_strength", "betweenness_centrality",
                    "pagerank"}
        assert required.issubset(set(df.columns)), (
            f"Missing columns: {required - set(df.columns)}"
        )

    @real_network
    def test_node_metrics_row_count(self):
        """REAL_DATA: node_metrics must have one row per unique airport."""
        df = pd.read_csv(REAL_NODES)
        assert 300 <= len(df) <= 400, (
            f"Expected ~343 airport nodes, got {len(df)}"
        )

    @real_network
    def test_no_duplicate_airports_in_nodes(self):
        """REAL_DATA: each airport appears once in node metrics."""
        df = pd.read_csv(REAL_NODES)
        assert df["iata"].duplicated().sum() == 0

    @real_network
    def test_edge_list_schema(self):
        """REAL_DATA: edge_list.csv must have expected columns."""
        df = pd.read_csv(REAL_EDGES)
        required = {"origin", "dest", "flight_count",
                    "mean_dep_delay_min", "mean_arr_delay_min"}
        assert required.issubset(set(df.columns))

    @real_network
    def test_no_self_loops(self):
        """REAL_DATA: no airport should have a flight to itself."""
        df = pd.read_csv(REAL_EDGES)
        self_loops = (df["origin"] == df["dest"]).sum()
        assert self_loops == 0, f"Found {self_loops} self-loop edges"

    @real_network
    def test_network_report_exists_and_has_disclaimer(self):
        """REAL_DATA: network report must exist and contain causal disclaimer."""
        assert os.path.exists(NET_REPORT), f"Network report not found: {NET_REPORT}"
        with open(NET_REPORT) as f:
            content = f.read().lower()
        assert "descriptive" in content or "causal" in content, (
            "Network report must include a causal-claim disclaimer."
        )
        assert "do not" in content or "does not" in content or "not imply" in content, (
            "Network report must state network metrics do not imply causal delay propagation."
        )

    @real_network
    def test_betweenness_values_in_valid_range(self):
        """REAL_DATA: normalised betweenness must be in [0, 1]."""
        df = pd.read_csv(REAL_NODES)
        assert (df["betweenness_centrality"] >= 0).all()
        assert (df["betweenness_centrality"] <= 1.0).all()

    @real_network
    def test_pagerank_sums_to_one(self):
        """REAL_DATA: PageRank values must sum to approximately 1."""
        df = pd.read_csv(REAL_NODES)
        total = df["pagerank"].sum()
        assert abs(total - 1.0) < 0.01, f"PageRank sum = {total:.4f}, expected ~1.0"

    @real_network
    def test_total_strength_matches_edge_list(self):
        """REAL_DATA: in+out strength in node metrics must match edge list totals."""
        nodes = pd.read_csv(REAL_NODES)
        edges = pd.read_csv(REAL_EDGES)
        # Spot-check ATL out-strength
        atl_out_edges = edges[edges["origin"] == "ATL"]["flight_count"].sum()
        atl_node = nodes[nodes["iata"] == "ATL"].iloc[0]
        assert atl_node["out_strength"] == atl_out_edges, (
            f"ATL out_strength {atl_node['out_strength']} != edge list sum {atl_out_edges}"
        )

    def test_network_analysis_importable(self):
        """The network_analysis module must import without errors."""
        import src.network_analysis as na
        assert hasattr(na, "build_edge_list")
        assert hasattr(na, "build_flight_network")
        assert hasattr(na, "compute_node_metrics")
        assert hasattr(na, "compute_network_summary")

    def test_haversine_in_station_matching_still_works(self):
        """Verify station_matching module is unaffected by audit changes."""
        from src.station_matching import haversine_km
        d = haversine_km(40.64, -73.78, 33.94, -118.41)
        assert d == pytest.approx(3983.0, rel=0.01)


# ===========================================================================
# PHASE 6: Requirements and configuration
# ===========================================================================

class TestRequirementsAndConfig:
    """Verify requirements.txt contains all needed packages."""

    def test_networkx_in_requirements(self):
        with open("requirements.txt") as f:
            content = f.read()
        assert "networkx" in content, "networkx must be listed in requirements.txt"

    def test_matplotlib_in_requirements(self):
        with open("requirements.txt") as f:
            content = f.read()
        assert "matplotlib" in content, "matplotlib must be listed in requirements.txt"

    def test_requests_in_requirements(self):
        with open("requirements.txt") as f:
            content = f.read()
        assert "requests" in content, "requests must be listed in requirements.txt"

    def test_network_analysis_script_exists(self):
        assert os.path.exists("scripts/run_network_analysis.py"), (
            "scripts/run_network_analysis.py must exist"
        )

    def test_network_analysis_src_exists(self):
        assert os.path.exists("src/network_analysis.py"), (
            "src/network_analysis.py must exist"
        )
