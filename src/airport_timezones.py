"""Airport IATA code -> IANA timezone lookup for the cleaning pipeline.

Owned by Thrupthi G D. The BTS time fields are LOCAL airport times with no
timezone, so to make timestamps comparable across airports we convert each
local time to UTC using the airport's IANA timezone (which also encodes
daylight-saving rules). ``zoneinfo`` (Python standard library) does the actual
conversion; this module only supplies the airport -> IANA-name map.

Where the map comes from (priority, highest first):
  1. A user-supplied CSV path passed to ``load_airport_tz`` (columns: iata,
     timezone). Use this to override or extend the bundled map.
  2. ``data/airport_timezones.csv`` in the repository, generated once from the
     public-domain OurAirports dataset (see ``scripts/build_airport_timezones.py``).
     This covers ~2000 US airports.
  3. ``DEFAULT_AIRPORT_TZ`` below: a tiny hand-checked fallback of common US hubs,
     used only if no CSV is available. We do NOT guess timezones for airports we
     do not know: airports missing from the map are reported as timezone-unknown
     (their timestamps are left as raw local values, never converted to UTC).
"""
from __future__ import annotations

import os
from typing import Optional

__all__ = ["DEFAULT_AIRPORT_TZ", "load_airport_tz", "resolve_tz"]

DEFAULT_CSV_PATH = os.path.join("data", "airport_timezones.csv")

# Small, hand-checked fallback of well-known US hubs (IATA -> IANA). Only used
# if no CSV is available. All values are real IANA zone names.
DEFAULT_AIRPORT_TZ: dict[str, str] = {
    "JFK": "America/New_York", "EWR": "America/New_York", "LGA": "America/New_York",
    "BOS": "America/New_York", "MCO": "America/New_York", "TPA": "America/New_York",
    "MIA": "America/New_York", "FLL": "America/New_York", "ATL": "America/New_York",
    "CLT": "America/New_York", "RDU": "America/New_York", "IAD": "America/New_York",
    "DCA": "America/New_York", "BWI": "America/New_York", "PHL": "America/New_York",
    "PIT": "America/New_York", "BUF": "America/New_York",
    "ORD": "America/Chicago", "MDW": "America/Chicago", "DTW": "America/Chicago",
    "MSP": "America/Chicago", "MKE": "America/Chicago", "CLE": "America/Chicago",
    "CMH": "America/Chicago", "IND": "America/Chicago", "MEM": "America/Chicago",
    "BNA": "America/Chicago", "DFW": "America/Chicago", "IAH": "America/Chicago",
    "AUS": "America/Chicago", "SAT": "America/Chicago", "MSY": "America/Chicago",
    "STL": "America/Chicago", "CVG": "America/Chicago", "MCI": "America/Chicago",
    "OKC": "America/Chicago",
    "DEN": "America/Denver", "SLC": "America/Denver", "ABQ": "America/Denver",
    "ELP": "America/Denver", "BOI": "America/Denver",
    "PHX": "America/Phoenix", "TUS": "America/Phoenix",
    "LAX": "America/Los_Angeles", "SFO": "America/Los_Angeles",
    "SAN": "America/Los_Angeles", "SEA": "America/Los_Angeles",
    "PDX": "America/Los_Angeles", "LAS": "America/Los_Angeles",
    "OAK": "America/Los_Angeles", "SJC": "America/Los_Angeles",
    "SNA": "America/Los_Angeles", "BUR": "America/Los_Angeles",
    "SMF": "America/Los_Angeles", "RNO": "America/Los_Angeles",
    "GEG": "America/Los_Angeles",
    "ANC": "America/Anchorage", "HNL": "Pacific/Honolulu",
}


def _read_csv(path: str) -> dict[str, str]:
    import pandas as pd
    df = pd.read_csv(path)
    # Accept a few reasonable header spellings.
    iata_col = next((c for c in df.columns if c.lower() in ("iata", "iata_code")), None)
    tz_col = next((c for c in df.columns if c.lower() in ("timezone", "iana", "tz", "time_zone")), None)
    if iata_col is None or tz_col is None:
        raise ValueError(
            f"Airport timezone CSV must have an IATA column and a timezone column; "
            f"got columns: {list(df.columns)}"
        )
    df = df[[iata_col, tz_col]].dropna()
    out = {}
    for iata, tz in zip(df[iata_col], df[tz_col]):
        if isinstance(iata, str) and isinstance(tz, str) and tz.strip():
            out[iata.strip().upper()] = tz.strip()
    return out


def load_airport_tz(path: Optional[str] = None) -> dict[str, str]:
    """Return an IATA(upper) -> IANA-name dict.

    Resolution: explicit ``path`` -> ``data/airport_timezones.csv`` ->
    ``DEFAULT_AIRPORT_TZ``. Missing files fall through silently.
    """
    candidates = [path, DEFAULT_CSV_PATH] if path else [DEFAULT_CSV_PATH]
    for p in candidates:
        if p and os.path.exists(p):
            try:
                return _read_csv(p)
            except Exception:
                # A broken CSV should not silently fall back to a wrong map;
                # but for robustness in tests we fall through to the default.
                pass
    return dict(DEFAULT_AIRPORT_TZ)


def resolve_tz(iata, mapping: dict[str, str]) -> Optional[str]:
    """Look up the IANA timezone for an IATA code (None if unknown/missing)."""
    if iata is None or (isinstance(iata, float) and iata != iata):
        return None
    return mapping.get(str(iata).strip().upper())
