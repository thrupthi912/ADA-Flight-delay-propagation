"""Generate the synthetic BTS-schema test fixtures.

Hand-writing CSV rows with several blank fields is error-prone (one wrong comma
misaligns every later column). This script builds each fixture from explicit
column dictionaries, so the field alignment is always correct, then writes the
CSV. Run after editing fixture data::

    python -m tests.fixtures.build_fixtures

These fixtures are NOT real flight data; they are tiny, hand-checked inputs.
"""
from __future__ import annotations

import os

import pandas as pd

COLS = [
    "Year", "Month", "DayofMonth", "DayOfWeek", "FlightDate",
    "Reporting_Airline", "Tail_Number", "Flight_Number_Reporting_Airline",
    "Origin", "Dest", "CRSDepTime", "DepTime", "DepDelay", "DepDelayMinutes",
    "CRSArrTime", "ArrTime", "ArrDelay", "ArrDelayMinutes",
    "Cancelled", "CancellationCode", "Diverted",
    "CRSElapsedTime", "ActualElapsedTime", "AirTime", "Distance",
]

# Row 1: JFK -> ATL (same Eastern zone), dep 22:30 -> arr next day 00:40
R1 = dict(Year=2024, Month=1, DayofMonth=5, DayOfWeek=5, FlightDate=20240105,
          Reporting_Airline="AA", Tail_Number="N123AB",
          Flight_Number_Reporting_Airline=100, Origin="JFK", Dest="ATL",
          CRSDepTime="2230", DepTime="2235", DepDelay="5", DepDelayMinutes="5",
          CRSArrTime="0030", ArrTime="0040", ArrDelay="10", ArrDelayMinutes="10",
          Cancelled="0", CancellationCode=None, Diverted="0",
          CRSElapsedTime="120", ActualElapsedTime="125", AirTime="110", Distance="760")

# Row 2: cancelled (ATL -> JFK, Eastern)
R2 = dict(R1)
R2.update(FlightDate=20240105, Reporting_Airline="DL", Tail_Number="N456CD",
          Flight_Number_Reporting_Airline=200, Origin="ATL", Dest="JFK",
          CRSDepTime="1400", DepTime=None, DepDelay=None, DepDelayMinutes=None,
          CRSArrTime="1600", ArrTime=None, ArrDelay=None, ArrDelayMinutes=None,
          Cancelled="1", CancellationCode="A", Diverted="0",
          CRSElapsedTime="120", ActualElapsedTime=None, AirTime=None, Distance="760")

# Row 3: diverted (SFO -> SEA, both Pacific)
R3 = dict(R1)
R3.update(Year=2024, Month=1, DayofMonth=6, DayOfWeek=6, FlightDate=20240106,
          Reporting_Airline="UA", Tail_Number="N789EF",
          Flight_Number_Reporting_Airline=300, Origin="SFO", Dest="SEA",
          CRSDepTime="0900", DepTime="0910", DepDelay="10", DepDelayMinutes="10",
          CRSArrTime="1100", ArrTime=None, ArrDelay=None, ArrDelayMinutes=None,
          Cancelled="0", CancellationCode=None, Diverted="1",
          CRSElapsedTime="120", ActualElapsedTime=None, AirTime=None, Distance="680")

# Row 5: missing tail number (DFW -> DEN, cross Mountain/Central)
R5 = dict(R1)
R5.update(Year=2024, Month=1, DayofMonth=7, DayOfWeek=7, FlightDate=20240107,
          Reporting_Airline="WN", Tail_Number=None,
          Flight_Number_Reporting_Airline=400, Origin="DFW", Dest="DEN",
          CRSDepTime="1200", DepTime="1205", DepDelay="5", DepDelayMinutes="5",
          CRSArrTime="1230", ArrTime="1235", ArrDelay="5", ArrDelayMinutes="5",
          Cancelled="0", CancellationCode=None, Diverted="0",
          CRSElapsedTime="90", ActualElapsedTime="90", AirTime="80", Distance="640")

# Row 6: invalid scheduled departure time 2500 (LAX -> PHX)
R6 = dict(R1)
R6.update(Year=2024, Month=1, DayofMonth=8, DayOfWeek=1, FlightDate=20240108,
          Reporting_Airline="AA", Tail_Number="N000X",
          Flight_Number_Reporting_Airline=500, Origin="LAX", Dest="PHX",
          CRSDepTime="2500", DepTime="1200", DepDelay="0", DepDelayMinutes="0",
          CRSArrTime="1300", ArrTime="1305", ArrDelay="5", ArrDelayMinutes="5",
          Cancelled="0", CancellationCode=None, Diverted="0",
          CRSElapsedTime="60", ActualElapsedTime="65", AirTime="55", Distance="370")

# Row 7: implausible distance 0 (BOS -> JFK, Eastern)
R7 = dict(R1)
R7.update(Year=2024, Month=1, DayofMonth=9, DayOfWeek=2, FlightDate=20240109,
          Reporting_Airline="B6", Tail_Number="N111ZZ",
          Flight_Number_Reporting_Airline=600, Origin="BOS", Dest="JFK",
          CRSDepTime="0800", DepTime="0805", DepDelay="5", DepDelayMinutes="5",
          CRSArrTime="0900", ArrTime="0910", ArrDelay="10", ArrDelayMinutes="10",
          Cancelled="0", CancellationCode=None, Diverted="0",
          CRSElapsedTime="60", ActualElapsedTime="65", AirTime="55", Distance="0")

# Row 8: arrival instant disagrees with raw arr_time (MIA -> ATL, Eastern)
R8 = dict(R1)
R8.update(Year=2024, Month=1, DayofMonth=10, DayOfWeek=3, FlightDate=20240110,
          Reporting_Airline="DL", Tail_Number="N222YY",
          Flight_Number_Reporting_Airline=700, Origin="MIA", Dest="ATL",
          CRSDepTime="1200", DepTime="1205", DepDelay="5", DepDelayMinutes="5",
          CRSArrTime="1400", ArrTime="1430", ArrDelay="30", ArrDelayMinutes="30",
          Cancelled="0", CancellationCode=None, Diverted="0",
          CRSElapsedTime="120", ActualElapsedTime="125", AirTime="115", Distance="600")

# Row 9: cross-timezone, consistent, eligible (JFK Eastern -> LAX Pacific)
R9 = dict(R1)
R9.update(Year=2024, Month=1, DayofMonth=11, DayOfWeek=4, FlightDate=20240111,
          Reporting_Airline="AA", Tail_Number="N333AB",
          Flight_Number_Reporting_Airline=800, Origin="JFK", Dest="LAX",
          CRSDepTime="0800", DepTime="0805", DepDelay="5", DepDelayMinutes="5",
          CRSArrTime="1100", ArrTime="1110", ArrDelay="10", ArrDelayMinutes="10",
          Cancelled="0", CancellationCode=None, Diverted="0",
          CRSElapsedTime="360", ActualElapsedTime="365", AirTime="345", Distance="2475")

# DST fixture rows (JFK -> ATL, Eastern)
# Spring-forward 2024-03-10: 02:30 does not exist at JFK
R_SPRING = dict(R1)
R_SPRING.update(Year=2024, Month=3, DayofMonth=10, DayOfWeek=7, FlightDate=20240310,
                CRSDepTime="0230", DepTime="0230", DepDelay="0", DepDelayMinutes="0",
                CRSArrTime="0430", ArrTime="0430", ArrDelay="0", ArrDelayMinutes="0",
                CRSElapsedTime="120", ActualElapsedTime="120", AirTime="110",
                Distance="760")
# Fall-back 2024-11-03: 01:30 occurs twice at JFK
R_FALL = dict(R1)
R_FALL.update(Year=2024, Month=11, DayofMonth=3, DayOfWeek=7, FlightDate=20241103,
              CRSDepTime="0130", DepTime="0130", DepDelay="0", DepDelayMinutes="0",
              CRSArrTime="0330", ArrTime="0330", ArrDelay="0", ArrDelayMinutes="0",
              CRSElapsedTime="120", ActualElapsedTime="120", AirTime="110",
              Distance="760")


def _write(records, name):
    df = pd.DataFrame(records, columns=COLS)
    out = os.path.join(os.path.dirname(__file__), name)
    df.to_csv(out, index=False, na_rep="")
    assert list(df.columns) == COLS and df.shape[1] == 25
    print(f"wrote {df.shape[0]} rows x {df.shape[1]} cols -> {out}")


def main():
    _write([R1, R2, R3, dict(R1), R5, R6, R7, R8, R9], "sample_flights.csv")
    _write([R_SPRING, R_FALL], "dst_flights.csv")


if __name__ == "__main__":
    main()
