# Person 3 → Person 4 Handoff Document

**From:** Dhanush R (Person 3) — Weather/Congestion Integration & Network Analysis
**To:** Anagha Kaushik (Person 4) — Statistical Modelling and Recovery Analysis
**Date:** November 13, 2026
**Branch:** `feature/person-3-external-features`

---

## Instructions for Person 4 – Anagha

This section is written directly for you. Read it before touching any dataset.

### Step 1 — Get the data

The generated CSVs are **too large for Git** (~1 GB combined) so they are gitignored.
Regenerate them from the committed code:

```bash
source .venv/bin/activate
python -m scripts.run_weather --skip-download   # uses cached raw GSOD (already on disk)
python -m scripts.run_congestion
python -m scripts.build_external_features
python -m scripts.run_network_analysis
```

Or ask Dhanush to share `external_features.csv` directly (388 MB).

### Step 2 — Load and join

**Recommended (robust to file reordering):**

```python
import pandas as pd

KEY = ["flight_date", "reporting_airline", "flight_number",
       "origin", "dest", "crs_dep_dt_utc"]

flights = pd.read_csv("data/processed/flights_rotation_eligible.csv")
ext     = pd.read_csv("data/processed/external_features.csv")

merged = flights.merge(ext, on=KEY, how="left")
assert len(merged) == len(flights),          "row count changed — stop"
assert merged[KEY].duplicated().sum() == 0,  "duplicate key — stop"
```

**Alternative (simpler, works while flights file is frozen):**

```python
flights = pd.read_csv("data/processed/flights_rotation_eligible.csv")
flights["flight_id"] = range(len(flights))   # add this before merging
ext = pd.read_csv("data/processed/external_features.csv")
merged = flights.merge(ext, on="flight_id", how="left")
assert len(merged) == len(flights)
```

### Step 3 — Know which features you can use

| Feature | Use in retrospective regression? | Use as real-time predictor? |
|---|---|---|
| `dep_count_origin_1h` | ✅ Yes | ✅ Yes — based on published schedule |
| `dep_count_origin_3h` | ✅ Yes | ✅ Yes |
| `arr_count_dest_1h` | ✅ Yes | ✅ Yes |
| `arr_count_dest_3h` | ✅ Yes | ✅ Yes |
| `total_traffic_origin_1h` | ✅ Yes | ✅ Yes |
| `temp_c_prev` | ✅ Yes — as weather control | ❌ No — GSOD not available in real-time |
| `dewp_c_prev` | ✅ Yes | ❌ No |
| `visibility_km_prev` | ✅ Yes | ❌ No |
| `wind_ms_prev` | ✅ Yes | ❌ No |
| `gust_ms_prev` | ✅ Yes — but 31% null, see §4 | ❌ No |
| `prcp_mm_prev` | ✅ Yes | ❌ No |
| `betweenness_centrality` (node metrics) | ✅ Yes | ✅ Yes — static network property |
| `pagerank` (node metrics) | ✅ Yes | ✅ Yes |

### Step 4 — Know the limitations

1. **4 months only:** Jan, Jun, Jul, Aug 2024. Winter underrepresented.
2. **Congestion ~4% undercounted:** Cancelled flights excluded from congestion index but were on the published schedule. No leakage, but slight bias.
3. **Weather is prior-day:** `weather_date = flight_date − 1`. Valid for controlling for weather regime; not same-day weather at departure time.
4. **gust_ms_prev 31% null:** NOAA GSOD 999.9 = "not reported." Do not impute as zero — use `wind_ms_prev` as a proxy instead, or add a binary `gust_reported` indicator.
5. **Network metrics are descriptive:** High betweenness centrality at DFW does not mean DFW causes more delays. Use as a covariate, not as causal evidence.

---

## 1. What Was Delivered

### Primary deliverable

| File | Description | Rows | Cols | Size |
|---|---|---|---|---|
| `data/processed/external_features.csv` | **11 features per flight** (5 congestion + 6 weather) | 2,319,468 | 19 | 388 MB |
| `data/processed/congestion_features.csv` | Congestion only | 2,319,468 | 13 | 257 MB |
| `data/processed/weather_features.csv` | Weather only | 2,319,468 | 14 | 332 MB |
| `data/processed/network_node_metrics.csv` | Per-airport network metrics | 343 | 8 | 14 KB |
| `data/processed/network_edge_list.csv` | Directed route edge list | 6,367 | 5 | 158 KB |
| `data/weather/airport_station_map.csv` | Airport → NOAA station mapping | 343 | 10 | 31 KB |
| `data/weather/weather_obs.csv` | Raw GSOD observations (committed) | 249,698 | 10 | 28 MB |

All large CSVs are gitignored. See §Reproduction Commands to regenerate.

---

## 2. Schema: external_features.csv (19 columns)

### Identity / join columns (not features)

| Column | Type | Description |
|---|---|---|
| `flight_id` | int | 0-based row index in `flights_rotation_eligible.csv`. Fragile if file reordered — prefer natural key join. |
| `flight_date` | date | Scheduled departure date (origin local calendar) |
| `reporting_airline` | str | IATA carrier code |
| `flight_number` | int | BTS flight number |
| `origin` | str | Origin airport IATA code |
| `dest` | str | Destination airport IATA code |
| `crs_dep_dt_utc` | datetime UTC | Scheduled departure instant in UTC |
| `weather_date` | date | GSOD observation date = `flight_date − 1` |

### Congestion features (safe as predictors)

All use **published schedule (CRS)** times only. Available at prediction time.
Window: `[t − W, t)` — open right, so the target flight never counts itself.

| Column | Description | Missing |
|---|---|---|
| `dep_count_origin_1h` | Scheduled departures from origin, trailing 1h | 0 |
| `dep_count_origin_3h` | Scheduled departures from origin, trailing 3h | 0 |
| `arr_count_dest_1h` | Scheduled arrivals at destination, trailing 1h | 0 |
| `arr_count_dest_3h` | Scheduled arrivals at destination, trailing 3h | 0 |
| `total_traffic_origin_1h` | Deps + arrs at origin, trailing 1h | 0 |

### Weather features (retrospective controls only)

All use **prior-day GSOD** (`weather_date = flight_date − 1`).
Not available in real-time — see §Weather Methodology.

| Column | Unit | Description | Missing |
|---|---|---|---|
| `temp_c_prev` | °C | Mean temperature, prior day | 0.08% |
| `dewp_c_prev` | °C | Mean dew point, prior day | 0.09% |
| `visibility_km_prev` | km | Mean visibility, prior day | 0.08% |
| `wind_ms_prev` | m/s | Mean wind speed, prior day | 0.08% |
| `gust_ms_prev` | m/s | Max wind gust, prior day — **31% null = not reported** | 31.2% |
| `prcp_mm_prev` | mm | Total precipitation, prior day | 0.32% |

---

## 3. Network Metrics: network_node_metrics.csv (8 columns)

| Column | Description |
|---|---|
| `iata` | Airport IATA code (join key) |
| `in_degree` | Number of airports that fly to this one |
| `out_degree` | Number of airports this one flies to |
| `in_strength` | Total flights arriving |
| `out_strength` | Total flights departing |
| `total_strength` | Total movements |
| `betweenness_centrality` | Fraction of network shortest paths through this airport |
| `pagerank` | Importance weighted by incoming traffic from other important airports |

**⚠️ Causal disclaimer:** These are descriptive topology metrics only. DFW having the highest betweenness (0.179) does not mean DFW causes more delays. Use as model covariates, not as causal claims.

Join to flights: `network_node_metrics["iata"] == flights["origin"]`

---

## 4. ⚠️ Weather Methodology: Retrospective Only

### The four temporal layers

| Layer | Example | Notes |
|---|---|---|
| **Observation date** | `2024-07-31` | Day weather was measured, 00:00Z–23:59Z |
| **Publication date** | `~2024-08-03` | NCEI README: "normally available a few days after" |
| **Flight date** | `2024-08-01` | Local departure date at origin |
| **This study's analysis date** | November 2026 | We have all historical data |

We use `weather_date = flight_date − 1` (July 31 for August 1 flights). This avoids same-day post-departure observations. Valid for retrospective regression.

For a real-time system, July 31 GSOD would not be available on August 1 morning. These features would need to be replaced with a data source that has confirmed real-time availability.

### Appropriate use in your model

✅ Use `temp_c_prev`, `wind_ms_prev`, `prcp_mm_prev` etc. as **environmental confounders** in a retrospective regression estimating the association between arrival delay and subsequent departure delay.

❌ Do not describe them as "weather conditions at prediction time" or "real-time weather features" in your thesis or paper.

---

## 5. Gust Feature Warning

`gust_ms_prev` is **31.2% null** because NOAA GSOD uses `999.9` to mean "insufficient data to report a valid gust value" — not "no gust occurred."

**Do not impute as zero.** Options:
- Drop the column; use `wind_ms_prev` as a wind proxy (99.9% complete)
- Create `gust_reported = gust_ms_prev.notna().astype(int)` as a binary indicator
- Apply explicit imputation and document it in your methods section

---

## 6. Flight Coverage

| Month | Flights |
|---|---|
| January 2024 | 518,378 (22%) |
| June 2024 | 594,973 (26%) |
| July 2024 | 607,467 (26%) |
| August 2024 | 598,650 (26%) |
| **Total** | **2,319,468** |

Winter underrepresented (January only). Treat seasonal generalisations with caution.

---

## 7. Reproduction Commands

```bash
source .venv/bin/activate
python -m scripts.build_station_map        # station matching
python -m scripts.run_weather --skip-download  # weather features (raw already cached)
python -m scripts.run_congestion           # congestion features
python -m scripts.build_external_features  # integrated dataset
python -m scripts.run_network_analysis     # network metrics + plots
python -m pytest tests/ -v                 # 232 passed, 2 skipped, 0 failed
```

All scripts accept `--help` for path overrides. No hardcoded paths.

---

## 8. Version Information

| Component | Value |
|---|---|
| Branch | `feature/person-3-external-features` |
| Commit | `08339ca` |
| Source flights | `flights_rotation_eligible.csv` (Git LFS, commit `ae1c81a`) |
| NOAA GSOD downloaded | November 7, 2026 |
| Python | 3.14.6 |
| pandas | 3.1.1 |
| networkx | 3.4.2 |
