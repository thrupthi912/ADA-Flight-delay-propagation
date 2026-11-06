import pandas as pd
import numpy as np

airports = pd.read_csv("matched_us_airports.csv")
stations = pd.read_csv("us_ghcnh_stations.csv")

def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(
        np.radians, [lat1, lon1, lat2, lon2]
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2)**2 + (
        np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2)**2
    )
    return 6371 * 2 * np.arcsin(np.sqrt(a))

results = []

for _, airport in airports.iterrows():
    exact = stations[
        stations["ICAO"].astype(str).str.strip()
        == str(airport["icao_code"]).strip()
    ]

    if not exact.empty:
        candidates = exact.copy()
        method = "Exact ICAO"
    else:
        candidates = stations.copy()
        method = "Nearest station"

    candidates["distance_km"] = haversine(
        airport["latitude_deg"],
        airport["longitude_deg"],
        candidates["LATITUDE"],
        candidates["LONGITUDE"]
    )

    best = candidates.sort_values("distance_km").iloc[0]

    results.append({
        "iata_code": airport["iata_code"],
        "airport_name": airport["name"],
        "station_id": best["GHCN_ID"],
        "station_name": best["NAME"],
        "distance_km": round(best["distance_km"], 2),
        "match_method": method
    })

result = pd.DataFrame(results)
result.to_csv("airport_weather_station_matches.csv", index=False)

print("Airports matched:", len(result))
print("Exact ICAO matches:", (result["match_method"] == "Exact ICAO").sum())
print("Nearest-station matches:", (result["match_method"] == "Nearest station").sum())
print("\nLargest distances:")
print(result.nlargest(15, "distance_km").to_string(index=False))
print("\nSaved airport_weather_station_matches.csv")
