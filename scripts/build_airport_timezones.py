"""Build data/airport_timezones.csv from the public-domain OurAirports dataset.

This is a one-time, reproducible generator (run by Thrupthi when refreshing the
airport -> IANA timezone map). It is NOT required to run the cleaning pipeline;
the pipeline just reads the produced ``data/airport_timezones.csv``.

Source data (public domain, OurAirports by David Megginson):
    https://ourairports.com/data/airports.csv
    (raw mirror: https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv)

OurAirports gives each airport's latitude/longitude but not its IANA timezone,
so we derive the timezone from the coordinates with ``timezonefinder``.

Usage (from the repository root)::

    pip install pandas timezonefinder
    python -m scripts.build_airport_timezones data/airport_timezones_raw.csv data/airport_timezones.csv
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from timezonefinder import TimezoneFinder


def build(raw_csv: str, out_csv: str) -> int:
    df = pd.read_csv(raw_csv)
    # Keep US airports that have an IATA code (commercial / larger fields).
    us = df[(df["iso_country"] == "US") & df["iata_code"].notna()].copy()
    tf = TimezoneFinder()

    def tz_of(lat: float, lon: float) -> str:
        try:
            return tf.timezone_at(lat=float(lat), lng=float(lon)) or ""
        except Exception:
            return ""

    us["timezone"] = [tz_of(lat, lon) for lat, lon in zip(us["latitude_deg"], us["longitude_deg"])]
    out = us[["iata_code", "icao_code", "name", "municipality", "iso_region", "timezone"]].copy()
    out.columns = ["iata", "icao", "name", "municipality", "region", "timezone"]
    out = out.dropna(subset=["iata"])
    out = out[out["timezone"] != ""]
    out["iata"] = out["iata"].str.upper()

    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    out.to_csv(out_csv, index=False)
    print(f"Wrote {len(out)} US airports with IATA codes to {out_csv}")
    return len(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw_csv", nargs="?", default="data/airport_timezones_raw.csv")
    ap.add_argument("out_csv", nargs="?", default="data/airport_timezones.csv")
    args = ap.parse_args()
    build(args.raw_csv, args.out_csv)
