"""pytest configuration: make the project root importable as `src.*`.

Ensures `from src.data_ingestion import ...` works no matter where pytest is
invoked from, and exposes the test fixture paths.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
SAMPLE_FLIGHTS = os.path.join(FIXTURES, "sample_flights.csv")
SAMPLE_OLD_NAMES = os.path.join(FIXTURES, "sample_old_names.csv")
DST_FLIGHTS = os.path.join(FIXTURES, "dst_flights.csv")
# Default airport -> IANA timezone map shipped with the repo.
AIRPORT_TZ_CSV = os.path.join(ROOT, "data", "airport_timezones.csv")
