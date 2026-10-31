import json

import numpy as np
import pandas as pd
import pytest

from src.aircraft_rotations import build_rotation_pairs
from src.train_delay_model import (
    FEATURE_SETS,
    TARGET,
    TIME_COLUMN,
    assert_no_leakage,
    baseline_predictions,
    build_features,
    chronological_split,
    drop_incomplete,
    encode_categories,
    evaluate,
    exclude_discontinuous,
    hhmm_to_minutes,
    join_pairs_with_flights,
    load_flights_subset,
    mean_absolute_error,
    root_mean_squared_error,
    run,
    select_valid_pairs,
)


def _iso(timestamp):
    return timestamp.isoformat(sep=" ")


def make_flights_frame(records):
    """Build a cleaned-flights frame from compact flight records.

    Each record is (tail, origin, dest, crs_dep, scheduled_minutes,
    arr_delay, airline). The departure delay equals the arrival delay.
    """
    rows = []
    for tail, origin, dest, crs_dep, minutes, delay, airline in records:
        crs_dep = pd.Timestamp(crs_dep, tz="UTC")
        crs_arr = crs_dep + pd.Timedelta(minutes=minutes)
        dep = crs_dep + pd.Timedelta(minutes=delay)
        arr = crs_arr + pd.Timedelta(minutes=delay)
        rows.append(
            {
                "tail_number": tail,
                "rotation_eligible": True,
                "origin": origin,
                "dest": dest,
                "crs_dep_dt_utc": _iso(crs_dep),
                "dep_dt_utc": _iso(dep),
                "crs_arr_dt_utc": _iso(crs_arr),
                "arr_dt_utc": _iso(arr),
                "crs_dep_time": crs_dep.hour * 100 + crs_dep.minute,
                "crs_arr_time": crs_arr.hour * 100 + crs_arr.minute,
                "reporting_airline": airline,
                "dep_delay": float(delay),
                "arr_delay": float(delay),
            }
        )
    return pd.DataFrame(rows)


def make_shuffled_flights():
    """Five flights whose file order differs from chronological order.

    Positions: 0 = N2 second leg, 1 = N1 second leg, 2 = N1 third leg,
    3 = N2 first leg, 4 = N1 first leg.
    """
    return make_flights_frame(
        [
            ("N2", "DEN", "ORD", "2024-01-05 19:00", 120, 30, "UA"),
            ("N1", "ATL", "JFK", "2024-01-05 17:00", 120, 15, "DL"),
            ("N1", "JFK", "ATL", "2024-01-05 20:00", 120, 0, "DL"),
            ("N2", "ORD", "DEN", "2024-01-05 15:00", 120, 20, "UA"),
            ("N1", "JFK", "ATL", "2024-01-05 14:00", 120, 5, "DL"),
        ]
    )


def flights_index(flights):
    indexed = flights.copy()
    indexed["crs_dep_dt_utc"] = pd.to_datetime(
        indexed["crs_dep_dt_utc"], utc=True
    )
    indexed.index = pd.Index(range(len(indexed)), name="row_id")
    return indexed


def make_valid_pairs(flights):
    pairs = build_rotation_pairs(flights)
    return select_valid_pairs(pairs)


# ---------------------------------------------------------------------------
# Row-ID joins
# ---------------------------------------------------------------------------
def test_join_uses_row_ids_not_sorted_position():
    flights = make_shuffled_flights()
    pairs = make_valid_pairs(flights)

    joined = join_pairs_with_flights(pairs, flights_index(flights))

    observed = {
        (int(r.incoming_row_id), int(r.next_row_id)): (
            r.incoming_arr_delay,
            r[TARGET],
        )
        for _, r in joined.iterrows()
    }

    # (incoming position, next position) -> (incoming delay, next delay)
    assert observed == {
        (4, 1): (5.0, 15.0),
        (1, 2): (15.0, 0.0),
        (3, 0): (20.0, 30.0),
    }


def test_join_attaches_next_flight_schedule_fields():
    flights = make_shuffled_flights()
    pairs = make_valid_pairs(flights)

    joined = join_pairs_with_flights(pairs, flights_index(flights))
    row = joined.loc[joined["next_row_id"] == 1].iloc[0]

    assert row["next_reporting_airline"] == "DL"
    assert row["next_crs_dep_time"] == 1700
    assert row["next_crs_arr_time"] == 1900
    assert row[TIME_COLUMN] == pd.Timestamp("2024-01-05 17:00", tz="UTC")


def test_join_rejects_tail_mismatch():
    flights = make_shuffled_flights()
    pairs = make_valid_pairs(flights)
    pairs.loc[0, "tail_number"] = "N999"

    with pytest.raises(ValueError, match="tail_number"):
        join_pairs_with_flights(pairs, flights_index(flights))


def test_join_rejects_wrong_airports():
    flights = make_shuffled_flights()
    pairs = make_valid_pairs(flights)
    pairs.loc[0, "next_dest"] = "LAX"

    with pytest.raises(ValueError, match="origin/dest"):
        join_pairs_with_flights(pairs, flights_index(flights))


def test_join_rejects_reversed_chronology():
    flights = make_shuffled_flights()
    pairs = make_valid_pairs(flights)

    # Swap incoming/next consistently so only the time order is wrong.
    reversed_pairs = pairs.copy()
    for a, b in (
        ("incoming_row_id", "next_row_id"),
        ("incoming_origin", "next_origin"),
        ("incoming_dest", "next_dest"),
    ):
        reversed_pairs[a], reversed_pairs[b] = pairs[b], pairs[a]

    with pytest.raises(ValueError, match="scheduled before"):
        join_pairs_with_flights(reversed_pairs, flights_index(flights))


def test_join_rejects_ineligible_flight():
    flights = make_shuffled_flights()
    pairs = make_valid_pairs(flights)
    indexed = flights_index(flights)
    indexed.loc[1, "rotation_eligible"] = False

    with pytest.raises(ValueError, match="rotation_eligible"):
        join_pairs_with_flights(pairs, indexed)


def test_join_rejects_ids_missing_from_flights():
    flights = make_shuffled_flights()
    pairs = make_valid_pairs(flights)
    indexed = flights_index(flights).drop(index=1)

    with pytest.raises(ValueError, match="not found"):
        join_pairs_with_flights(pairs, indexed)


def test_select_valid_pairs_excludes_invalid_connections():
    pairs = pd.DataFrame(
        {
            "incoming_row_id": [0, 1, 2],
            "next_row_id": [1, 2, 3],
            "connection_valid": [True, "False", "true"],
        }
    )

    kept = select_valid_pairs(pairs)

    assert kept["incoming_row_id"].tolist() == [0, 2]


def test_select_valid_pairs_rejects_duplicate_next_ids():
    pairs = pd.DataFrame(
        {
            "incoming_row_id": [0, 1],
            "next_row_id": [5, 5],
            "connection_valid": [True, True],
        }
    )

    with pytest.raises(ValueError, match="Duplicate next_row_id"):
        select_valid_pairs(pairs)


def test_select_valid_pairs_rejects_self_link_and_missing_ids():
    self_link = pd.DataFrame(
        {
            "incoming_row_id": [3],
            "next_row_id": [3],
            "connection_valid": [True],
        }
    )
    with pytest.raises(ValueError, match="itself"):
        select_valid_pairs(self_link)

    missing = pd.DataFrame(
        {
            "incoming_row_id": [1.0, np.nan],
            "next_row_id": [2.0, 3.0],
            "connection_valid": [True, True],
        }
    )
    with pytest.raises(ValueError, match="missing"):
        select_valid_pairs(missing)


def test_exclude_discontinuous_counts_airport_changes():
    joined = pd.DataFrame(
        {
            "incoming_dest": ["ATL", "ATL", " JFK "],
            "next_origin": ["ATL", "JFK", "JFK"],
        }
    )

    kept, dropped = exclude_discontinuous(joined)

    assert dropped == 1
    assert len(kept) == 2


def test_loader_reads_needed_rows_across_chunks(tmp_path):
    flights = make_shuffled_flights()
    path = tmp_path / "flights.csv"
    flights.to_csv(path, index=False)

    loaded = load_flights_subset(path, [4, 0, 3], chunksize=2)

    assert loaded.index.tolist() == [0, 3, 4]
    assert loaded.index.name == "row_id"
    assert loaded.loc[4, "tail_number"] == "N1"
    assert loaded.loc[0, "arr_delay"] == 30.0
    assert loaded.loc[3, "origin"] == "ORD"
    assert str(loaded["crs_dep_dt_utc"].dtype).endswith("UTC]")


def test_loader_rejects_ids_beyond_file_length(tmp_path):
    path = tmp_path / "flights.csv"
    make_shuffled_flights().to_csv(path, index=False)

    with pytest.raises(ValueError, match="only 5 data rows"):
        load_flights_subset(path, [1, 9])


def test_loader_missing_column_raises(tmp_path):
    path = tmp_path / "flights.csv"
    make_shuffled_flights().drop(columns=["arr_delay"]).to_csv(
        path, index=False
    )

    with pytest.raises(ValueError, match="arr_delay"):
        load_flights_subset(path, [0])


# ---------------------------------------------------------------------------
# Chronological splitting
# ---------------------------------------------------------------------------
def make_time_table(times):
    return pd.DataFrame(
        {
            "next_row_id": range(len(times)),
            TIME_COLUMN: pd.to_datetime(times, utc=True),
        }
    )


def test_chronological_split_puts_latest_rows_in_test():
    times = [
        "2024-01-09",
        "2024-01-02",
        "2024-01-07",
        "2024-01-01",
        "2024-01-05",
        "2024-01-03",
        "2024-01-08",
        "2024-01-04",
        "2024-01-06",
        "2024-01-10",
    ]

    train, test, cutoff = chronological_split(make_time_table(times), 0.3)

    assert len(train) == 7
    assert len(test) == 3
    assert train[TIME_COLUMN].max() < test[TIME_COLUMN].min()
    assert test[TIME_COLUMN].min() == cutoff
    assert test[TIME_COLUMN].dt.day.tolist() == [8, 9, 10]


def test_chronological_split_keeps_tied_timestamps_together():
    times = [
        "2024-01-01",
        "2024-01-02",
        "2024-01-03",
        "2024-01-03",
        "2024-01-03",
        "2024-01-04",
    ]

    train, test, _ = chronological_split(make_time_table(times), 0.5)

    assert set(train[TIME_COLUMN]).isdisjoint(set(test[TIME_COLUMN]))
    assert len(train) == 2
    assert len(test) == 4


def test_chronological_split_rejects_bad_fraction():
    table = make_time_table(["2024-01-01", "2024-01-02", "2024-01-03"])

    for fraction in (0, 1, -0.1, 1.5):
        with pytest.raises(ValueError, match="test_fraction"):
            chronological_split(table, fraction)


def test_chronological_split_needs_distinct_times():
    table = make_time_table(["2024-01-01"] * 4)

    with pytest.raises(ValueError, match="no training rows"):
        chronological_split(table, 0.25)


def test_chronological_split_rejects_missing_times():
    table = make_time_table(["2024-01-01", "2024-01-02", "2024-01-03"])
    table.loc[1, TIME_COLUMN] = pd.NaT

    with pytest.raises(ValueError, match="missing"):
        chronological_split(table, 0.3)


# ---------------------------------------------------------------------------
# Missing values and features
# ---------------------------------------------------------------------------
def test_drop_incomplete_removes_missing_and_infinite_values():
    table = pd.DataFrame(
        {
            TARGET: [1.0, np.nan, 3.0, 4.0, 5.0],
            "incoming_arr_delay": [1.0, 2.0, np.nan, 4.0, np.inf],
            "next_origin": ["JFK", "JFK", "JFK", None, "JFK"],
            "extra": [None] * 5,
        }
    )

    kept, dropped = drop_incomplete(
        table, [TARGET, "incoming_arr_delay", "next_origin"]
    )

    assert dropped == 4
    assert kept[TARGET].tolist() == [1.0]


def test_drop_incomplete_requires_columns():
    with pytest.raises(ValueError, match="nope"):
        drop_incomplete(pd.DataFrame({"a": [1]}), ["a", "nope"])


def test_hhmm_to_minutes_handles_edge_values():
    values = pd.Series([735, 1430, 2400, 0, 2460, -5, None, "bad"])

    minutes = hhmm_to_minutes(values)

    assert minutes.iloc[:4].tolist() == [455.0, 870.0, 0.0, 0.0]
    assert minutes.iloc[4:].isna().all()


def test_build_features_adds_next_flight_minutes():
    flights = make_shuffled_flights()
    pairs = make_valid_pairs(flights)
    joined = join_pairs_with_flights(pairs, flights_index(flights))

    table = build_features(joined)
    row = table.loc[table["next_row_id"] == 1].iloc[0]

    assert row["next_crs_dep_minute"] == 17 * 60
    assert row["next_crs_arr_minute"] == 19 * 60
    assert row["scheduled_ground_time_min"] == 60.0


def test_encode_categories_uses_training_vocabulary_only():
    train = pd.DataFrame({"airport": ["JFK", "JFK", "ATL", "ORD"]})
    test = pd.DataFrame({"airport": ["ATL", "SEA", "JFK"]})

    train_enc, test_enc, vocab = encode_categories(
        train, test, ["airport"], max_categories=2
    )

    assert vocab["airport"] == ["JFK", "ATL"]
    assert train_enc["airport"].tolist() == [0, 0, 1, 2]
    # SEA never appeared in training, so it shares the "other" code.
    assert test_enc["airport"].tolist() == [1, 2, 0]


def test_encode_categories_rejects_too_many_categories():
    frame = pd.DataFrame({"airport": ["JFK"]})

    with pytest.raises(ValueError, match="max_categories"):
        encode_categories(frame, frame, ["airport"], max_categories=300)


# ---------------------------------------------------------------------------
# Leakage guard
# ---------------------------------------------------------------------------
def test_default_feature_sets_contain_no_leaky_columns():
    for numeric, categorical in FEATURE_SETS.values():
        assert_no_leakage([*numeric, *categorical])

    core_numeric, _ = FEATURE_SETS["core"]
    assert core_numeric == ["incoming_arr_delay", "scheduled_ground_time_min"]


def test_leaky_columns_are_rejected():
    for column in (
        "turnaround_slack_min",
        "actual_turnaround_min",
        TARGET,
        "next_actual_departure",
    ):
        with pytest.raises(ValueError, match="unavailable"):
            assert_no_leakage(["incoming_arr_delay", column])


# ---------------------------------------------------------------------------
# Metrics and baseline
# ---------------------------------------------------------------------------
def test_metrics_match_hand_calculation():
    y_true = [0.0, 2.0, 4.0]
    y_pred = [1.0, 2.0, 2.0]

    assert mean_absolute_error(y_true, y_pred) == pytest.approx(1.0)
    assert root_mean_squared_error(y_true, y_pred) == pytest.approx(
        np.sqrt(5 / 3)
    )

    stats = evaluate(y_true, y_pred)
    assert stats["n"] == 3
    assert stats["mae"] == pytest.approx(1.0)
    assert stats["rmse"] == pytest.approx(np.sqrt(5 / 3))


def test_metrics_reject_bad_input():
    with pytest.raises(ValueError, match="same shape"):
        mean_absolute_error([1, 2], [1])

    with pytest.raises(ValueError, match="empty"):
        root_mean_squared_error([], [])

    with pytest.raises(ValueError, match="finite"):
        evaluate([1.0, np.nan], [1.0, 2.0])


def test_baseline_predicts_training_mean_only():
    y_train = [10.0, 20.0, 30.0]

    predictions = baseline_predictions(y_train, 4)

    assert predictions.tolist() == [20.0, 20.0, 20.0, 20.0]

    with pytest.raises(ValueError, match="empty"):
        baseline_predictions([], 3)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def make_signal_flights(seed=0, n_tails=40, legs=15):
    """Synthetic fleet where delay carries over: d[k] = 0.6 d[k-1] + noise."""
    rng = np.random.default_rng(seed)
    records = []
    airports = ["JFK", "ATL"]

    for tail_index in range(n_tails):
        departure = pd.Timestamp("2024-01-05 06:00") + pd.Timedelta(
            minutes=7 * tail_index
        )
        delay = 0.0
        for leg in range(legs):
            delay = 0.6 * delay + rng.normal(0, 5)
            records.append(
                (
                    f"N{tail_index:03d}",
                    airports[leg % 2],
                    airports[(leg + 1) % 2],
                    departure,
                    120,
                    round(delay, 1),
                    "DL" if tail_index % 2 else "UA",
                )
            )
            departure += pd.Timedelta(
                minutes=120 + int(rng.integers(45, 240))
            )

    flights = make_flights_frame(records)
    order = rng.permutation(len(flights))
    return flights.iloc[order].reset_index(drop=True)


def write_inputs(tmp_path):
    flights = make_signal_flights()
    flights_path = tmp_path / "flights_clean.csv"
    pairs_path = tmp_path / "pairs.csv"

    flights.to_csv(flights_path, index=False)
    pairs = build_rotation_pairs(pd.read_csv(flights_path))
    pairs.to_csv(pairs_path, index=False)

    return flights_path, pairs_path


def test_run_end_to_end_writes_outputs_and_beats_baseline(tmp_path):
    pytest.importorskip("sklearn")
    flights_path, pairs_path = write_inputs(tmp_path)
    out_dir = tmp_path / "out" / "nested"

    results = run(
        flights_path,
        pairs_path,
        out_dir,
        test_fraction=0.25,
        seed=7,
        chunksize=100,
    )

    for name in ("predictions.csv", "metrics.json", "results_summary.md"):
        assert (out_dir / name).exists()

    counts = results["counts"]
    assert counts["train_rows"] + counts["test_rows"] == (
        counts["modelling_rows"]
    )
    assert counts["pairs_valid"] > 0

    split = results["split"]
    assert split["train_end_utc"] < split["cutoff_utc"]
    assert split["test_start_utc"] == split["cutoff_utc"]

    baseline = results["baseline"]["test"]
    model = results["model"]["test"]
    assert baseline["n"] == model["n"] == counts["test_rows"]
    assert model["rmse"] < baseline["rmse"]

    saved = json.loads((out_dir / "metrics.json").read_text())
    assert saved["model"]["features"] == [
        *FEATURE_SETS["extended"][0],
        *FEATURE_SETS["extended"][1],
    ]
    assert "turnaround_slack_min" not in json.dumps(saved["model"])

    predictions = pd.read_csv(out_dir / "predictions.csv")
    assert len(predictions) == counts["test_rows"]
    assert predictions["baseline_prediction"].nunique() == 1


def test_run_is_reproducible_for_a_fixed_seed(tmp_path):
    pytest.importorskip("sklearn")
    flights_path, pairs_path = write_inputs(tmp_path)

    first = run(flights_path, pairs_path, tmp_path / "a", seed=3)
    second = run(flights_path, pairs_path, tmp_path / "b", seed=3)

    assert first["model"]["test"] == second["model"]["test"]
    first_csv = (tmp_path / "a" / "predictions.csv").read_text()
    second_csv = (tmp_path / "b" / "predictions.csv").read_text()
    assert first_csv == second_csv


def test_run_rejects_unknown_feature_set(tmp_path):
    with pytest.raises(ValueError, match="feature_set"):
        run("a.csv", "b.csv", tmp_path, feature_set="everything")
