"""Train a next-flight arrival-delay model from reconstructed rotations.

Prediction task
---------------
For every valid aircraft connection (an *incoming* flight followed by the
*next* flight flown by the same aircraft), predict the next flight's
arrival delay ``arr_delay`` in minutes.

The prediction is made at the moment the incoming flight arrives. Only
information that exists at that moment is allowed as a predictor:

* the incoming flight's realised arrival delay,
* the scheduled ground time between the two flights,
* the next flight's published schedule (scheduled clock times, airline,
  origin and destination).

Leakage rules
-------------
``actual_turnaround_min`` uses the next flight's *actual* departure, which
is in the future at prediction time, so it can never be a predictor.
``turnaround_slack_min`` is excluded as well (per the project spec). It is
built from the incoming arrival and the next scheduled departure, which
makes it almost exactly ``scheduled_ground_time_min - incoming_arr_delay``,
so the core predictors already carry the same information.
Neither column is even read from the pairs file.

Row IDs
-------
``incoming_row_id`` and ``next_row_id`` are zero-based row positions in the
cleaned flights CSV, assigned by ``src.aircraft_rotations`` *before* it
sorts by aircraft and scheduled departure. The flights CSV is therefore
read in chunks while counting positions, and no row may be dropped before
the join. Each joined pair is cross-checked (tail number, airports,
eligibility flag, chronology) so a wrong ID base fails loudly.

Evaluation
----------
The split is chronological on the next flight's scheduled departure (UTC).
The baseline predicts the training-set mean arrival delay.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# Columns read from the cleaned flights CSV (nothing else is loaded).
FLIGHT_COLUMNS = [
    "tail_number",
    "rotation_eligible",
    "origin",
    "dest",
    "crs_dep_dt_utc",
    "crs_dep_time",
    "crs_arr_time",
    "reporting_airline",
    "arr_delay",
]

# Columns read from the connections CSV. turnaround_slack_min and
# actual_turnaround_min are intentionally absent.
PAIR_COLUMNS = [
    "incoming_row_id",
    "next_row_id",
    "tail_number",
    "incoming_origin",
    "incoming_dest",
    "next_origin",
    "next_dest",
    "scheduled_ground_time_min",
    "connection_valid",
]

TARGET = "next_arr_delay"
TIME_COLUMN = "next_crs_dep_dt_utc"

CORE_NUMERIC = ["incoming_arr_delay", "scheduled_ground_time_min"]
EXTENDED_NUMERIC = [
    *CORE_NUMERIC,
    "next_crs_dep_minute",
    "next_crs_arr_minute",
]
EXTENDED_CATEGORICAL = [
    "next_reporting_airline",
    "next_origin",
    "next_dest",
]

# feature set name -> (numeric columns, categorical columns)
FEATURE_SETS = {
    "core": (CORE_NUMERIC, []),
    "extended": (EXTENDED_NUMERIC, EXTENDED_CATEGORICAL),
}

FORBIDDEN_PREDICTORS = frozenset(
    {
        "turnaround_slack_min",
        "actual_turnaround_min",
        TARGET,
        "next_dep_delay",
        "next_dep_dt_utc",
        "next_arr_dt_utc",
    }
)

DEFAULT_CHUNKSIZE = 200_000
DEFAULT_MAX_CATEGORIES = 100
# HistGradientBoosting supports at most 255 category codes; one code is
# reserved for rare/unseen categories.
MAX_CATEGORIES_LIMIT = 254


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _coerce_bool(series: pd.Series) -> pd.Series:
    """Interpret a CSV flag column (bool, 0/1, or text) as booleans."""
    if series.dtype == bool:
        return series

    text = series.astype("string").str.strip().str.lower()
    flags = text.isin(["true", "1", "1.0", "yes"])
    return flags.fillna(False).astype(bool)


def _text_array(values: Any) -> np.ndarray:
    """Normalize identifiers to stripped strings for safe comparison."""
    series = pd.Series(values).astype("string").fillna("").str.strip()
    return series.to_numpy(dtype=object)


def hhmm_to_minutes(values: pd.Series) -> pd.Series:
    """Convert BTS hhmm clock values to minutes since midnight.

    Invalid values (negative, above 2400, minutes above 59) become NaN.
    2400 maps to 0, matching the start of the next day.
    """
    numbers = pd.to_numeric(values, errors="coerce")
    valid = numbers.between(0, 2400) & (numbers % 100 < 60)
    minutes = (numbers // 100) * 60 + numbers % 100
    return (minutes % 1440).where(valid)


def assert_no_leakage(feature_columns: list[str]) -> None:
    """Raise if a predictor would use the target or future information."""
    leaky = [
        column
        for column in feature_columns
        if column in FORBIDDEN_PREDICTORS or "actual" in column
    ]
    if leaky:
        raise ValueError(
            "Predictors use information that is unavailable at "
            f"prediction time: {', '.join(sorted(leaky))}"
        )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _check_columns(path: Path, required: list[str]) -> None:
    header = pd.read_csv(path, nrows=0).columns
    missing = sorted(set(required) - set(header))
    if missing:
        raise ValueError(
            f"{path} is missing required columns: {', '.join(missing)}"
        )


def load_pairs(path: str | Path) -> pd.DataFrame:
    """Load only the connection columns needed for modelling."""
    path = Path(path)
    _check_columns(path, PAIR_COLUMNS)
    return pd.read_csv(path, usecols=PAIR_COLUMNS)


def select_valid_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    """Keep connections flagged valid and check their row-ID columns."""
    valid = _coerce_bool(pairs["connection_valid"])
    kept = pairs.loc[valid].reset_index(drop=True)

    for column in ("incoming_row_id", "next_row_id"):
        ids = kept[column]
        if ids.isna().any():
            raise ValueError(f"{column} contains missing values.")
        as_int = ids.astype("int64")
        if not (as_int == ids).all():
            raise ValueError(f"{column} must contain whole numbers.")
        kept[column] = as_int

    if (kept["incoming_row_id"] == kept["next_row_id"]).any():
        raise ValueError("A connection links a flight to itself.")

    # Each flight has at most one successor and one predecessor.
    for column in ("incoming_row_id", "next_row_id"):
        if kept[column].duplicated().any():
            raise ValueError(
                f"Duplicate {column} values in valid connections."
            )

    return kept


def load_flights_subset(
    path: str | Path,
    needed_row_ids: Any,
    chunksize: int = DEFAULT_CHUNKSIZE,
) -> pd.DataFrame:
    """Read only the flight rows referenced by the connections.

    The CSV is streamed in chunks. Each row's zero-based position in the
    file is tracked explicitly so it matches the IDs written by
    ``src.aircraft_rotations``. The returned frame is indexed by that
    position (``row_id``).
    """
    path = Path(path)
    _check_columns(path, FLIGHT_COLUMNS)

    needed = np.unique(np.asarray(needed_row_ids, dtype=np.int64))
    parts = []
    offset = 0

    reader = pd.read_csv(path, usecols=FLIGHT_COLUMNS, chunksize=chunksize)
    for chunk in reader:
        positions = np.arange(offset, offset + len(chunk), dtype=np.int64)
        keep = np.isin(positions, needed)

        if keep.any():
            part = chunk.loc[keep].copy()
            part.index = pd.Index(positions[keep], name="row_id")
            parts.append(part)

        offset += len(chunk)

    if needed.size and (needed.min() < 0 or needed.max() >= offset):
        raise ValueError(
            f"Row IDs up to {int(needed.max())} were requested but "
            f"{path} has only {offset} data rows."
        )

    if parts:
        flights = pd.concat(parts)
    else:
        flights = pd.DataFrame(columns=FLIGHT_COLUMNS)
        flights.index.name = "row_id"

    flights["arr_delay"] = pd.to_numeric(
        flights["arr_delay"], errors="coerce"
    )
    flights["crs_dep_dt_utc"] = pd.to_datetime(
        flights["crs_dep_dt_utc"],
        errors="coerce",
        utc=True,
        format="ISO8601",
    )
    flights["rotation_eligible"] = _coerce_bool(flights["rotation_eligible"])

    return flights


# ---------------------------------------------------------------------------
# Joining and validation
# ---------------------------------------------------------------------------
def _raise_if_any(mask: Any, pairs: pd.DataFrame, message: str) -> None:
    mask = np.asarray(mask, dtype=bool)
    if mask.any():
        first = pairs.loc[mask].iloc[0]
        raise ValueError(
            f"{message} ({int(mask.sum())} of {len(pairs)} connections; "
            f"first: incoming_row_id={int(first['incoming_row_id'])}, "
            f"next_row_id={int(first['next_row_id'])}). Row IDs must be "
            "zero-based positions in the cleaned flights CSV."
        )


def join_pairs_with_flights(
    pairs: pd.DataFrame,
    flights: pd.DataFrame,
) -> pd.DataFrame:
    """Attach incoming and next flight fields using the row IDs.

    Parameters
    ----------
    pairs:
        Valid connections with integer ``incoming_row_id`` and
        ``next_row_id``.
    flights:
        Flight rows indexed by ``row_id`` (see ``load_flights_subset``).

    Returns
    -------
    pandas.DataFrame
        One row per connection. Incoming-flight fields are prefixed
        ``incoming_`` and next-flight fields ``next_``.

    Raises
    ------
    ValueError
        If any ID is missing from ``flights`` or a joined record disagrees
        with the connection (tail number, airports, eligibility flag or
        chronology).
    """
    pairs = pairs.reset_index(drop=True)
    incoming_ids = pairs["incoming_row_id"].to_numpy(dtype=np.int64)
    next_ids = pairs["next_row_id"].to_numpy(dtype=np.int64)

    available = flights.index.to_numpy()
    for name, ids in (("incoming", incoming_ids), ("next", next_ids)):
        _raise_if_any(
            ~np.isin(ids, available),
            pairs,
            f"{name}_row_id not found in the flights data",
        )

    incoming = flights.loc[incoming_ids].reset_index(drop=True)
    nxt = flights.loc[next_ids].reset_index(drop=True)

    tails = _text_array(pairs["tail_number"])
    _raise_if_any(
        (tails != _text_array(incoming["tail_number"]))
        | (tails != _text_array(nxt["tail_number"])),
        pairs,
        "tail_number does not match the flights at the given row IDs",
    )

    airport_pairs = (
        ("incoming_origin", incoming["origin"]),
        ("incoming_dest", incoming["dest"]),
        ("next_origin", nxt["origin"]),
        ("next_dest", nxt["dest"]),
    )
    airport_mismatch = np.zeros(len(pairs), dtype=bool)
    for pair_column, flight_values in airport_pairs:
        airport_mismatch |= (
            _text_array(pairs[pair_column]) != _text_array(flight_values)
        )
    _raise_if_any(
        airport_mismatch,
        pairs,
        "origin/dest columns do not match the flights at the given row IDs",
    )

    _raise_if_any(
        ~incoming["rotation_eligible"].to_numpy(dtype=bool)
        | ~nxt["rotation_eligible"].to_numpy(dtype=bool),
        pairs,
        "valid connection refers to a flight that is not rotation_eligible",
    )

    _raise_if_any(
        (nxt["crs_dep_dt_utc"] < incoming["crs_dep_dt_utc"]).to_numpy(),
        pairs,
        "next flight is scheduled before the incoming flight",
    )

    joined = pairs.copy()
    joined["incoming_arr_delay"] = incoming["arr_delay"].to_numpy()
    joined[TARGET] = nxt["arr_delay"].to_numpy()
    joined["incoming_crs_dep_dt_utc"] = incoming["crs_dep_dt_utc"].to_numpy()
    joined[TIME_COLUMN] = nxt["crs_dep_dt_utc"].to_numpy()
    joined["next_crs_dep_time"] = nxt["crs_dep_time"].to_numpy()
    joined["next_crs_arr_time"] = nxt["crs_arr_time"].to_numpy()
    joined["next_reporting_airline"] = nxt["reporting_airline"].to_numpy()

    return joined


def exclude_discontinuous(joined: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop connections where the aircraft changes airport between flights.

    If the incoming flight lands somewhere other than where the next flight
    departs, a leg is missing and the ground time is not a real turnaround.
    """
    continuous = _text_array(joined["incoming_dest"]) == _text_array(
        joined["next_origin"]
    )
    kept = joined.loc[continuous].reset_index(drop=True)
    return kept, int((~continuous).sum())


# ---------------------------------------------------------------------------
# Features and cleaning
# ---------------------------------------------------------------------------
def build_features(joined: pd.DataFrame) -> pd.DataFrame:
    """Add schedule-based features of the next flight."""
    table = joined.copy()
    table["next_crs_dep_minute"] = hhmm_to_minutes(table["next_crs_dep_time"])
    table["next_crs_arr_minute"] = hhmm_to_minutes(table["next_crs_arr_time"])
    return table


def drop_incomplete(
    table: pd.DataFrame,
    required_columns: list[str],
) -> tuple[pd.DataFrame, int]:
    """Remove rows with a missing or non-finite value in any required column."""
    missing = sorted(set(required_columns) - set(table.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    subset = table[required_columns]
    complete = subset.notna().all(axis=1).to_numpy(dtype=bool)

    numeric = subset.select_dtypes(include="number")
    if not numeric.empty:
        finite = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
        complete = complete & finite

    kept = table.loc[complete].reset_index(drop=True)
    return kept, int((~complete).sum())


# ---------------------------------------------------------------------------
# Splitting, encoding, modelling
# ---------------------------------------------------------------------------
def chronological_split(
    table: pd.DataFrame,
    test_fraction: float,
    time_column: str = TIME_COLUMN,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    """Split by time: earliest rows train, latest rows test.

    Rows sharing the cutoff timestamp all go to the test set so no instant
    appears on both sides. The test set therefore holds *at least*
    ``test_fraction`` of the rows when timestamps tie.

    Returns
    -------
    tuple
        ``(train, test, cutoff)`` where every train timestamp is strictly
        earlier than ``cutoff`` and every test timestamp is >= ``cutoff``.
    """
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be strictly between 0 and 1.")

    if time_column not in table.columns:
        raise ValueError(f"Missing time column: {time_column}")

    if table[time_column].isna().any():
        raise ValueError(f"{time_column} contains missing values.")

    if len(table) < 2:
        raise ValueError("Need at least two rows for a train/test split.")

    sort_keys = [time_column]
    if "next_row_id" in table.columns:
        sort_keys.append("next_row_id")

    ordered = table.sort_values(sort_keys, kind="stable").reset_index(
        drop=True
    )

    n_rows = len(ordered)
    n_test = min(max(int(round(n_rows * test_fraction)), 1), n_rows - 1)
    cutoff = ordered[time_column].iloc[n_rows - n_test]

    is_test = (ordered[time_column] >= cutoff).to_numpy()
    train = ordered.loc[~is_test].reset_index(drop=True)
    test = ordered.loc[is_test].reset_index(drop=True)

    if train.empty:
        raise ValueError(
            "Chronological split left no training rows; too many rows "
            "share the same scheduled departure time."
        )

    return train, test, cutoff


def encode_categories(
    train: pd.DataFrame,
    test: pd.DataFrame,
    columns: list[str],
    max_categories: int = DEFAULT_MAX_CATEGORIES,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, list[str]]]:
    """Integer-encode categorical columns using the training set only.

    The ``max_categories`` most frequent training values get their own
    code. Everything else, including values never seen in training, shares
    one extra "other" code.
    """
    if not 1 <= max_categories <= MAX_CATEGORIES_LIMIT:
        raise ValueError(
            f"max_categories must be between 1 and {MAX_CATEGORIES_LIMIT}."
        )

    train_out = train.copy()
    test_out = test.copy()
    vocabulary: dict[str, list[str]] = {}

    for column in columns:
        counts = train[column].astype(str).value_counts()
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        kept = [name for name, _ in ranked[:max_categories]]
        lookup = {name: code for code, name in enumerate(kept)}
        other_code = len(kept)

        for source, target in ((train, train_out), (test, test_out)):
            codes = source[column].astype(str).map(lookup)
            target[column] = codes.fillna(other_code).astype(int)

        vocabulary[column] = kept

    return train_out, test_out, vocabulary


def fit_model(
    features: pd.DataFrame,
    target: pd.Series,
    categorical_columns: list[str],
    seed: int,
    max_iter: int,
    learning_rate: float,
):
    """Fit a gradient-boosted regressor.

    Early stopping is off because scikit-learn would otherwise carve a
    random validation set out of the training data.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor

    categorical = set(categorical_columns)
    mask = [column in categorical for column in features.columns]

    model = HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=learning_rate,
        max_iter=max_iter,
        categorical_features=mask if any(mask) else None,
        early_stopping=False,
        random_state=seed,
    )
    model.fit(features.to_numpy(dtype=float), target.to_numpy(dtype=float))
    return model


# ---------------------------------------------------------------------------
# Metrics and reporting
# ---------------------------------------------------------------------------
def _as_arrays(y_true: Any, y_pred: Any) -> tuple[np.ndarray, np.ndarray]:
    true = np.asarray(y_true, dtype=float)
    pred = np.asarray(y_pred, dtype=float)

    if true.shape != pred.shape:
        raise ValueError("y_true and y_pred must have the same shape.")

    if true.size == 0:
        raise ValueError("Cannot compute metrics on empty input.")

    if not (np.isfinite(true).all() and np.isfinite(pred).all()):
        raise ValueError("Metrics require finite values.")

    return true, pred


def mean_absolute_error(y_true: Any, y_pred: Any) -> float:
    """Average absolute prediction error, in minutes."""
    true, pred = _as_arrays(y_true, y_pred)
    return float(np.mean(np.abs(true - pred)))


def root_mean_squared_error(y_true: Any, y_pred: Any) -> float:
    """Square root of the mean squared prediction error, in minutes."""
    true, pred = _as_arrays(y_true, y_pred)
    return float(np.sqrt(np.mean((true - pred) ** 2)))


def evaluate(y_true: Any, y_pred: Any) -> dict[str, float | int]:
    """Return sample count, MAE and RMSE."""
    true, pred = _as_arrays(y_true, y_pred)
    return {
        "n": int(true.size),
        "mae": mean_absolute_error(true, pred),
        "rmse": root_mean_squared_error(true, pred),
    }


def baseline_predictions(y_train: Any, n_rows: int) -> np.ndarray:
    """Predict the training-set mean for every row."""
    train = np.asarray(y_train, dtype=float)
    if train.size == 0:
        raise ValueError("Cannot compute a baseline from empty training data.")
    return np.full(n_rows, float(train.mean()))


def _improvement_pct(baseline: float, model: float) -> float:
    if baseline == 0:
        return 0.0
    return 100.0 * (baseline - model) / baseline


def format_results_summary(results: dict[str, Any]) -> str:
    """Render the metrics as a short markdown summary."""
    counts = results["counts"]
    split = results["split"]
    base = results["baseline"]
    model = results["model"]
    gain = results["improvement_test_pct"]

    rows = [
        ("Baseline (train mean)", "train", base["train"]),
        ("Baseline (train mean)", "test", base["test"]),
        ("Gradient boosting", "train", model["train"]),
        ("Gradient boosting", "test", model["test"]),
    ]

    lines = [
        "# Next-flight arrival delay: model results",
        "",
        "Target: arrival delay (minutes) of the next flight flown by the "
        "same aircraft.",
        f"Features ({results['config']['feature_set']}): "
        f"{', '.join(model['features'])}",
        "",
        "## Data",
        "",
        f"- Candidate connections: {counts['pairs_total']:,}",
        f"- Valid connections: {counts['pairs_valid']:,}",
        f"- Dropped, airport discontinuity: "
        f"{counts['discontinuous_excluded']:,}",
        f"- Dropped, missing target or predictor: "
        f"{counts['incomplete_excluded']:,}",
        f"- Modelling rows: {counts['modelling_rows']:,} "
        f"(train {counts['train_rows']:,}, test {counts['test_rows']:,})",
        f"- Chronological cutoff (next flight's scheduled departure, UTC): "
        f"{split['cutoff_utc']}",
        "",
        "## Metrics (minutes)",
        "",
        "| Model | Split | n | MAE | RMSE |",
        "|---|---|---:|---:|---:|",
    ]
    for name, part, stats in rows:
        lines.append(
            f"| {name} | {part} | {stats['n']:,} | "
            f"{stats['mae']:.3f} | {stats['rmse']:.3f} |"
        )

    lines += [
        "",
        "## Summary",
        "",
        f"On the held-out later period the model changes MAE by "
        f"{gain['mae']:+.1f}% and RMSE by {gain['rmse']:+.1f}% relative to "
        "the training-mean baseline (positive means the model is better).",
        "",
        "These are predictive associations, not causal effects.",
        "",
    ]
    return "\n".join(lines)


def save_outputs(
    output_dir: str | Path,
    predictions: pd.DataFrame,
    results: dict[str, Any],
) -> dict[str, Path]:
    """Write predictions, metrics and a markdown summary."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "predictions": output_dir / "predictions.csv",
        "metrics": output_dir / "metrics.json",
        "summary": output_dir / "results_summary.md",
    }

    predictions.to_csv(paths["predictions"], index=False)

    with open(paths["metrics"], "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)

    with open(paths["summary"], "w", encoding="utf-8") as handle:
        handle.write(format_results_summary(results))

    return paths


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run(
    flights_path: str | Path,
    pairs_path: str | Path,
    output_dir: str | Path,
    test_fraction: float = 0.2,
    seed: int = 42,
    feature_set: str = "extended",
    max_categories: int = DEFAULT_MAX_CATEGORIES,
    max_iter: int = 200,
    learning_rate: float = 0.1,
    chunksize: int = DEFAULT_CHUNKSIZE,
    require_continuity: bool = True,
) -> dict[str, Any]:
    """Run the full pipeline and return the results dictionary."""
    if feature_set not in FEATURE_SETS:
        raise ValueError(
            f"feature_set must be one of: {', '.join(FEATURE_SETS)}"
        )

    numeric, categorical = FEATURE_SETS[feature_set]
    feature_columns = [*numeric, *categorical]
    assert_no_leakage(feature_columns)

    pairs_all = load_pairs(pairs_path)
    pairs = select_valid_pairs(pairs_all)
    if pairs.empty:
        raise ValueError("No valid connections to model.")

    needed = np.concatenate(
        [pairs["incoming_row_id"].to_numpy(), pairs["next_row_id"].to_numpy()]
    )
    flights = load_flights_subset(flights_path, needed, chunksize=chunksize)

    joined = join_pairs_with_flights(pairs, flights)
    del flights

    n_discontinuous = 0
    if require_continuity:
        joined, n_discontinuous = exclude_discontinuous(joined)

    table = build_features(joined)
    table, n_incomplete = drop_incomplete(
        table, [TARGET, TIME_COLUMN, *feature_columns]
    )

    train, test, cutoff = chronological_split(table, test_fraction)

    train_x, test_x, vocabulary = encode_categories(
        train[feature_columns],
        test[feature_columns],
        categorical,
        max_categories=max_categories,
    )
    y_train = train[TARGET]
    y_test = test[TARGET]

    model = fit_model(
        train_x,
        y_train,
        categorical,
        seed=seed,
        max_iter=max_iter,
        learning_rate=learning_rate,
    )

    train_pred = model.predict(train_x.to_numpy(dtype=float))
    test_pred = model.predict(test_x.to_numpy(dtype=float))

    baseline_value = float(y_train.mean())
    baseline_train = baseline_predictions(y_train, len(train))
    baseline_test = baseline_predictions(y_train, len(test))

    baseline_metrics = {
        "train": evaluate(y_train, baseline_train),
        "test": evaluate(y_test, baseline_test),
    }
    model_metrics = {
        "train": evaluate(y_train, train_pred),
        "test": evaluate(y_test, test_pred),
    }

    results: dict[str, Any] = {
        "config": {
            "flights_path": str(flights_path),
            "pairs_path": str(pairs_path),
            "test_fraction": test_fraction,
            "seed": seed,
            "feature_set": feature_set,
            "max_categories": max_categories,
            "max_iter": max_iter,
            "learning_rate": learning_rate,
            "require_continuity": require_continuity,
        },
        "counts": {
            "pairs_total": int(len(pairs_all)),
            "pairs_valid": int(len(pairs)),
            "discontinuous_excluded": n_discontinuous,
            "incomplete_excluded": n_incomplete,
            "modelling_rows": int(len(table)),
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
        },
        "split": {
            "cutoff_utc": cutoff.isoformat(),
            "train_start_utc": train[TIME_COLUMN].min().isoformat(),
            "train_end_utc": train[TIME_COLUMN].max().isoformat(),
            "test_start_utc": test[TIME_COLUMN].min().isoformat(),
            "test_end_utc": test[TIME_COLUMN].max().isoformat(),
        },
        "baseline": {
            "train_mean_arr_delay": baseline_value,
            **baseline_metrics,
        },
        "model": {
            "name": "HistGradientBoostingRegressor",
            "features": feature_columns,
            "categorical_vocabulary_sizes": {
                column: len(names) for column, names in vocabulary.items()
            },
            **model_metrics,
        },
        "improvement_test_pct": {
            "mae": _improvement_pct(
                baseline_metrics["test"]["mae"],
                model_metrics["test"]["mae"],
            ),
            "rmse": _improvement_pct(
                baseline_metrics["test"]["rmse"],
                model_metrics["test"]["rmse"],
            ),
        },
    }

    predictions = test[
        ["incoming_row_id", "next_row_id", "tail_number", TIME_COLUMN]
    ].copy()
    predictions["actual_next_arr_delay"] = y_test.to_numpy()
    predictions["baseline_prediction"] = baseline_test
    predictions["model_prediction"] = test_pred

    paths = save_outputs(output_dir, predictions, results)
    results["output_files"] = {name: str(path) for name, path in paths.items()}

    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Train a model that predicts the next flight's arrival delay "
            "from the incoming flight's delay and scheduled ground time."
        )
    )

    parser.add_argument(
        "--flights",
        default="data/processed/flights_clean.csv",
        help="Path to the cleaned flight-level CSV.",
    )
    parser.add_argument(
        "--pairs",
        default="results/aircraft_rotation_pairs.csv",
        help="Path to the aircraft connection CSV.",
    )
    parser.add_argument(
        "--output-dir",
        default="results/delay_model",
        help="Directory for predictions, metrics and the summary.",
    )
    parser.add_argument(
        "--test-fraction",
        type=float,
        default=0.2,
        help="Fraction of the latest connections held out for testing.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--feature-set",
        choices=sorted(FEATURE_SETS),
        default="extended",
        help="core = incoming delay + ground time only.",
    )
    parser.add_argument(
        "--max-categories",
        type=int,
        default=DEFAULT_MAX_CATEGORIES,
        help="Most frequent categories kept per categorical feature.",
    )
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--learning-rate", type=float, default=0.1)
    parser.add_argument(
        "--chunksize",
        type=int,
        default=DEFAULT_CHUNKSIZE,
        help="Rows per chunk when streaming the flights CSV.",
    )
    parser.add_argument(
        "--allow-discontinuous",
        action="store_true",
        help="Keep connections where incoming dest != next origin.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    args = build_parser().parse_args(argv)

    for label, value in (("flights", args.flights), ("pairs", args.pairs)):
        if not Path(value).exists():
            raise FileNotFoundError(
                f"Input {label} file not found: {value}. "
                "Run preprocessing and rotation reconstruction first, "
                f"or specify --{label}."
            )

    results = run(
        flights_path=args.flights,
        pairs_path=args.pairs,
        output_dir=args.output_dir,
        test_fraction=args.test_fraction,
        seed=args.seed,
        feature_set=args.feature_set,
        max_categories=args.max_categories,
        max_iter=args.max_iter,
        learning_rate=args.learning_rate,
        chunksize=args.chunksize,
        require_continuity=not args.allow_discontinuous,
    )

    print(format_results_summary(results))
    print("Output files:")
    for name, path in results["output_files"].items():
        print(f"  {name}: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
