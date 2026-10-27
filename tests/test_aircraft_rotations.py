import pandas as pd
import pytest

from src.aircraft_rotations import (
    build_rotation_pairs,
    generate_rotation_report,
)


def make_flights():
    """Create three consecutive flights for one aircraft."""
    return pd.DataFrame(
        {
            "tail_number": ["N123AB", "N123AB", "N123AB"],
            "rotation_eligible": [True, True, True],
            "crs_dep_dt_utc": [
                "2024-01-05 14:00:00+00:00",
                "2024-01-05 17:00:00+00:00",
                "2024-01-05 20:00:00+00:00",
            ],
            "dep_dt_utc": [
                "2024-01-05 14:05:00+00:00",
                "2024-01-05 17:10:00+00:00",
                "2024-01-05 20:05:00+00:00",
            ],
            "crs_arr_dt_utc": [
                "2024-01-05 16:00:00+00:00",
                "2024-01-05 19:00:00+00:00",
                "2024-01-05 22:00:00+00:00",
            ],
            "arr_dt_utc": [
                "2024-01-05 16:05:00+00:00",
                "2024-01-05 19:10:00+00:00",
                "2024-01-05 22:05:00+00:00",
            ],
            "origin": ["JFK", "ATL", "JFK"],
            "dest": ["ATL", "JFK", "ATL"],
        }
    )


def test_builds_consecutive_flight_pairs():
    pairs = build_rotation_pairs(make_flights())

    assert len(pairs) == 2
    assert pairs["connection_valid"].all()


def test_calculates_scheduled_ground_time():
    pairs = build_rotation_pairs(make_flights())

    assert pairs["scheduled_ground_time_min"].tolist() == [
        60.0,
        60.0,
    ]


def test_calculates_turnaround_slack():
    pairs = build_rotation_pairs(make_flights())

    assert pairs["turnaround_slack_min"].tolist() == [
        55.0,
        50.0,
    ]


def test_negative_slack_is_not_automatically_invalid():
    flights = make_flights()

    # The first aircraft arrives 20 minutes after the next flight's
    # scheduled departure, but the next flight actually departs later.
    flights.loc[0, "arr_dt_utc"] = "2024-01-05 17:20:00+00:00"
    flights.loc[1, "dep_dt_utc"] = "2024-01-05 17:30:00+00:00"

    pairs = build_rotation_pairs(flights)

    assert pairs.loc[0, "turnaround_slack_min"] == -20.0
    assert bool(pairs.loc[0, "connection_valid"]) is True
    assert pairs.loc[0, "exclusion_reason"] == ""


def test_ineligible_flight_is_flagged():
    flights = make_flights()
    flights.loc[1, "rotation_eligible"] = False

    pairs = build_rotation_pairs(flights)

    assert not pairs.loc[0, "connection_valid"]
    assert "next_flight_ineligible" in pairs.loc[0, "exclusion_reason"]
    assert not pairs.loc[1, "connection_valid"]
    assert "incoming_flight_ineligible" in pairs.loc[1, "exclusion_reason"]


def test_missing_required_column_raises():
    flights = make_flights().drop(columns=["tail_number"])

    with pytest.raises(ValueError, match="tail_number"):
        build_rotation_pairs(flights)


def test_empty_input_returns_empty_output():
    pairs = build_rotation_pairs(make_flights().iloc[0:0])

    assert pairs.empty


def test_report_counts_connections():
    pairs = build_rotation_pairs(make_flights())
    report = generate_rotation_report(pairs)

    assert report == {
        "candidate_connections": 2,
        "valid_connections": 2,
        "invalid_connections": 0,
    }