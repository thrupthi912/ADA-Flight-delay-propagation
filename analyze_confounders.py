import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

DATA = Path.home() / "Downloads/flight_data/flights_clean.csv"
OUT = Path("results/confounder_analysis")
OUT.mkdir(parents=True, exist_ok=True)

# Read only the columns required for this analysis
cols = [
    "flight_date", "tail_number", "origin", "dest",
    "crs_dep_dt_utc", "crs_arr_dt_utc",
    "dep_delay", "arr_delay", "crs_elapsed_time",
    "cancelled", "diverted"
]

print("Loading flight data...")
df = pd.read_csv(DATA, usecols=cols, low_memory=False)

for c in ["crs_dep_dt_utc", "crs_arr_dt_utc"]:
    df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")

for c in ["dep_delay", "arr_delay", "crs_elapsed_time"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")

df = df.dropna(subset=["tail_number", "crs_dep_dt_utc", "crs_arr_dt_utc"])
df = df.sort_values(["tail_number", "crs_dep_dt_utc"])

# Aircraft turnaround buffer analysis
df["next_scheduled_dep"] = df.groupby("tail_number")["crs_dep_dt_utc"].shift(-1)
df["next_origin"] = df.groupby("tail_number")["origin"].shift(-1)
df["next_dep_delay"] = df.groupby("tail_number")["dep_delay"].shift(-1)
df["next_arr_delay"] = df.groupby("tail_number")["arr_delay"].shift(-1)

df["ground_time_min"] = (
    (df["next_scheduled_dep"] - df["crs_arr_dt_utc"]).dt.total_seconds() / 60
)
df["buffer_remaining_min"] = df["ground_time_min"] - df["arr_delay"]

pairs = df[
    (df["dest"] == df["next_origin"]) &
    df["ground_time_min"].between(0, 720) &
    df["arr_delay"].notna() &
    df["next_dep_delay"].notna()
].copy()

pairs["buffer_remaining_min"] = pairs["ground_time_min"] - pairs["arr_delay"]
pairs["buffer_consumed"] = pairs["arr_delay"] > pairs["ground_time_min"]

pairs.to_csv(OUT / "turnaround_buffer_analysis.csv", index=False)

# Airport congestion proxy: scheduled departures per airport-hour
flights = df.dropna(subset=["origin", "crs_dep_dt_utc"]).copy()
flights["hour"] = flights["crs_dep_dt_utc"].dt.floor("h")

congestion = (
    flights.groupby(["origin", "hour"])
    .size()
    .rename("scheduled_departures_per_airport_hour")
    .reset_index()
)

congestion.to_csv(OUT / "airport_congestion_proxy.csv", index=False)

# Summary
summary = {
    "valid_same_airport_rotation_pairs": len(pairs),
    "mean_scheduled_ground_time_min": pairs["ground_time_min"].mean(),
    "median_scheduled_ground_time_min": pairs["ground_time_min"].median(),
    "fraction_incoming_delay_exceeds_ground_time": pairs["buffer_consumed"].mean(),
    "airport_hour_groups": len(congestion),
    "mean_scheduled_departures_per_airport_hour": congestion["scheduled_departures_per_airport_hour"].mean(),
}

pd.Series(summary, name="value").to_csv(OUT / "summary.csv")

# Plot 1: turnaround buffer
plt.figure(figsize=(8, 5))
pairs["buffer_remaining_min"].clip(-120, 240).hist(bins=60)
plt.xlabel("Remaining turnaround buffer (minutes)")
plt.ylabel("Number of flight connections")
plt.title("Turnaround Buffer Remaining After Incoming Delay")
plt.tight_layout()
plt.savefig(OUT / "turnaround_buffer.png", dpi=160)
plt.close()

# Plot 2: airport-hour departure volume
plt.figure(figsize=(8, 5))
congestion["scheduled_departures_per_airport_hour"].clip(
    upper=congestion["scheduled_departures_per_airport_hour"].quantile(0.99)
).hist(bins=40)
plt.xlabel("Scheduled departures per airport-hour")
plt.ylabel("Number of airport-hours")
plt.title("Distribution of Airport Departure Congestion Proxy")
plt.tight_layout()
plt.savefig(OUT / "airport_congestion_proxy.png", dpi=160)
plt.close()

print("\nDONE. Summary:")
for k, v in summary.items():
    print(f"{k}: {v}")
print(f"\nFiles saved in: {OUT}")
