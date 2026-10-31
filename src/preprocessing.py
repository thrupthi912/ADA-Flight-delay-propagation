"""Initial cleaning of BTS On-Time Performance flight records.

Owned by Thrupthi G D. Standardizes raw BTS columns, validates records,
reconstructs **comparable chronological (UTC) timestamps** from airport-local
clock times, flags data-quality issues, removes exact duplicate rows (and only
those), and writes a cleaned flight-level dataset plus a reproducible
data-quality report.

Why timezones matter (the bug this fixes)
-----------------------------------------
BTS stores departure and arrival times as LOCAL airport clock times in hhmm
format. A departure is in the ORIGIN's timezone and an arrival is in the
DESTINATION's timezone. The previous version of this code computed arrival as
``dep_dt + actual_elapsed_time`` and treated the result as if it were a single
timezone. That is wrong across time zones: a JFK (Eastern) flight arriving in
LAX (Pacific) has a 3-hour offset, so adding elapsed time to an origin-local
departure does NOT give the destination-local arrival clock, and two local
times from different airports cannot be compared or sorted.

This version instead converts each local clock time to a UTC *instant* using the
airport's IANA timezone (which also encodes daylight-saving rules), then uses
timezone-independent *durations* (elapsed time, signed delay) to move between
instants:

    crs_dep_dt_utc = origin_local(flight_date, crs_dep_time) -> UTC   (via origin tz)
    dep_dt_utc     = crs_dep_dt_utc + dep_delay (minutes)              (a duration)
    crs_arr_dt_utc = crs_dep_dt_utc + crs_elapsed_time (minutes)       (a duration)
    arr_dt_utc     = dep_dt_utc     + actual_elapsed_time (minutes)    (a duration)

``arr_dt_utc`` is the arrival INSTANT in UTC (correct regardless of the
origin/destination time-zone difference). It is NOT labelled "destination-local";
the destination-local arrival clock is simply the preserved raw ``arr_time``
field. Sorting flights by ``dep_dt_utc`` / ``arr_dt_utc`` is now correct across
airports, midnight, and most daylight-saving transitions.

Validation philosophy
---------------------
* Raw clock fields (``crs_dep_time``, ``dep_time``, ``crs_arr_time``,
  ``arr_time``) are preserved unchanged.
* Timestamp consistency is checked with INDEPENDENT raw fields, not with the
  identities used to build the timestamps:
    - ``dep_time_inconsistent``: raw ``dep_time`` vs ``crs_dep_time + dep_delay``
    - ``arr_time_inconsistent``: raw ``arr_time`` vs ``crs_arr_time + arr_delay``
    - ``arrival_elapsed_inconsistent``: UTC arrival instant (from elapsed time)
      converted back to the destination timezone vs raw ``arr_time``
* Missing cancellation/diversion indicators are NOT assumed to be zero; they are
  flagged and the record is made ineligible for rotation.
* Daylight-saving ambiguous (fall-back) and nonexistent (spring-forward) local
  times are detected, given a best-effort UTC instant (fold=0), flagged, and made
  ineligible for rotation. They are never silently dropped.
* If an airport's timezone is unknown (not in the map), its timestamps are left
  as raw local values and flagged ``missing_origin_timezone``; we do NOT invent a
  timezone.
* Exact duplicate rows are removed (a data error) and logged; every other
  decision is a documented flag plus an ``exclusion_reason``.

Units: minutes for delay/elapsed/taxi/air-time, miles for distance.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import timezone as _dt_tz
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.data_ingestion import (
    ALIASES, COLUMN_MAP, REQUIRED_FIELDS, ESSENTIAL_FIELDS,
    basic_info, require_columns,
)

__all__ = [
    "parse_flight_date",
    "parse_hhmm",
    "standardize_columns",
    "coerce_types",
    "build_timestamps",
    "validate_records",
    "detect_duplicates",
    "clean",
    "generate_quality_report",
    "main",
]

# Numeric fields (minutes / miles / counts / ids) coerced from string.
NUMERIC_FIELDS = [
    "year", "quarter", "month", "day_of_month", "day_of_week",
    "dot_id_reporting_airline", "origin_airport_id", "origin_airport_seq_id",
    "origin_city_market_id", "origin_state_fips", "origin_wac",
    "dest_airport_id", "dest_airport_seq_id", "dest_city_market_id",
    "dest_state_fips", "dest_wac",
    "dep_delay", "dep_delay_minutes", "departure_delay_groups",
    "arr_delay", "arr_delay_minutes", "arrival_delay_groups",
    "taxi_out", "taxi_in", "wheels_off", "wheels_on",
    "crs_elapsed_time", "actual_elapsed_time", "air_time", "flights",
    "distance", "distance_group",
    "carrier_delay", "weather_delay", "nas_delay", "security_delay",
    "late_aircraft_delay", "total_add_gtime", "longest_add_gtime",
    "div_airport_landings", "div_actual_elapsed_time", "div_arr_delay",
    "div_distance",
]

# 0/1 indicator fields -> nullable integer.
INDICATOR_FIELDS = ["cancelled", "diverted", "dep_del15", "arr_del15"]

# Plausibility bounds (minutes / miles) used to flag implausible records.
MAX_ELAPSED_MIN = 24 * 60      # a domestic leg should not take more than 24h
MAX_DELAY_MIN = 24 * 60        # a single delay > 24h is implausible
MAX_DISTANCE_MI = 6000         # domestic non-stop max is well under this
MIN_TAIL_LEN = 1
MAX_TAIL_LEN = 6              # N-numbers are at most 6 characters

# Tolerances for the non-circular consistency checks (minutes).
DEP_TIME_TOL = 1
ARR_TIME_TOL = 1
ARRIVAL_ELAPSED_TOL = 3


def parse_flight_date(series: pd.Series) -> pd.Series:
    """Parse BTS FL_DATE which can be any of:
    - M/D/YYYY 12:00:00 AM  (e.g. "2/1/2024 12:00:00 AM")  <- real data format
    - YYYYMMDD              (e.g. "20240201")              <- synthetic fixtures
    - YYYY-MM-DD            (e.g. "2024-02-01")
    - MM/DD/YYYY            (e.g. "02/01/2024")
    Returns a datetime64[ns] Series; unparseable values become NaT.
    """
    s = series.astype(str).str.strip()
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    # YYYYMMDD (synthetic test fixtures, old BTS format)
    m = s.str.match(r"^\d{8}$")
    if m.any():
        result.update(pd.to_datetime(s[m], format="%Y%m%d", errors="coerce"))

    # YYYY-MM-DD
    m2 = s.str.match(r"^\d{4}-\d{2}-\d{2}$") & result.isna()
    if m2.any():
        result.update(pd.to_datetime(s[m2], format="%Y-%m-%d", errors="coerce"))

    # M/D/YYYY or MM/DD/YYYY with or without time component (real BTS format)
    # Use format="mixed" with dayfirst=False for cross-platform compatibility
    m3 = s.str.match(r"^\d{1,2}/\d{1,2}/\d{4}") & result.isna()
    if m3.any():
        result.update(pd.to_datetime(s[m3], format="mixed", dayfirst=False, errors="coerce"))

    return result


def parse_hhmm(value: Any) -> float:
    """Parse a BTS hhmm local time into minutes-since-midnight.

    Returns NaN for missing/invalid input. 2400 is treated as 1440 minutes
    (midnight at the end of the day, i.e. start of the next day).

    Examples: "735" -> 455, "1430" -> 870, "2400" -> 1440, "" -> NaN, "2500" -> NaN.
    """
    if value is None:
        return np.nan
    try:
        if isinstance(value, str):
            v = value.strip()
            if v == "" or v.lower() == "nan":
                return np.nan
            f = float(v)
        elif isinstance(value, (int, float, np.integer, np.floating)):
            f = float(value)
        else:
            return np.nan
        if math.isnan(f):
            return np.nan
        n = int(round(f))
    except (ValueError, TypeError):
        return np.nan
    if n == 2400:
        return 1440.0
    if n < 0 or n > 2400:
        return np.nan
    hh, mm = n // 100, n % 100
    if hh > 23 or mm > 59:
        return np.nan
    return float(hh * 60 + mm)


def _circular_min_diff(a: pd.Series, b: pd.Series) -> pd.Series:
    """Smallest absolute difference between two minute-of-day series, wrapping
    at 1440 (so 1435 vs 5 differs by 10, not 1430)."""
    d = (a - b).abs()
    return d.where(d <= 720, 1440 - d)


def _classify_dst(ts: pd.Timestamp, tzname: str) -> tuple[str, pd.Timestamp]:
    """Classify a naive-local Timestamp for DST-aware conversion to UTC.

    Returns ``(status, utc_instant_fold0)`` where status is one of
    ``"ok"``, ``"ambiguous"`` (fall-back: the local time occurs twice), or
    ``"nonexistent"`` (spring-forward: the local time does not exist). The
    returned UTC instant uses fold=0 as a documented best-effort choice for the
    ambiguous/nonexistent cases.

    The distinguisher relies on the empirical fact that converting the fold=0
    instant back to the local zone preserves the wall clock for ambiguous times
    but NOT for nonexistent times (the gap gets shifted).
    """
    tz = ZoneInfo(tzname)
    a0 = ts.replace(tzinfo=tz, fold=0)
    a1 = ts.replace(tzinfo=tz, fold=1)
    u0 = a0.astimezone(_dt_tz.utc)
    u1 = a1.astimezone(_dt_tz.utc)
    if u0 == u1:
        return "ok", pd.Timestamp(u0)
    rt0 = u0.astimezone(tz).replace(tzinfo=None)
    status = "ambiguous" if rt0 == ts else "nonexistent"
    return status, pd.Timestamp(u0)


def _localize_group_to_utc(local_series: pd.Series, tzname: str):
    """Vectorized local->UTC for one timezone group; transitions get a per-row
    best-effort UTC and an ambiguous/nonexistent label."""
    aware = local_series.dt.tz_localize(tzname, ambiguous="NaT", nonexistent="NaT")
    utc = aware.dt.tz_convert("UTC")
    status = pd.Series("ok", index=local_series.index, dtype=object)
    nat_mask = utc.isna() & local_series.notna()
    for i in local_series.index[nat_mask]:
        st, u = _classify_dst(local_series.loc[i], tzname)
        status.loc[i] = st
        utc.loc[i] = u
    return utc, status


# ---------------------------------------------------------------------------
# Step 1: standardize column names
# ---------------------------------------------------------------------------
def _resolve_name(col: str) -> str:
    """Map a raw column name to its standardized snake_case name, if known."""
    canonical = ALIASES.get(col, col)          # old name -> current BTS name
    if canonical in COLUMN_MAP:                  # current BTS name -> snake_case
        return COLUMN_MAP[canonical]
    for key in COLUMN_MAP:                       # case-insensitive fallback
        if key.lower() == canonical.lower():
            return COLUMN_MAP[key]
    for key in ALIASES:
        if key.lower() == canonical.lower():
            return COLUMN_MAP[ALIASES[key]]
    return col                                   # unknown -> keep as-is (preserved)


def standardize_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Rename raw BTS columns to standardized snake_case names.

    Returns the renamed dataframe and the {original: standardized} mapping
    actually applied (for traceability). Unknown columns are kept untouched.
    """
    mapping = {col: _resolve_name(col) for col in df.columns}
    out = df.copy()
    out.columns = [mapping[c] for c in df.columns]
    return out, mapping


# ---------------------------------------------------------------------------
# Step 2: coerce types
# ---------------------------------------------------------------------------
def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Convert string columns to their proper numeric / datetime types.

    Done explicitly (rather than at read time) so every conversion and the
    resulting missing values are auditable. Missing/blank -> NaN. The raw hhmm
    time strings are intentionally left as strings so the original values are
    preserved verbatim.
    """
    out = df.copy()

    if "flight_date" in out.columns:
        out["flight_date"] = parse_flight_date(out["flight_date"])

    for col in NUMERIC_FIELDS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    for col in INDICATOR_FIELDS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")

    # When DIVERTED / diverted is absent from the source file, add it as all-NA
    # (Int64) rather than treating those flights as confirmed non-diverted.
    if "diverted" not in out.columns:
        out["diverted"] = pd.array([pd.NA] * len(out), dtype="Int64")

    for col in ["tail_number", "reporting_airline", "iata_code_reporting_airline",
               "origin", "dest", "cancellation_code", "origin_state",
               "dest_state", "origin_city_name", "dest_city_name"]:
        if col in out.columns:
            s = out[col].astype("string").str.strip()
            out[col] = s.where((s != "") & (s.notna()) & (s.str.lower() != "nan"),
                               pd.NA)
    return out


# ---------------------------------------------------------------------------
# Step 3: reconstruct comparable UTC timestamps
# ---------------------------------------------------------------------------
def build_timestamps(df: pd.DataFrame, airport_tz: dict | None = None) -> pd.DataFrame:
    """Add timezone-aware UTC timestamp columns and non-circular consistency
    checks. See the module docstring for the full model.

    Added columns
    -------------
    Raw values preserved: ``crs_dep_time``, ``dep_time``, ``crs_arr_time``,
    ``arr_time`` (hhmm strings), ``dep_delay``, ``arr_delay``,
    ``crs_elapsed_time``, ``actual_elapsed_time`` (minutes).

    Reference (origin-local, timezone-NAIVE; NOT comparable across airports):
      ``crs_dep_dt_local``, ``dep_dt_local``
    Airport timezones used:
      ``origin_tz``, ``dest_tz``, ``tz_origin_known``, ``tz_dest_known``
    UTC instants (comparable across airports; ``...+00:00`` in CSV output):
      ``crs_dep_dt_utc``, ``dep_dt_utc``, ``crs_arr_dt_utc``, ``arr_dt_utc``
    DST status of the departure conversion:
      ``crs_dep_dst_status``, ``dep_dst_status`` (ok/ambiguous/nonexistent/no_tz/na)
    Convenience:
      ``has_utc_timestamps`` (dep_dt_utc is present)
    Non-circular consistency flags:
      ``dep_time_inconsistent``, ``arr_time_inconsistent``,
      ``arrival_elapsed_inconsistent``
    """
    out = df.copy()
    tz_map = airport_tz or {}

    out["_crs_dep_min"] = out["crs_dep_time"].map(parse_hhmm) if "crs_dep_time" in out else np.nan
    out["_dep_min"] = out["dep_time"].map(parse_hhmm) if "dep_time" in out else np.nan
    out["_crs_arr_min"] = out["crs_arr_time"].map(parse_hhmm) if "crs_arr_time" in out else np.nan
    out["_arr_min"] = out["arr_time"].map(parse_hhmm) if "arr_time" in out else np.nan

    fd = out["flight_date"] if "flight_date" in out else pd.NaT

    # Origin-local naive departure datetimes (reference only).
    out["crs_dep_dt_local"] = fd + pd.to_timedelta(out["_crs_dep_min"], unit="m")
    if "dep_delay" in out:
        out["dep_dt_local"] = out["crs_dep_dt_local"] + pd.to_timedelta(out["dep_delay"], unit="m")
    else:
        out["dep_dt_local"] = pd.NaT

    # Resolve airport timezones (None when unknown -> not invented).
    origin = (out["origin"].astype("string").str.upper().str.strip()
              if "origin" in out else pd.Series([pd.NA] * len(out), index=out.index))
    dest = (out["dest"].astype("string").str.upper().str.strip()
            if "dest" in out else pd.Series([pd.NA] * len(out), index=out.index))
    out["origin_tz"] = origin.map(lambda c: tz_map.get(c) if pd.notna(c) else None)
    out["dest_tz"] = dest.map(lambda c: tz_map.get(c) if pd.notna(c) else None)
    out["tz_origin_known"] = out["origin_tz"].notna()
    out["tz_dest_known"] = out["dest_tz"].notna()

    # Convert origin-local departures to UTC, grouped by origin timezone.
    def _build_utc(local_col, utc_col, status_col):
        out[utc_col] = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")
        out[status_col] = pd.Series(
            np.where(out["tz_origin_known"], "na", "no_tz"), index=out.index, dtype=object)
        local = out[local_col]
        known = out["tz_origin_known"] & local.notna()
        sub = pd.DataFrame({"tz": out["origin_tz"], "local": local})
        for tzname, grp in sub.loc[known].groupby("tz"):
            utc_vals, status_vals = _localize_group_to_utc(grp["local"], tzname)
            out.loc[utc_vals.index, utc_col] = utc_vals
            out.loc[status_vals.index, status_col] = status_vals

    _build_utc("crs_dep_dt_local", "crs_dep_dt_utc", "crs_dep_dst_status")
    _build_utc("dep_dt_local", "dep_dt_utc", "dep_dst_status")

    # Arrival instants (UTC) = departure instant + elapsed time (a duration).
    crs_elapsed = out["crs_elapsed_time"] if "crs_elapsed_time" in out else pd.Series(np.nan, index=out.index)
    act_elapsed = out["actual_elapsed_time"] if "actual_elapsed_time" in out else pd.Series(np.nan, index=out.index)
    out["crs_arr_dt_utc"] = out["crs_dep_dt_utc"] + pd.to_timedelta(crs_elapsed, unit="m")
    out["arr_dt_utc"] = out["dep_dt_utc"] + pd.to_timedelta(act_elapsed, unit="m")
    out["has_utc_timestamps"] = out["dep_dt_utc"].notna()

    # Non-circular consistency check 1: raw dep_time vs crs_dep_time + dep_delay
    # (both origin-local, same zone). Wraparound at 1440 handles midnight.
    if "dep_delay" in out and "crs_dep_time" in out and "dep_time" in out:
        calc = (out["_crs_dep_min"] + out["dep_delay"]) % 1440
        diff = _circular_min_diff(out["_dep_min"], calc)
        out["dep_time_inconsistent"] = (
            out["_dep_min"].notna() & out["_crs_dep_min"].notna()
            & out["dep_delay"].notna() & (diff > DEP_TIME_TOL))
    else:
        out["dep_time_inconsistent"] = pd.Series(False, index=out.index)

    # Non-circular consistency check 2: raw arr_time vs crs_arr_time + arr_delay
    # (both destination-local, same zone).
    if "arr_delay" in out and "crs_arr_time" in out and "arr_time" in out:
        calc = (out["_crs_arr_min"] + out["arr_delay"]) % 1440
        diff = _circular_min_diff(out["_arr_min"], calc)
        out["arr_time_inconsistent"] = (
            out["_arr_min"].notna() & out["_crs_arr_min"].notna()
            & out["arr_delay"].notna() & (diff > ARR_TIME_TOL))
    else:
        out["arr_time_inconsistent"] = pd.Series(False, index=out.index)

    # Non-circular consistency check 3: UTC arrival instant (from elapsed time)
    # converted back to the destination timezone vs raw arr_time. Only checked
    # when the departure conversion was unambiguous and the dest tz is known.
    out["_arr_local_min_from_utc"] = pd.Series(np.nan, index=out.index)
    mask = ((out["dep_dst_status"] == "ok") & out["tz_dest_known"]
            & out["arr_dt_utc"].notna() & out["_arr_min"].notna())
    sub = pd.DataFrame({"tz": out["dest_tz"], "arr_utc": out["arr_dt_utc"]})
    for tzname, grp in sub.loc[mask].groupby("tz"):
        arr_local = grp["arr_utc"].dt.tz_convert(tzname)
        mins = arr_local.dt.hour * 60 + arr_local.dt.minute
        out.loc[mins.index, "_arr_local_min_from_utc"] = mins
    diff = _circular_min_diff(out["_arr_local_min_from_utc"], out["_arr_min"])
    out["arrival_elapsed_inconsistent"] = mask & (diff > ARRIVAL_ELAPSED_TOL)

    out = out.drop(columns=[c for c in ["_crs_dep_min", "_dep_min", "_crs_arr_min",
                                        "_arr_min", "_arr_local_min_from_utc"]
                            if c in out])
    return out


# ---------------------------------------------------------------------------
# Step 4: validate and flag records
# ---------------------------------------------------------------------------
def validate_records(df: pd.DataFrame) -> pd.DataFrame:
    """Add boolean quality flags and an ``exclusion_reason`` string per record."""
    out = df.copy()

    # Cancellation / diversion: do NOT assume missing == 0.
    if "cancelled" in out:
        out["is_cancelled"] = out["cancelled"].eq(1)          # nullable boolean
    else:
        out["is_cancelled"] = pd.Series(pd.NA, index=out.index, dtype="boolean")
    if "diverted" in out:
        out["is_diverted"] = out["diverted"].eq(1)
    else:
        out["is_diverted"] = pd.Series(pd.NA, index=out.index, dtype="boolean")

    # Tail number presence + plausibility (alphanumeric, 1-6 chars).
    if "tail_number" in out:
        tail = out["tail_number"].astype("string")
        tail_present = tail.notna()
        tail_alnum = tail.str.match(r"^[A-Za-z0-9]{%d,%d}$" % (MIN_TAIL_LEN, MAX_TAIL_LEN)).fillna(False)
        out["has_tail_number"] = tail_present.fillna(False)
        out["valid_tail_number"] = (tail_present & tail_alnum).fillna(False)
    else:
        out["has_tail_number"] = pd.Series(False, index=out.index)
        out["valid_tail_number"] = pd.Series(False, index=out.index)

    # Time validity.
    out["valid_crs_dep_time"] = (out["crs_dep_dt_local"].notna()
                                 if "crs_dep_dt_local" in out
                                 else pd.Series(False, index=out.index))
    out["valid_dep_time"] = (out["dep_dt_local"].notna()
                             if "dep_dt_local" in out
                             else pd.Series(False, index=out.index))
    out["valid_arr_time"] = (out["arr_dt_utc"].notna()
                             if "arr_dt_utc" in out
                             else pd.Series(False, index=out.index))
    out["invalid_timestamp"] = ~out["valid_crs_dep_time"].fillna(False)

    # Implausible numeric values.
    out["implausible_record"] = pd.Series(False, index=out.index)
    if "distance" in out:
        d = out["distance"]
        out["implausible_record"] = out["implausible_record"] | (d.notna() & (d <= 0)) | (d > MAX_DISTANCE_MI)
    for ecol in ("actual_elapsed_time", "crs_elapsed_time"):
        if ecol in out:
            e = out[ecol]
            out["implausible_record"] = out["implausible_record"] | (e.notna() & (e <= 0)) | (e > MAX_ELAPSED_MIN)
    if "dep_delay" in out:
        out["implausible_record"] = out["implausible_record"] | (out["dep_delay"].abs() > MAX_DELAY_MIN)
    if "arr_delay" in out:
        out["implausible_record"] = out["implausible_record"] | (out["arr_delay"].abs() > MAX_DELAY_MIN)

    # rotation_eligible: usable by the aircraft-rotation module. Requires UTC
    # timestamps (so origin tz is known) and a clean, unambiguous departure.
    out["rotation_eligible"] = (
        out["valid_tail_number"].fillna(False)
        & out["valid_crs_dep_time"].fillna(False)
        & out.get("has_utc_timestamps", pd.Series(False, index=out.index)).fillna(False)
        & out["valid_arr_time"].fillna(False)
        & out["is_cancelled"].eq(False).fillna(False)
        & out["is_diverted"].eq(False).fillna(False)
        & ~out["dep_time_inconsistent"].fillna(False)
        & ~out["arr_time_inconsistent"].fillna(False)
        & ~out["arrival_elapsed_inconsistent"].fillna(False)
        & (out.get("dep_dst_status", pd.Series("ok", index=out.index)) == "ok")
        & ~out["implausible_record"].fillna(False)
    ).fillna(False)

    # sensitivity_eligible: flights that are rotation-eligible OR would be
    # rotation-eligible except that diversion status is unknown (NA).
    # These are labeled explicitly as unknown-diversion cases and kept
    # separate from the strict rotation_eligible cohort.
    out["sensitivity_eligible"] = (
        out["rotation_eligible"]
        | (
            out["valid_tail_number"].fillna(False)
            & out["valid_crs_dep_time"].fillna(False)
            & out.get("has_utc_timestamps", pd.Series(False, index=out.index)).fillna(False)
            & out["valid_arr_time"].fillna(False)
            & out["is_cancelled"].eq(False).fillna(False)
            & out["is_diverted"].isna()  # diversion status UNKNOWN, not confirmed diverted
            & ~out["dep_time_inconsistent"].fillna(False)
            & ~out["arr_time_inconsistent"].fillna(False)
            & ~out["arrival_elapsed_inconsistent"].fillna(False)
            & (out.get("dep_dst_status", pd.Series("ok", index=out.index)) == "ok")
            & ~out["implausible_record"].fillna(False)
        )
    ).fillna(False)

    # Human-readable exclusion reason (first failing gate wins).
    def _reason(row: pd.Series) -> str:
        c = row.get("is_cancelled")
        if pd.notna(c) and bool(c):
            return "cancelled"
        d = row.get("is_diverted")
        if pd.notna(d) and bool(d):
            return "diverted"
        if "cancelled" in row.index and pd.isna(row.get("cancelled")):
            return "missing_cancellation_indicator"
        if "diverted" in row.index and pd.isna(row.get("diverted")):
            return "missing_diversion_indicator"
        if not bool(row.get("valid_tail_number", False)):
            return "missing_or_invalid_tail_number"
        if not bool(row.get("valid_crs_dep_time", False)):
            return "invalid_scheduled_departure_time"
        if not bool(row.get("tz_origin_known", False)):
            return "missing_origin_timezone"
        if not bool(row.get("has_utc_timestamps", False)):
            return "missing_actual_departure"
        if pd.isna(row.get("arr_dt_utc", pd.NaT)):
            return "missing_actual_arrival"
        if bool(row.get("dep_time_inconsistent", False)):
            return "inconsistent_departure_time"
        if bool(row.get("arr_time_inconsistent", False)):
            return "inconsistent_arrival_time"
        if bool(row.get("arrival_elapsed_inconsistent", False)):
            return "inconsistent_arrival_instant"
        if row.get("dep_dst_status", "ok") != "ok":
            return "dst_transition_departure"
        if bool(row.get("implausible_record", False)):
            return "implausible_value"
        return ""
    out["exclusion_reason"] = out.apply(_reason, axis=1)
    return out


# ---------------------------------------------------------------------------
# Step 5: duplicate detection
# ---------------------------------------------------------------------------
def detect_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flag and remove exact duplicate rows. Returns ``(kept, removed)``.

    Exact duplicates are a data error and are removed (logged to
    ``data/processed/duplicate_records.csv``). Identity duplicates (same flight
    day/carrier/number/route/time) are flagged but kept, because two physically
    distinct flights can rarely share those keys and removing them could lose
    legitimate records.
    """
    out = df.copy()
    if out.empty:
        out["duplicate_exact"] = pd.Series(dtype=bool)
        out["duplicate_identity"] = pd.Series(dtype=bool)
        return out, out.iloc[0:0]

    out["duplicate_exact"] = out.duplicated(keep="first")
    keep = ~out["duplicate_exact"]
    kept = out[keep].copy()
    removed = out[~keep].copy()

    identity_keys = [c for c in
                     ["flight_date", "reporting_airline", "flight_number",
                      "origin", "dest", "crs_dep_time"] if c in kept.columns]
    kept["duplicate_identity"] = (
        kept.duplicated(subset=identity_keys, keep=False) if identity_keys
        else False
    )
    kept["duplicate_exact"] = False
    return kept, removed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def clean(df: pd.DataFrame, airport_tz: dict | None = None):
    """Run the full cleaning pipeline on a raw BTS dataframe.

    Returns ``(cleaned, removed_duplicates, summary)``. Raises ``ValueError``
    if essential standardized columns are missing.
    """
    raw_rows = len(df)
    std, mapping = standardize_columns(df)
    require_columns(std.columns, ESSENTIAL_FIELDS)          # clear error if missing
    missing_required = [f for f in REQUIRED_FIELDS if f not in std.columns]
    coerced = coerce_types(std)
    with_ts = build_timestamps(coerced, airport_tz=airport_tz)
    flagged = validate_records(with_ts)
    kept, removed = detect_duplicates(flagged)
    cleaned = kept

    def _count_true(s) -> int:
        return int(s.fillna(False).sum()) if s is not None else 0

    summary = {
        "raw_rows": raw_rows,
        "rows_after_dedup": len(cleaned),
        "exact_duplicates_removed": raw_rows - len(cleaned),
        "columns_original_to_standardized": mapping,
        "missing_required_fields": missing_required,
        "n_rotation_eligible": _count_true(cleaned.get("rotation_eligible")),
        "n_cancelled": _count_true(cleaned.get("is_cancelled")),
        "n_diverted": _count_true(cleaned.get("is_diverted")),
        "n_missing_cancellation_indicator": int(cleaned["cancelled"].isna().sum()) if "cancelled" in cleaned else 0,
        "n_missing_diversion_indicator": int(cleaned["diverted"].isna().sum()) if "diverted" in cleaned else 0,
        "n_missing_tail": int((~cleaned["valid_tail_number"].fillna(False)).sum()) if "valid_tail_number" in cleaned else 0,
        "n_invalid_timestamp": int(cleaned["invalid_timestamp"].sum()) if "invalid_timestamp" in cleaned else 0,
        "n_missing_origin_timezone": int((~cleaned["tz_origin_known"].fillna(False)).sum()) if "tz_origin_known" in cleaned else 0,
        "n_has_utc_timestamps": _count_true(cleaned.get("has_utc_timestamps")),
        "n_dep_time_inconsistent": _count_true(cleaned.get("dep_time_inconsistent")),
        "n_arr_time_inconsistent": _count_true(cleaned.get("arr_time_inconsistent")),
        "n_arrival_elapsed_inconsistent": _count_true(cleaned.get("arrival_elapsed_inconsistent")),
        "n_dst_transition": int(cleaned["dep_dst_status"].isin(["ambiguous", "nonexistent"]).sum()) if "dep_dst_status" in cleaned else 0,
        "n_implausible": _count_true(cleaned.get("implausible_record")),
        "n_diversion_status_unknown": int(cleaned["diverted"].isna().sum()) if "diverted" in cleaned else 0,
        "n_sensitivity_eligible": int(cleaned["sensitivity_eligible"].fillna(False).sum()) if "sensitivity_eligible" in cleaned else 0,
    }
    cleaned.attrs["cleaning_summary"] = summary
    cleaned.attrs["column_mapping"] = mapping
    return cleaned, removed, summary


# ---------------------------------------------------------------------------
# Data-quality report
# ---------------------------------------------------------------------------
def generate_quality_report(raw_df, cleaned, summary, out_md, out_json, source=""):
    """Write a reproducible markdown + JSON data-quality report to results/."""
    os.makedirs(os.path.dirname(os.path.abspath(out_md)), exist_ok=True)

    report: dict[str, Any] = dict(summary)
    report["source"] = source
    report["raw_shape"] = list(raw_df.shape)
    report["cleaned_shape"] = list(cleaned.shape)
    report["cleaned_columns"] = list(cleaned.columns)

    important = [f for f in
                 ["tail_number", "crs_dep_time", "dep_time", "dep_delay",
                  "crs_arr_time", "arr_time", "arr_delay", "cancelled",
                  "diverted", "crs_elapsed_time", "actual_elapsed_time",
                  "distance"] if f in cleaned.columns]
    miss_after = {c: int(cleaned[c].isna().sum()) for c in important}
    raw_std, _ = standardize_columns(raw_df)
    miss_before = {c: int(raw_std[c].isna().sum()) for c in important
                    if c in raw_std.columns}
    report["missing_before"] = miss_before
    report["missing_after"] = miss_after

    if "exclusion_reason" in cleaned.columns:
        reasons = cleaned.loc[cleaned["exclusion_reason"] != "", "exclusion_reason"]
        report["exclusion_reasons"] = reasons.value_counts().to_dict()

    report["timestamp_validation"] = {
        "valid_crs_dep_time": int(cleaned["valid_crs_dep_time"].sum()) if "valid_crs_dep_time" in cleaned else 0,
        "has_utc_timestamps": int(summary.get("n_has_utc_timestamps", 0)),
        "valid_arr_time": int(cleaned["valid_arr_time"].sum()) if "valid_arr_time" in cleaned else 0,
        "dep_time_inconsistent": int(summary.get("n_dep_time_inconsistent", 0)),
        "arr_time_inconsistent": int(summary.get("n_arr_time_inconsistent", 0)),
        "arrival_elapsed_inconsistent": int(summary.get("n_arrival_elapsed_inconsistent", 0)),
        "dst_transition_departures": int(summary.get("n_dst_transition", 0)),
        "missing_origin_timezone": int(summary.get("n_missing_origin_timezone", 0)),
    }

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    lines = ["# Data-Quality Report\n",
             f"**Source file:** `{source or 'n/a'}`\n",
             "Generated automatically by `src/preprocessing.py`; reproducible "
             "from the raw input.\n",
             "## Shape\n",
             f"- Raw rows: {report['raw_shape'][0]:,}  |  columns: {report['raw_shape'][1]}",
             f"- Cleaned rows: {report['cleaned_shape'][0]:,}  |  columns: {report['cleaned_shape'][1]}",
             f"- Exact duplicate rows removed: {summary['exact_duplicates_removed']:,}",
             f"- Rotation-eligible rows: {summary['n_rotation_eligible']:,}\n",
             "## Records flagged (retained, not deleted)\n",
             f"- Cancelled: {summary['n_cancelled']:,}",
             f"- Diverted: {summary['n_diverted']:,}",
             f"- Missing cancellation indicator: {summary['n_missing_cancellation_indicator']:,}",
             f"- Missing diversion indicator: {summary['n_missing_diversion_indicator']:,}",
             f"- Missing/invalid tail number: {summary['n_missing_tail']:,}",
             f"- Invalid scheduled departure time: {summary['n_invalid_timestamp']:,}",
             f"- Missing origin timezone: {summary['n_missing_origin_timezone']:,}",
             f"- DST-transition departures (ambiguous/nonexistent): {summary['n_dst_transition']:,}",
             f"- Implausible values: {summary['n_implausible']:,}\n",
             "## Timestamp consistency (non-circular checks)\n",
             f"- Dep time vs crs_dep+dep_delay mismatch: {summary['n_dep_time_inconsistent']:,}",
             f"- Arr time vs crs_arr+arr_delay mismatch: {summary['n_arr_time_inconsistent']:,}",
             f"- Arrival instant vs raw arr_time mismatch: {summary['n_arrival_elapsed_inconsistent']:,}",
             f"- Rows with UTC timestamps: {summary['n_has_utc_timestamps']:,}\n",
             "## Missing values for important fields\n",
             "| Field | Before | After |", "|---|---:|---:|"]
    for c in important:
        lines.append(f"| {c} | {miss_before.get(c, 'n/a')} | {miss_after.get(c, 'n/a')} |")
    lines.append("")
    if report.get("exclusion_reasons"):
        lines += ["## Exclusion reasons (retained records)\n", "| Reason | Count |", "|---|---:|"]
        for reason, n in report["exclusion_reasons"].items():
            lines.append(f"| {reason} | {n:,} |")
        lines.append("")
    if summary.get("missing_required_fields"):
        lines += ["## Missing required fields\n",
                  "These standardized fields were not found in the raw file: "
                  f"{', '.join(summary['missing_required_fields'])}\n"]
    lines += ["## Limitations\n",
              "- Departure UTC instants come from the ORIGIN timezone; arrival "
              "UTC instants come from departure instant + elapsed time (a "
              "duration), so they are correct across time zones. The "
              "destination-local arrival CLOCK remains the raw `arr_time` field.",
              "- UTC conversion needs the airport's IANA timezone. Airports not "
              "in `data/airport_timezones.csv` are flagged "
              "`missing_origin_timezone` and not converted (no timezone is "
              "invented). Extend the map with `scripts/build_airport_timezones.py`.",
              "- Departures that fall in a daylight-saving gap (nonexistent) or "
              "overlap (ambiguous) get a best-effort UTC (fold=0), are flagged, "
              "and are excluded from `rotation_eligible` (kept, not dropped).",
              "- Delay-cause fields exist only from June 2003 and are "
              "self-reported/incomplete; they are supplementary, not a weather proxy.",
              "- Scheduled airport flight volume (the congestion proxy) is added "
              "later by the weather/congestion module.\n"]
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clean BTS flight records and write a data-quality report."
    )
    parser.add_argument("path", nargs="?", help="Path to the raw BTS CSV file.")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--nrows", type=int, default=None,
                        help="Process only the first N rows (sample month, etc.).")
    parser.add_argument("--out-dir", default="data/processed")
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--tz-csv", default=None,
                        help="Optional CSV (iata,timezone) overriding the bundled "
                             "airport timezone map.")
    args = parser.parse_args(argv)

    from src.data_ingestion import find_raw_file, load_raw
    from src.airport_timezones import load_airport_tz
    path = args.path or find_raw_file(args.raw_dir)
    if not path:
        print(f"No BTS CSV found in '{args.raw_dir}'. Place a downloaded month in "
              f"data/raw/ first.")
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(args.results_dir, exist_ok=True)

    airport_tz = load_airport_tz(args.tz_csv)
    print(f"Loading {path} ...")
    raw = load_raw(path, nrows=args.nrows)
    print(f"  raw rows: {len(raw):,}   airport tz map size: {len(airport_tz):,}")

    cleaned, removed, summary = clean(raw, airport_tz=airport_tz)
    print(f"  cleaned rows: {len(cleaned):,}  "
          f"(removed {summary['exact_duplicates_removed']:,} exact duplicates)")
    print(f"  with UTC timestamps: {summary['n_has_utc_timestamps']:,}   "
          f"rotation-eligible: {summary['n_rotation_eligible']:,}")

    clean_path = os.path.join(args.out_dir, "flights_clean.csv")
    eligible_path = os.path.join(args.out_dir, "flights_rotation_eligible.csv")
    dup_path = os.path.join(args.out_dir, "duplicate_records.csv")
    map_path = os.path.join(args.out_dir, "column_mapping.json")
    report_md = os.path.join(args.results_dir, "data_quality_report.md")
    report_json = os.path.join(args.results_dir, "data_quality_summary.json")

    cleaned.to_csv(clean_path, index=False)
    if "rotation_eligible" in cleaned.columns:
        cleaned[cleaned["rotation_eligible"]].to_csv(eligible_path, index=False)
    removed.to_csv(dup_path, index=False)
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(summary["columns_original_to_standardized"], f, indent=2)

    generate_quality_report(raw, cleaned, summary, report_md, report_json, source=path)
    print(f"  cleaned dataset: {clean_path}")
    print(f"  rotation-eligible subset: {eligible_path}")
    print(f"  duplicate audit log: {dup_path}")
    print(f"  column mapping: {map_path}")
    print(f"  report: {report_md}")
    print(f"  report (json): {report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
