import pandas as pd
from pathlib import Path

flights_path = Path.home() / "Downloads/flight_data/flights_clean.csv"

flights = pd.read_csv(flights_path, usecols=["origin", "dest"])
airport_codes = set(flights["origin"].dropna()) | set(flights["dest"].dropna())

airports = pd.read_csv("airports_reference.csv", low_memory=False)
stations = pd.read_csv("ghcnh-station-list.csv", low_memory=False)

# Match US airports by IATA code
airports = airports[
    (airports["iso_country"] == "US") &
    (airports["iata_code"].isin(airport_codes))
].copy()

# Keep airports with coordinates and ICAO codes
airports = airports.dropna(subset=["latitude_deg", "longitude_deg", "icao_code"])

stations = stations[stations["ISO_CODE"] == "US"].dropna(
    subset=["LATITUDE", "LONGITUDE"]
).copy()

print("Flight airport codes:", len(airport_codes))
print("Matched US airports:", len(airports))
print("NOAA US stations:", len(stations))
print("\nMatched airports:")
print(airports[["iata_code", "icao_code", "name"]].to_string(index=False))

airports.to_csv("matched_us_airports.csv", index=False)
stations.to_csv("us_ghcnh_stations.csv", index=False)
