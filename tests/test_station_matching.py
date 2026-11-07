"""Tests for the NOAA station matching module (src.station_matching).

All tests use synthetic data — no network requests are made.

Coverage
--------
* ``haversine_km`` — scalar and vectorised, known-distance pairs,
  symmetry, same-point zero, antipodal maximum.
* ``build_station_map`` — end-to-end on a synthetic fixture:
  - ICAO primary match (Tier 1).
  - Extended ICAO match (Tier 2) for territory airports.
  - Geographic fallback (Tier 3).
  - Unmatched airport recorded with match_method='none'.
  - No duplicate IATA codes in output.
  - Every airport appears exactly once.
  - distance_km=0.0 for ICAO matches.
  - distance_km>0 for geographic matches.
  - match_method values are one of the allowed set.
  - Stations with END < 20240101 are excluded.
  - Stations with zero coordinates are excluded.
  - Output has expected columns.
  - Reproducibility (deterministic output for same input).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src.station_matching import (
    ISD_ACTIVE_CUTOFF,
    TERRITORY_SUPPLEMENT,
    XWA_COORDS,
    build_station_map,
    haversine_km,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = {
    "iata", "icao_airport", "usaf", "wban", "station_name",
    "station_lat", "station_lon", "distance_km", "match_method", "isd_end_date",
}

ALLOWED_METHODS = {"icao_primary", "icao_extended", "geographic", "none"}


def _make_airport_tz(tmp_path, rows) -> str:
    """Write a synthetic airport_timezones.csv and return its path."""
    df = pd.DataFrame(rows)
    p = str(tmp_path / "airport_timezones.csv")
    df.to_csv(p, index=False)
    return p


def _make_isd_catalog(tmp_path, rows) -> str:
    """Write a synthetic isd-history.csv and return its path."""
    df = pd.DataFrame(rows)
    p = str(tmp_path / "isd-history.csv")
    df.to_csv(p, index=False)
    return p


def _make_flights(tmp_path, origins, dests) -> str:
    """Write a minimal flights CSV containing the given airports."""
    df = pd.DataFrame({
        "origin": origins,
        "dest": dests,
        "crs_dep_dt_utc": ["2024-01-01 10:00:00+00:00"] * len(origins),
        "crs_arr_dt_utc": ["2024-01-01 12:00:00+00:00"] * len(origins),
    })
    p = str(tmp_path / "flights.csv")
    df.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------

class TestHaversineKm:
    """Unit tests for the Haversine distance calculation."""

    def test_same_point_is_zero(self):
        d = haversine_km(40.0, -74.0, 40.0, -74.0)
        assert d == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_jfk_to_lax(self):
        # JFK: 40.6413, -73.7781  /  LAX: 33.9425, -118.4081
        # Great-circle ~3,983 km (actual flight distance ~3,970 km)
        d = haversine_km(40.6413, -73.7781, 33.9425, -118.4081)
        assert d == pytest.approx(3983.0, rel=0.01)

    def test_symmetry(self):
        d1 = haversine_km(40.0, -74.0, 34.0, -118.0)
        d2 = haversine_km(34.0, -118.0, 40.0, -74.0)
        assert d1 == pytest.approx(d2, rel=1e-9)

    def test_vectorised_matches_scalar(self):
        lat2 = np.array([33.94, 41.98])
        lon2 = np.array([-118.41, -87.91])
        scalar_0 = haversine_km(40.64, -73.78, lat2[0], lon2[0])
        scalar_1 = haversine_km(40.64, -73.78, lat2[1], lon2[1])
        vec = haversine_km(40.64, -73.78, lat2, lon2)
        assert vec[0] == pytest.approx(scalar_0, rel=1e-9)
        assert vec[1] == pytest.approx(scalar_1, rel=1e-9)

    def test_small_distance_accuracy(self):
        # Two points ~1 km apart (roughly 0.009 degrees latitude)
        d = haversine_km(51.000, 0.000, 51.009, 0.000)
        assert d == pytest.approx(1.0, abs=0.02)

    def test_positive_output(self):
        d = haversine_km(0.0, 0.0, 1.0, 1.0)
        assert d > 0


# ---------------------------------------------------------------------------
# build_station_map — end-to-end
# ---------------------------------------------------------------------------

def _synthetic_isd_rows():
    """Minimal ISD catalog with stations covering test scenarios."""
    return [
        # KJFK — JFK, active in 2024, good coords
        dict(USAF="744860", WBAN=94789, **{"STATION NAME": "JFK INTL"},
             CTRY="US", STATE="NY", ICAO="KJFK",
             LAT=40.639, LON=-73.778, **{"ELEV(M)": 4.0},
             BEGIN=19480101, END=20260101),
        # KATL — ATL, active in 2024
        dict(USAF="722190", WBAN=13874, **{"STATION NAME": "ATLANTA HARTSFIELD"},
             CTRY="US", STATE="GA", ICAO="KATL",
             LAT=33.630, LON=-84.442, **{"ELEV(M)": 308.0},
             BEGIN=19500101, END=20260101),
        # KBOS — BOS, active in 2024
        dict(USAF="725090", WBAN=14739, **{"STATION NAME": "BOSTON LOGAN"},
             CTRY="US", STATE="MA", ICAO="KBOS",
             LAT=42.361, LON=-71.010, **{"ELEV(M)": 9.0},
             BEGIN=19450101, END=20260101),
        # TJSJ — SJU territory airport, active in 2024
        dict(USAF="785260", WBAN=11641, **{"STATION NAME": "LUIS MUNOZ MARIN"},
             CTRY="US", STATE="PR", ICAO="TJSJ",
             LAT=18.433, LON=-66.011, **{"ELEV(M)": 9.0},
             BEGIN=19540101, END=20260101),
        # Nearby station for geographic fallback (XWA area, ND)
        dict(USAF="720868", WBAN=294, **{"STATION NAME": "WATFORD CITY MUNI"},
             CTRY="US", STATE="ND", ICAO="KS25",
             LAT=47.796, LON=-103.254, **{"ELEV(M)": 699.0},
             BEGIN=20101210, END=20260101),
        # OLD inactive station — should be excluded
        dict(USAF="999999", WBAN=99999, **{"STATION NAME": "OLD STATION"},
             CTRY="US", STATE="CA", ICAO="KOLD",
             LAT=34.0, LON=-118.0, **{"ELEV(M)": 0.0},
             BEGIN=19500101, END=20000101),
        # Station with zero coordinates — should be excluded
        dict(USAF="000001", WBAN=1, **{"STATION NAME": "ZERO COORDS"},
             CTRY="US", STATE="TX", ICAO="KZRO",
             LAT=0.0, LON=0.0, **{"ELEV(M)": 0.0},
             BEGIN=19500101, END=20260101),
    ]


class TestBuildStationMap:
    """End-to-end tests for build_station_map using synthetic fixtures."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.tmp = tmp_path

        # Airport timezone CSV: JFK, ATL, BOS have ICAO; XNA does not match ISD
        tz_rows = [
            dict(iata="JFK", icao="KJFK", name="JFK", municipality="New York",
                 region="US-NY", timezone="America/New_York"),
            dict(iata="ATL", icao="KATL", name="ATL", municipality="Atlanta",
                 region="US-GA", timezone="America/New_York"),
            dict(iata="BOS", icao="KBOS", name="BOS", municipality="Boston",
                 region="US-MA", timezone="America/New_York"),
            # XWA — in timezone CSV with KXWA, but KXWA not in ISD -> geographic fallback
            dict(iata="XWA", icao="KXWA", name="Williston Basin",
                 municipality="Williston", region="US-ND",
                 timezone="America/Chicago"),
        ]
        self.tz_path = _make_airport_tz(tmp_path, tz_rows)
        self.isd_path = _make_isd_catalog(tmp_path, _synthetic_isd_rows())

        # Flights: origin/dest cover JFK, ATL, BOS, XWA, SJU (territory)
        self.flights_path = _make_flights(
            tmp_path,
            origins=["JFK", "ATL", "BOS", "XWA", "SJU"],
            dests=["ATL", "JFK", "JFK", "ATL", "JFK"],
        )
        self.output_path = str(tmp_path / "airport_station_map.csv")

        build_station_map(
            airport_tz_path=self.tz_path,
            isd_catalog_path=self.isd_path,
            flights_path=self.flights_path,
            output_path=self.output_path,
        )
        self.out = pd.read_csv(self.output_path)

    # -- Basic structure --

    def test_output_has_expected_columns(self):
        assert EXPECTED_COLUMNS.issubset(set(self.out.columns))

    def test_every_airport_appears_exactly_once(self):
        # All 5 unique airports: JFK, ATL, BOS, XWA, SJU
        assert set(self.out["iata"]) == {"JFK", "ATL", "BOS", "XWA", "SJU"}

    def test_no_duplicate_iata(self):
        dups = self.out["iata"].duplicated().sum()
        assert dups == 0, f"Duplicate IATA codes found: {dups}"

    def test_all_match_methods_valid(self):
        invalid = set(self.out["match_method"]) - ALLOWED_METHODS
        assert not invalid, f"Invalid match_method values: {invalid}"

    # -- ICAO primary match (Tier 1) --

    def test_jfk_matched_by_icao_primary(self):
        row = self.out[self.out["iata"] == "JFK"].iloc[0]
        assert row["match_method"] == "icao_primary"
        assert str(row["usaf"]) == "744860"
        assert row["icao_airport"] == "KJFK"

    def test_atl_matched_by_icao_primary(self):
        row = self.out[self.out["iata"] == "ATL"].iloc[0]
        assert row["match_method"] == "icao_primary"
        assert str(row["usaf"]) == "722190"

    def test_bos_matched_by_icao_primary(self):
        row = self.out[self.out["iata"] == "BOS"].iloc[0]
        assert row["match_method"] == "icao_primary"
        assert str(row["usaf"]) == "725090"

    def test_icao_primary_distance_is_zero(self):
        icao_rows = self.out[self.out["match_method"] == "icao_primary"]
        assert (icao_rows["distance_km"] == 0.0).all(), (
            "ICAO-matched stations must have distance_km=0"
        )

    # -- Extended ICAO match (Tier 2) --

    def test_sju_matched_by_icao_extended(self):
        row = self.out[self.out["iata"] == "SJU"].iloc[0]
        assert row["match_method"] == "icao_extended"
        assert str(row["usaf"]) == "785260"
        assert row["icao_airport"] == "TJSJ"

    def test_icao_extended_distance_is_zero(self):
        ext_rows = self.out[self.out["match_method"] == "icao_extended"]
        assert (ext_rows["distance_km"] == 0.0).all()

    # -- Geographic fallback (Tier 3) --

    def test_xwa_matched_by_geographic(self):
        row = self.out[self.out["iata"] == "XWA"].iloc[0]
        assert row["match_method"] == "geographic"

    def test_xwa_distance_positive(self):
        row = self.out[self.out["iata"] == "XWA"].iloc[0]
        assert row["distance_km"] > 0

    def test_xwa_distance_within_threshold(self):
        row = self.out[self.out["iata"] == "XWA"].iloc[0]
        from src.station_matching import MAX_FALLBACK_KM
        assert row["distance_km"] <= MAX_FALLBACK_KM

    def test_xwa_nearest_station_selected(self):
        """The geographic fallback must select the nearest station."""
        row = self.out[self.out["iata"] == "XWA"].iloc[0]
        # Watford City (KS25) is the only ND station in our synthetic catalog.
        assert str(row["usaf"]) == "720868"

    # -- Station quality checks --

    def test_inactive_stations_excluded(self):
        """OLD STATION with END=20000101 must never appear in output."""
        assert "999999" not in self.out["usaf"].values, (
            "Inactive station (END < 20240101) must not be selected"
        )

    def test_zero_coordinate_stations_excluded(self):
        """Stations with LAT=LON=0 must not appear in output."""
        assert "000001" not in self.out["usaf"].values, (
            "Station with zero coordinates must not be selected"
        )

    # -- All matched stations have END >= cutoff --

    def test_matched_stations_end_date_valid(self):
        matched = self.out[self.out["match_method"] != "none"]
        bad = matched[matched["isd_end_date"].astype(float) < ISD_ACTIVE_CUTOFF]
        assert len(bad) == 0, f"Stations with END < {ISD_ACTIVE_CUTOFF}: {bad['iata'].tolist()}"

    # -- Coordinate check --

    def test_matched_airports_have_station_coords(self):
        matched = self.out[self.out["match_method"] != "none"]
        assert matched["station_lat"].notna().all()
        assert matched["station_lon"].notna().all()

    # -- Reproducibility --

    def test_deterministic_output(self, tmp_path):
        """Running build_station_map twice on the same input produces identical output."""
        out2 = str(tmp_path / "map2.csv")
        build_station_map(
            airport_tz_path=self.tz_path,
            isd_catalog_path=self.isd_path,
            flights_path=self.flights_path,
            output_path=out2,
        )
        df1 = pd.read_csv(self.output_path)
        df2 = pd.read_csv(out2)
        pd.testing.assert_frame_equal(
            df1.sort_values("iata").reset_index(drop=True),
            df2.sort_values("iata").reset_index(drop=True),
        )


# ---------------------------------------------------------------------------
# TERRITORY_SUPPLEMENT and XWA_COORDS constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_territory_supplement_all_icao_four_chars(self):
        for iata, icao in TERRITORY_SUPPLEMENT.items():
            assert len(icao) == 4, f"{iata}: ICAO {icao!r} is not 4 characters"

    def test_xwa_coords_in_north_dakota(self):
        lat, lon = XWA_COORDS
        # Williston, ND is ~48.1°N, 103.6°W
        assert 47.0 <= lat <= 49.5, f"XWA latitude {lat} outside ND range"
        assert -105.0 <= lon <= -102.0, f"XWA longitude {lon} outside ND range"

    def test_territory_iata_codes_known(self):
        expected = {"BQN", "GUM", "PBI", "PPG", "PSE", "SJU", "STT", "STX"}
        assert set(TERRITORY_SUPPLEMENT.keys()) == expected
