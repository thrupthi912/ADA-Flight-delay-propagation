from pathlib import Path
import json

import numpy as np
import pandas as pd

import train_delay_model as base


FLIGHTS = "data/processed/flights_clean_recovered.csv"
PAIRS = "data/processed/aircraft_rotation_pairs_2024_recovered.csv"
WEATHER = "data/processed/weather_observations_2024.csv"
MAPPING = "airport_weather_station_matches.csv"
OUTPUT = Path("results/delay_model_weather")

WEATHER_COLUMNS = [
    "temperature",
    "wind_speed",
    "precipitation",
    "relative_humidity",
    "visibility",
    "sea_level_pressure",
]

WEATHER_FEATURES = [f"origin_weather_{c}" for c in WEATHER_COLUMNS]
FEATURES = [*base.CORE_NUMERIC, *WEATHER_FEATURES]


def load_arrival_times(path, needed_ids, chunksize=200_000):
    """Load actual incoming-flight arrival timestamps by original row ID."""
    needed = np.unique(np.asarray(needed_ids, dtype=np.int64))
    parts = []
    offset = 0

    for chunk in pd.read_csv(
        path, usecols=["arr_dt_utc"], chunksize=chunksize
    ):
        positions = np.arange(offset, offset + len(chunk), dtype=np.int64)
        keep = np.isin(positions, needed)

        if keep.any():
            part = chunk.loc[keep, ["arr_dt_utc"]].copy()
            part.index = pd.Index(positions[keep], name="row_id")
            parts.append(part)

        offset += len(chunk)

    if needed.size and (needed.max() >= offset or needed.min() < 0):
        raise ValueError("Some incoming row IDs are outside the flights file.")

    result = pd.concat(parts) if parts else pd.DataFrame(
        columns=["arr_dt_utc"]
    )
    result["arr_dt_utc"] = pd.to_datetime(
        result["arr_dt_utc"], errors="coerce", utc=True,
        format="ISO8601"
    )
    return result["arr_dt_utc"]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)

    print("1. Loading valid aircraft connections...")
    pairs_all = base.load_pairs(PAIRS)
    pairs = base.select_valid_pairs(pairs_all)

    needed = np.concatenate([
        pairs["incoming_row_id"].to_numpy(),
        pairs["next_row_id"].to_numpy(),
    ])

    print("2. Joining flight records...")
    flights = base.load_flights_subset(FLIGHTS, needed)
    joined = base.join_pairs_with_flights(pairs, flights)
    del flights

    joined, discontinuous = base.exclude_discontinuous(joined)

    print("3. Loading actual incoming-flight arrival times...")
    arrivals = load_arrival_times(
        FLIGHTS, joined["incoming_row_id"].to_numpy()
    )
    joined["prediction_time_utc"] = joined["incoming_row_id"].map(arrivals)

    missing_times = int(joined["prediction_time_utc"].isna().sum())
    if missing_times:
        raise ValueError(
            f"{missing_times:,} connections lack actual incoming arrival "
            "timestamps. Stopping rather than using a future timestamp."
        )

    print("4. Loading airport-to-station mapping...")
    mapping = pd.read_csv(MAPPING, dtype={"iata_code": str, "station_id": str})
    station_lookup = dict(zip(mapping["iata_code"], mapping["station_id"]))

    joined["STATION"] = joined["next_origin"].map(station_lookup)
    if joined["STATION"].isna().any():
        raise ValueError("Some next-flight origin airports lack weather stations.")

    print("5. Loading processed weather observations...")
    weather = pd.read_csv(
        WEATHER,
        usecols=["STATION", "DATE", *WEATHER_COLUMNS],
        dtype={"STATION": str},
        parse_dates=["DATE"],
        low_memory=False,
    )
    weather["DATE"] = pd.to_datetime(weather["DATE"], utc=True)
    weather = weather.dropna(subset=["STATION", "DATE"])

    # Use only weather observations available by the prediction time.
    left = joined.sort_values("prediction_time_utc").copy()
    right = weather.sort_values("DATE").copy()

    print("6. Matching latest prior weather observation (2-hour limit)...")
    matched = pd.merge_asof(
        left,
        right,
        left_on="prediction_time_utc",
        right_on="DATE",
        by="STATION",
        direction="backward",
        tolerance=pd.Timedelta(hours=2),
        allow_exact_matches=True,
    )

    matched = matched.rename(
        columns={c: f"origin_weather_{c}" for c in WEATHER_COLUMNS}
    )
    matched["weather_observation_time_utc"] = matched["DATE"]
    matched["weather_age_minutes"] = (
        matched["prediction_time_utc"] - matched["DATE"]
    ).dt.total_seconds() / 60

    matched = base.build_features(matched)

    # Missing weather is retained. HistGradientBoostingRegressor handles NaNs.
    matched, incomplete = base.drop_incomplete(
        matched, [base.TARGET, base.TIME_COLUMN, *base.CORE_NUMERIC]
    )

    print("7. Creating the same chronological 80:20 split...")
    train, test, cutoff = base.chronological_split(matched, 0.2)

    y_train = train[base.TARGET]
    y_test = test[base.TARGET]

    train_x = train[FEATURES].copy()
    test_x = test[FEATURES].copy()

    print("8. Training weather-enhanced gradient boosting model...")
    model = base.fit_model(
        train_x,
        y_train,
        categorical_columns=[],
        seed=42,
        max_iter=200,
        learning_rate=0.1,
    )

    train_pred = model.predict(train_x.to_numpy(dtype=float))
    test_pred = model.predict(test_x.to_numpy(dtype=float))

    print("9. Training core-only model on identical rows...")
    core_train_x = train[base.CORE_NUMERIC].copy()
    core_test_x = test[base.CORE_NUMERIC].copy()
    core_model = base.fit_model(
        core_train_x, y_train, [],
        seed=42, max_iter=200, learning_rate=0.1
    )
    core_test_pred = core_model.predict(core_test_x.to_numpy(dtype=float))
    core_metrics = base.evaluate(y_test, core_test_pred)

    baseline_train = base.baseline_predictions(y_train, len(train))
    baseline_test = base.baseline_predictions(y_train, len(test))

    baseline_metrics = {
        "train": base.evaluate(y_train, baseline_train),
        "test": base.evaluate(y_test, baseline_test),
    }
    model_metrics = {
        "train": base.evaluate(y_train, train_pred),
        "test": base.evaluate(y_test, test_pred),
    }

    results = {
        "prediction_time": "Actual incoming-flight arrival UTC",
        "weather_join": "Latest origin observation at or before prediction time",
        "weather_tolerance_minutes": 120,
        "weather_features": WEATHER_FEATURES,
        "core_features": base.CORE_NUMERIC,
        "counts": {
            "valid_pairs_before_continuity": int(len(pairs)),
            "discontinuous_excluded": int(discontinuous),
            "incomplete_core_rows_excluded": int(incomplete),
            "modelling_rows": int(len(matched)),
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "rows_with_weather_match": int(
                matched["weather_observation_time_utc"].notna().sum()
            ),
            "rows_without_weather_match": int(
                matched["weather_observation_time_utc"].isna().sum()
            ),
        },
        "split": {
            "cutoff_utc": cutoff.isoformat(),
            "train_start_utc": train[base.TIME_COLUMN].min().isoformat(),
            "train_end_utc": train[base.TIME_COLUMN].max().isoformat(),
            "test_start_utc": test[base.TIME_COLUMN].min().isoformat(),
            "test_end_utc": test[base.TIME_COLUMN].max().isoformat(),
        },
        "baseline": baseline_metrics,
        "weather_model": model_metrics,
        "improvement_test_pct": {
            "mae": base._improvement_pct(
                baseline_metrics["test"]["mae"],
                model_metrics["test"]["mae"],
            ),
            "rmse": base._improvement_pct(
                baseline_metrics["test"]["rmse"],
                model_metrics["test"]["rmse"],
            ),
        },
    }

    predictions = test[
        [
            "incoming_row_id", "next_row_id", "tail_number",
            base.TIME_COLUMN, "prediction_time_utc",
            "weather_observation_time_utc", "weather_age_minutes",
        ]
    ].copy()
    predictions["actual_next_arr_delay"] = y_test.to_numpy()
    predictions["baseline_prediction"] = baseline_test
    predictions["weather_model_prediction"] = test_pred

    predictions["core_model_prediction"] = core_test_pred

    comparison = {
        "same_rows": True,
        "same_chronological_split": True,
        "cutoff_utc": cutoff.isoformat(),
        "train_rows": len(train),
        "test_rows": len(test),
        "core_model_test": core_metrics,
        "weather_model_test": model_metrics["test"],
        "weather_improvement_over_core_pct": {
            "mae": base._improvement_pct(
                core_metrics["mae"], model_metrics["test"]["mae"]
            ),
            "rmse": base._improvement_pct(
                core_metrics["rmse"], model_metrics["test"]["rmse"]
            ),
        },
    }
    with open(OUTPUT / "same_rows_comparison.json", "w") as f:
        json.dump(comparison, f, indent=2)

    predictions.to_csv(OUTPUT / "predictions.csv", index=False)
    with open(OUTPUT / "metrics.json", "w") as f:
        json.dump(results, f, indent=2)

    summary = [
        "# Weather-enhanced next-flight arrival-delay model",
        "",
        f"Prediction time: {results['prediction_time']}",
        f"Weather join: {results['weather_join']}",
        f"Weather lookback: {results['weather_tolerance_minutes']} minutes",
        f"Chronological cutoff: {cutoff.isoformat()}",
        "",
        f"Training rows: {len(train):,}",
        f"Test rows: {len(test):,}",
        f"Rows with weather match: {results['counts']['rows_with_weather_match']:,}",
        f"Rows without weather match: {results['counts']['rows_without_weather_match']:,}",
        "",
        "| Model | Test MAE (min) | Test RMSE (min) |",
        "|---|---:|---:|",
        f"| Mean baseline | {baseline_metrics['test']['mae']:.3f} | {baseline_metrics['test']['rmse']:.3f} |",
        f"| Weather model | {model_metrics['test']['mae']:.3f} | {model_metrics['test']['rmse']:.3f} |",
        "",
        f"MAE improvement: {results['improvement_test_pct']['mae']:.2f}%",
        f"RMSE improvement: {results['improvement_test_pct']['rmse']:.2f}%",
    ]
    (OUTPUT / "results_summary.md").write_text("\n".join(summary) + "\n")

    print("\nWEATHER MODEL COMPLETE")
    print("\n".join(summary))
    print("\nSaved results to:", OUTPUT)


if __name__ == "__main__":
    main()
