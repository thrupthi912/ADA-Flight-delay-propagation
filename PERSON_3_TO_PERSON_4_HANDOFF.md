# Person 3 → Person 4 Handoff Package

**From:** Dhanush R (Person 3) — External Features and Airport-Network Analysis
**To:** Anagha Kaushik (Person 4) — Statistical Modelling and Recovery Analysis
**Branch:** `feature/person-3-external-features`
**Date:** November 13, 2026

---

## A. Project and Responsibility Overview

### Overall objective

This project estimates the **causal association between an inbound flight's arrival delay and the subsequent departure delay of the next flight operated by the same aircraft** (aircraft rotation), while controlling for operational and environmental confounders including weather and airport congestion.

### Team responsibilities

| Person | Name | Responsibility |
|---|---|---|
| 1 | Thrupthi G D | BTS data ingestion, cleaning, UTC timestamp reconstruction, data-quality reports |
| 2 | Amulya | Aircraft rotation pair linking — connecting inbound and outbound legs on the same tail number |
| **3** | **Dhanush R** | **External features: NOAA weather, airport congestion, airport-network analysis** |
| 4 | Anagha Kaushik | Statistical modelling, regression, confidence intervals, recovery analysis, robustness |

### Where Person 3's work ends and Person 4's begins

Person 3 delivers:
- A cleaned, joined external-features dataset ready to merge with the modelling dataset
- Airport-level weather conditions (prior-day, retrospective)
- Airport-level congestion counts (from published schedule)
- Airport network topology metrics

Person 4 is responsible for:
- Obtaining Amulya's rotation-pair dataset (inbound arrival delay paired with outbound departure delay)
- Joining external features onto those pairs
- Specifying and estimating the regression model
- Interpreting results causally
- Robustness checks and sensitivity analysis

Person 3 does **not** build the rotation pairs and does **not** specify the model.

---

## B. Generated Datasets

All large CSVs are **gitignored** because of size. See §I for regeneration commands.

### Dataset summary

| Dataset | Rows | Columns | Size | Purpose |
|---|---:|---:|---:|---|
| `data/processed/external_features.csv` | 2,319,468 | 19 | 388 MB | **Primary deliverable** — 11 features per flight |
| `data/processed/congestion_features.csv` | 2,319,468 | 13 | 257 MB | Congestion features only |
| `data/processed/weather_features.csv` | 2,319,468 | 14 | 332 MB | Weather features only |
| `data/weather/weather_obs.csv` | 249,698 | 10 | 28 MB | Raw GSOD daily observations (committed to git) |
| `data/processed/network_node_metrics.csv` | 343 | 8 | 14 KB | Per-airport network metrics |
| `data/processed/network_edge_list.csv` | 6,367 | 5 | 158 KB | Directed route edge list |
| `data/weather/airport_station_map.csv` | 343 | 10 | 31 KB | Airport → NOAA station mapping (committed) |

### Date and month coverage (all feature datasets)

| Month | Flight rows |
|---|---|
| January 2024 | 518,378 |
| June 2024 | 594,973 |
| July 2024 | 607,467 |
| August 2024 | 598,650 |
| **Total** | **2,319,468** |

---

### Schema: `external_features.csv` (primary deliverable)

This is the only file you need to load. It contains all 11 features.

**Identity / join columns (not features — do not use as model inputs):**

| Column | Type | Description |
|---|---|---|
| `flight_id` | int64 | 0-based row index in `flights_rotation_eligible.csv`. Fragile if file is reordered. Use natural key join instead — see §C. |
| `flight_date` | string YYYY-MM-DD | Scheduled departure date, origin local calendar |
| `reporting_airline` | string | IATA carrier code (e.g. `AA`, `DL`) |
| `flight_number` | float64 | BTS flight number |
| `origin` | string | Origin airport IATA code |
| `dest` | string | Destination airport IATA code |
| `crs_dep_dt_utc` | string datetime+UTC | Scheduled departure instant in UTC |
| `weather_date` | string YYYY-MM-DD | GSOD observation date = `flight_date − 1` always |

**Congestion features (5 columns — safe for predictive modelling):**

| Column | Type | Missing | Mean | Max |
|---|---|---|---|---|
| `dep_count_origin_1h` | float64 | 0 (0.0%) | 20.5 | 105 |
| `dep_count_origin_3h` | float64 | 0 (0.0%) | 57.8 | 239 |
| `arr_count_dest_1h` | float64 | 0 (0.0%) | 19.7 | 103 |
| `arr_count_dest_3h` | float64 | 0 (0.0%) | 55.8 | 230 |
| `total_traffic_origin_1h` | float64 | 0 (0.0%) | 39.4 | 197 |

**Weather features (6 columns — retrospective controls only, NOT real-time predictors):**

| Column | Unit | Missing | Mean | Min | Max |
|---|---|---|---|---|---|
| `temp_c_prev` | °C | 1,886 (0.08%) | 21.1 | −41.5 | 41.7 |
| `dewp_c_prev` | °C | 2,020 (0.09%) | 12.7 | −38.9 | 27.8 |
| `visibility_km_prev` | km | 1,889 (0.08%) | 15.3 | 0.3 | 24.6 |
| `wind_ms_prev` | m/s | 1,904 (0.08%) | 3.6 | 0.0 | 18.2 |
| `gust_ms_prev` | m/s | **724,562 (31.2%)** | 12.4 | 5.1 | 40.2 |
| `prcp_mm_prev` | mm | 7,345 (0.32%) | 3.0 | 0.0 | 280.9 |

---

### Schema: `network_node_metrics.csv`

Join to flight data on `network_node_metrics["iata"] == flights["origin"]`.

| Column | Type | Description |
|---|---|---|
| `iata` | string | Airport IATA code (join key) |
| `in_degree` | int | Number of distinct airports flying to this one |
| `out_degree` | int | Number of distinct airports this one flies to |
| `in_strength` | int | Total flights arriving at this airport |
| `out_strength` | int | Total flights departing from this airport |
| `total_strength` | int | in_strength + out_strength |
| `betweenness_centrality` | float | Fraction of network shortest paths passing through this airport (normalised) |
| `pagerank` | float | Relative importance by incoming traffic from other important airports |

---

## C. Joining External Features with the Modelling Dataset

### Why use the natural key, not `flight_id`

`flight_id` is a 0-based row counter assigned during feature generation. It is correct for the current file on disk, but it would become incorrect if `flights_rotation_eligible.csv` is ever regenerated or reordered. The natural key — a 6-column combination — is globally unique and stable regardless of file order.

**Verified:** 0 duplicate natural keys in a 200,000-row sample of `external_features.csv`.

### Preferred join — using the natural key

```python
import pandas as pd

# ── Load source flight data ───────────────────────────────────────────────
flights = pd.read_csv("data/processed/flights_rotation_eligible.csv")

# ── Load external features ────────────────────────────────────────────────
ext = pd.read_csv("data/processed/external_features.csv")

# ── Normalize types before joining ───────────────────────────────────────
# Both files should already have matching types, but be explicit:
for df in [flights, ext]:
    df["flight_date"]     = df["flight_date"].astype(str)
    df["crs_dep_dt_utc"]  = df["crs_dep_dt_utc"].astype(str)
    df["flight_number"]   = pd.to_numeric(df["flight_number"], errors="coerce")

NATURAL_KEY = [
    "flight_date",
    "reporting_airline",
    "flight_number",
    "origin",
    "dest",
    "crs_dep_dt_utc",
]

# ── Join ──────────────────────────────────────────────────────────────────
merged = flights.merge(ext, on=NATURAL_KEY, how="left")

# ── Validate ──────────────────────────────────────────────────────────────
assert len(merged) == len(flights), (
    f"Row count changed: {len(flights):,} → {len(merged):,}. "
    "Likely a duplicate key in external_features."
)
assert merged[NATURAL_KEY].duplicated().sum() == 0, "Duplicate natural keys found."

unmatched = merged["dep_count_origin_1h"].isna().sum()
print(f"Rows with no external features: {unmatched:,} "
      f"({100 * unmatched / len(merged):.2f}%)")

print(f"Merged dataset: {len(merged):,} rows × {len(merged.columns)} columns")
```

**Expected output:**
```
Rows with no external features: 0 (0.00%)
Merged dataset: 2,319,468 rows × 112 columns
```

### Fallback join — using `flight_id` (simpler but order-dependent)

Use this only if the files have not been regenerated since the current dataset was built.

```python
flights = pd.read_csv("data/processed/flights_rotation_eligible.csv")
flights["flight_id"] = range(len(flights))   # must add this column

ext = pd.read_csv("data/processed/external_features.csv")
merged = flights.merge(ext, on="flight_id", how="left")

assert len(merged) == len(flights), "Row count changed — stop."
```

> **Warning:** If `flights_rotation_eligible.csv` is ever regenerated by Thrupthi and you re-use an old `external_features.csv`, the `flight_id` join will silently produce wrong matches. The natural key join does not have this problem.

### If you have Amulya's rotation-pair dataset

Amulya's output will be a dataset of rotation pairs: each row links an inbound flight to the subsequent outbound flight on the same tail number. That dataset will have its own join key (likely a combination of tail number, inbound arrival, and outbound departure). Merge external features onto the **outbound flight** of each pair using the natural key above.

```python
# Assuming rotation_pairs has outbound flight identity columns:
rotation_pairs = pd.read_csv("data/processed/rotation_pairs.csv")  # Amulya's output
ext = pd.read_csv("data/processed/external_features.csv")

# Rename ext columns to avoid collision with inbound flight columns
ext_outbound = ext.rename(columns={c: f"outbound_{c}" for c in ext.columns
                                    if c not in NATURAL_KEY})

# Join external features onto the outbound leg
pairs_with_features = rotation_pairs.merge(
    ext_outbound,
    left_on=["outbound_flight_date", "outbound_reporting_airline",
             "outbound_flight_number", "outbound_origin",
             "outbound_dest", "outbound_crs_dep_dt_utc"],
    right_on=NATURAL_KEY,
    how="left"
)
```

Adjust column names to match Amulya's actual schema.

---

## D. Weather Feature Documentation

### Source

**NOAA Global Summary of the Day (GSOD)** from the NCEI data repository.
URL pattern: `https://www.ncei.noaa.gov/data/global-summary-of-the-day/access/{year}/{USAF:06d}{WBAN:05d}.csv`

686 files downloaded: 343 airports × 2 years (2023 and 2024).

### Airport-to-station matching

343 airports in the flight dataset were matched to NOAA stations:

| Method | Count | Description |
|---|---|---|
| ICAO primary | 334 | Exact match on ICAO code between airport and ISD catalog |
| Extended ICAO | 8 | US-territory airports (Puerto Rico, USVI, Guam, Samoa) matched via known ICAO |
| Geographic fallback | 1 | XWA (Williston Basin, ND) — nearest station within 200 km (Watford City, 63 km away) |

Station mapping file: `data/weather/airport_station_map.csv`

### Unit conversions applied

| GSOD field | Original unit | Converted to | Formula |
|---|---|---|---|
| `TEMP`, `DEWP` | °F | °C | `(x − 32) × 5/9` |
| `VISIB` | statute miles | km | `x × 1.60934` |
| `WDSP`, `GUST` | knots | m/s | `x × 0.514444` |
| `PRCP` | inches | mm | `x × 25.4` |

### Previous-day lag

`weather_date = flight_date − 1 day`

This means the weather for a flight on `2024-08-01` uses GSOD observations from `2024-07-31`. Verified for all 2,319,468 rows.

### Why prior-day, not same-day

GSOD daily summaries are averages and totals computed over the **entire GMT calendar day (00:00Z–23:59Z)**. A same-day summary for `2024-08-01` includes observations from 14:00, 20:00, and 23:00 — all after most morning departures. Using same-day GSOD would introduce post-departure observations.

The prior day's summary is complete before any departure begins on `flight_date`.

### GSOD sentinel values replaced with NaN

| Field | Sentinel | Meaning |
|---|---|---|
| `TEMP`, `DEWP` | 9999.9 | Missing observation |
| `VISIB`, `WDSP`, `GUST` | 999.9 | Missing / not reported |
| `PRCP` | 99.99 | Missing (note: also appears on genuinely dry days at some stations) |

### ⚠️ RETROSPECTIVE ONLY — publication latency

> Per the official NCEI GSOD documentation (`readme.txt`): *"The latest daily summary data are normally available a few days after the date-time of the observations used in the daily summaries."*

This means the `2024-07-31` GSOD file was not available in the NCEI database until approximately `2024-08-03`. It was **not** accessible at the time of an `2024-08-01` flight departure.

These features are valid for a **retrospective regression study** (which this is — data downloaded in November 2026 for 2024 flights). They are **not** valid as real-time predictors.

> **Do not describe `temp_c_prev`, `wind_ms_prev`, or any other `_prev` feature as "weather conditions at prediction time" or "real-time weather" in your thesis, paper, or model description.** They represent the prior-day weather regime used as a retrospective confounder control.

The code enforces this: calling `build_weather_features()` raises a `UserWarning` with this exact text every time.

### gust_ms_prev — 31% null, special handling required

`gust_ms_prev` is null for 724,562 rows (31.2%). This is **not** a data pipeline error.

The NOAA GSOD `GUST` field uses `999.9` to mean *"not reported / insufficient data"* — not "no gust occurred." The 31% reflects stations or days where gusts were not measured or reported.

**Do not impute `gust_ms_prev = NaN` as zero.** Options:
- Drop the column; use `wind_ms_prev` as a wind proxy (99.9% complete)
- Create a binary indicator: `gust_reported = gust_ms_prev.notna().astype(int)`
- Apply explicit model-aware imputation and document it in your methods section

---

## E. Congestion Feature Documentation

### What they measure

Each congestion feature counts the number of **other** scheduled flights at an airport in a trailing time window ending at the reference flight's own scheduled time. These are proxies for how busy the airport was scheduled to be — not actual movements.

### Timestamps used

**All congestion features use CRS (scheduled) UTC times only.** No actual departure times, arrival times, delays, or outcomes enter any calculation.

| Feature | Reference timestamp | Airport | Window |
|---|---|---|---|
| `dep_count_origin_1h` | `crs_dep_dt_utc` | origin | `[t − 1h, t)` |
| `dep_count_origin_3h` | `crs_dep_dt_utc` | origin | `[t − 3h, t)` |
| `arr_count_dest_1h` | `crs_arr_dt_utc`* | dest | `[t − 1h, t)` |
| `arr_count_dest_3h` | `crs_arr_dt_utc`* | dest | `[t − 3h, t)` |
| `total_traffic_origin_1h` | `crs_dep_dt_utc` | origin | `[t − 1h, t)` |

*`crs_arr_dt_utc` = scheduled arrival = `crs_dep_dt_utc + crs_elapsed_time`. Derived from scheduled times only.

`total_traffic_origin_1h = dep_count_origin_1h + scheduled arrivals at origin in [t−1h, t)`

### UTC alignment

All timestamps were converted to UTC by Thrupthi's preprocessing pipeline using IANA timezone data from the OurAirports dataset. The congestion algorithm uses tz-stripped `datetime64[us]` numpy arrays for efficiency. All comparisons are UTC-consistent.

### Window convention: `[t − W, t)` — open right

The trailing window is **closed on the left** (includes `t − W`) and **open on the right** (excludes `t` itself). This means:
- A flight scheduled at exactly `t − 1h` is counted (it was already scheduled when the reference flight departs)
- The reference flight itself is never counted in its own congestion score
- No future flights (scheduled after `t`) enter the count

### Target-flight exclusion

The reference flight's own `crs_dep_dt_utc` is the right boundary of the window. Because the right boundary is open (`< t`, not `≤ t`), the flight is excluded from its own congestion count. This is verified in `tests/test_congestion_features.py`.

### Background population and coverage

The congestion index is built from `flights_rotation_eligible.csv` — the strict rotation-eligible cohort. For the four months covered:

| Month | BTS source rows | Rotation-eligible | Coverage |
|---|---|---|---|
| January | 547,271 | 518,378 | 94.7% |
| June | 611,132 | 594,973 | 97.4% |
| July | 634,613 | 607,467 | 95.7% |
| August | 619,025 | 598,650 | 96.7% |
| **Total** | **2,412,041** | **2,319,468** | **96.2%** |

The ~4% difference is cancelled flights (~2–4% of source rows). Cancelled flights were on the published schedule at prediction time but were never operated. Excluding them causes a slight downward bias in congestion counts (the schedule was slightly busier than the index records). There is no data leakage — CRS times are fixed schedule data, not operational outcomes.

### Are these features safe for predictive modelling?

**Yes.** The airline schedule (CRS times) is published months in advance and is fully available at prediction time. These features do not use actual delays, actual departure times, or any post-departure information.

---

## F. Airport-Network Analysis

### Network definition

| Property | Value |
|---|---|
| Nodes | 343 US airports |
| Edges | 6,367 directed routes (origin → destination) |
| Edge weight | `flight_count` — total rotation-eligible flights on that route (Jan/Jun/Jul/Aug 2024) |
| Graph type | Directed weighted |
| Weakly connected components | 1 (fully connected) |
| Strongly connected components | 9 |
| Network density | 0.054 |

### Top airports by total traffic (actual verified data)

| Airport | In-strength | Out-strength | Total | Betweenness | PageRank |
|---|---|---|---|---|---|
| ATL | 109,884 | 112,363 | 222,247 | 0.093 | 0.042 |
| DFW | 105,096 | 105,887 | 210,983 | **0.179** | **0.048** |
| DEN | 104,114 | 104,456 | 208,570 | 0.177 | 0.047 |
| ORD | 90,558 | 91,425 | 181,983 | 0.105 | 0.036 |
| CLT | 69,777 | 71,132 | 140,909 | 0.040 | 0.026 |

### Metric definitions

**In/out-degree:** Number of distinct airports that fly to/from this airport.

**In/out-strength:** Total flight count on all incoming/outgoing routes. Reflects actual traffic volume.

**Betweenness centrality (normalised):** Fraction of all shortest paths in the network that pass through this airport. DFW (0.179) and DEN (0.177) have the highest scores, meaning they sit on more indirect routing paths than ATL despite having slightly less total traffic. This makes them structurally important connectors.

**PageRank (power iteration, weighted):** An airport scores higher if it receives traffic from other high-traffic airports. Sum equals 1.0 (verified).

### Busiest routes

| Route | Flights | Mean dep delay (min) |
|---|---|---|
| HNL→OGG | 3,911 | 3.2 |
| OGG→HNL | 3,909 | 2.6 |
| LAX→SFO | 3,591 | 10.4 |

### Output files

| File | Description |
|---|---|
| `data/processed/network_node_metrics.csv` | 343 rows × 8 cols — per-airport metrics |
| `data/processed/network_edge_list.csv` | 6,367 rows × 5 cols — directed routes with flight counts and mean delays |
| `reports/network_analysis_report.md` | Full descriptive report |
| `reports/network_degree_distribution.png` | In/out-degree histograms |
| `reports/network_top_hubs.png` | Top-20 airports by traffic and PageRank |
| `reports/network_route_frequency.png` | Top-25 busiest routes |

### ⚠️ Causal disclaimer

> **Network metrics are descriptive topology statistics, not causal estimates.** DFW having the highest betweenness centrality (0.179) does NOT mean DFW causes more delays. It means DFW sits on many shortest paths in the schedule network, which may correlate with delay propagation but requires causal analysis to establish. Use network metrics as regression covariates if your modelling design supports it — do not interpret them as direct causes.

### How to use network metrics in your model

```python
import pandas as pd

# Load network metrics
net = pd.read_csv("data/processed/network_node_metrics.csv")

# Join to your modelling dataset on the outbound flight's origin airport
# (or the inbound flight's destination, depending on your research question)
model_data = pd.read_csv("path/to/rotation_pairs.csv")  # Amulya's output

model_data = model_data.merge(
    net[["iata", "betweenness_centrality", "total_strength", "pagerank"]],
    left_on="outbound_origin",   # adjust to Amulya's column name
    right_on="iata",
    how="left"
).drop(columns="iata")
```

---

## G. Guidance for Statistical Modelling

### Feature classification

#### ✅ Suitable for retrospective regression modelling

| Feature | Type | Rationale |
|---|---|---|
| `dep_count_origin_1h` | Congestion | Based on published schedule; no future info |
| `dep_count_origin_3h` | Congestion | Same |
| `arr_count_dest_1h` | Congestion | Based on published schedule |
| `arr_count_dest_3h` | Congestion | Same |
| `total_traffic_origin_1h` | Congestion | Sum of deps + arrs at origin |
| `temp_c_prev` | Weather control | Valid for retrospective regression as environmental confounder |
| `dewp_c_prev` | Weather control | Same |
| `visibility_km_prev` | Weather control | Same |
| `wind_ms_prev` | Weather control | Same |
| `prcp_mm_prev` | Weather control | Same |
| `betweenness_centrality` | Network | Static topology; valid covariate if design supports |
| `total_strength` | Network | Static topology |

#### ⚠️ Requires care

| Feature | Issue | Recommendation |
|---|---|---|
| `gust_ms_prev` | 31.2% null — not-reported, not zero | Use `wind_ms_prev` as proxy, or add binary indicator |
| `prcp_mm_prev` | 0.32% null; some dry days also appear as null | Consider binary rain indicator `prcp_mm_prev > 0` |

#### ❌ Not validated for real-time prediction

All `_prev` weather features. They are retrospective-only due to GSOD publication latency (~3 days). Do not describe them as real-time features in any publication.

### Person 4's modelling responsibilities

The following are entirely Anagha's scope — not covered by this handoff:

- Specifying the regression model (OLS, mixed effects, IV, etc.)
- Deciding which features to include after checking for multicollinearity
- Estimating and reporting confidence intervals
- Persistence and recovery analysis
- Robustness checks
- Causal interpretation

---

## H. Data Limitations

1. **4-month coverage only.** January, June, July, August 2024. The other 8 months (Feb–May, Sep–Dec) are absent because the BTS DIVERTED column is missing in those months' downloads, preventing the strict rotation-eligible cohort from being built. Regenerating them requires re-downloading raw BTS CSVs.

2. **Congestion ~4% undercounting.** Cancelled flights were on the published schedule but are excluded from the rotation-eligible cohort. This introduces a small downward bias in congestion counts (not a leakage issue).

3. **Weather features are prior-day, not same-day.** `weather_date = flight_date − 1`. They capture the ambient weather regime, not specific conditions at departure time.

4. **GSOD publication latency.** Prior-day GSOD is typically available ~3 days after observations. These features are not usable in a real-time system.

5. **gust_ms_prev is 31% null.** NOAA GSOD 999.9 sentinel = not reported. Do not impute as zero.

6. **XWA geographic fallback.** Williston Basin International Airport (XWA) is matched to Watford City ASOS station, 63 km away. Its weather observations are approximate.

7. **flight_id is order-dependent.** Safe for the current file state; prefer natural key join for robustness.

8. **Network metrics are descriptive.** Based on 4 months only; different months may show different topology. All metrics are from the rotation-eligible cohort.

9. **Dataset transfer.** The large CSVs (external_features.csv, congestion_features.csv, weather_features.csv) are gitignored and stored only on Dhanush's local machine. They must be transferred via shared storage (OneDrive, Google Drive) or regenerated from the committed code.

---

## I. Reproduction Instructions

### Environment setup

```bash
git clone https://github.com/Dhanush-R-Gowda/ADA-Flight-delay-propagation.git
cd ADA-Flight-delay-propagation
git checkout feature/person-3-external-features
git lfs pull          # downloads flights_rotation_eligible.csv (~1.3 GB)

python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Generate all Person 3 outputs

```bash
# Step 1: Station matching (ISD catalog already in git — skip download)
python -m scripts.build_station_map

# Step 2: Weather features
# Option A — use pre-downloaded raw GSOD files (if data/weather/raw/ exists):
python -m scripts.run_weather --skip-download
# Option B — full re-download from NCEI (~22 MB, ~5–10 min):
python -m scripts.run_weather

# Step 3: Congestion features (~3 min)
python -m scripts.run_congestion

# Step 4: Integrated external features (~2 min)
python -m scripts.build_external_features

# Step 5: Airport-network analysis (~2 min)
python -m scripts.run_network_analysis
```

### Run tests

```bash
python -m pytest tests/ -v
# Expected: 232 passed, 2 skipped, 0 failed
```

The 2 skipped tests require raw BTS monthly files (`data/raw/`) which are gitignored.

### Dataset transfer

The generated CSV files are **only on Dhanush's local machine**. Transfer options:
- Share via OneDrive (team already uses this per `.gitignore` comments)
- Share via Google Drive or similar
- Anagha regenerates using the commands above (takes ~15–30 min depending on network speed for GSOD download)

---

## J. Handoff Checklist for Anagha

Use this list to verify you have everything needed before starting modelling.

- [ ] Cloned `feature/person-3-external-features` branch
- [ ] Ran `git lfs pull` and obtained `flights_rotation_eligible.csv` (2,319,468 rows)
- [ ] Obtained or regenerated `external_features.csv` (2,319,468 rows × 19 cols)
- [ ] Loaded `external_features.csv` and verified row count matches `flights_rotation_eligible.csv`
- [ ] Performed natural-key join and confirmed no row multiplication
- [ ] Confirmed `weather_date = flight_date − 1` for all rows
- [ ] Noted that weather features are retrospective-only (not real-time predictors)
- [ ] Noted that `gust_ms_prev` is 31% null — do not impute as zero
- [ ] Loaded `network_node_metrics.csv` and joined on `iata == origin`
- [ ] Received Amulya's rotation-pair dataset (separate dependency — confirm with Amulya)
- [ ] Read §G (modelling guidance) for feature classification
- [ ] Read §H (limitations) before writing methods section

---

*All statistics in this document are from actual files on disk — no values are invented. Last verified: November 13, 2026.*
