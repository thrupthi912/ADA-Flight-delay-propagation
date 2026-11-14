# Person 3 — Final Integrity Audit Report

**Author:** Dhanush R (Person 3)
**Branch:** `feature/person-3-external-features`
**Last audit:** November 13, 2026
**Purpose:** Pre-handoff integrity verification for Person 4 (Anagha)

---

## 1. Flight ID Integrity

### How `flight_id` is generated

`flight_id` is a **0-based integer row index** assigned by reading `flights_rotation_eligible.csv` sequentially with no sorting. The congestion and weather pipelines both read the same file in the same order, increment a counter per chunk, and assign identical row numbers.

### Is this fragile?

**Conditionally.** The row-index approach is reliable as long as `flights_rotation_eligible.csv` is never re-sorted or re-written. If Thrupthi re-runs `scripts/run_pipeline.py`, the output row order could change, invalidating all existing `flight_id` assignments.

### Mitigating factor: a stable natural key also exists

`external_features.csv` already contains all columns needed for a natural join:

| Column | Present in external_features.csv |
|---|---|
| `flight_date` | ✅ |
| `reporting_airline` | ✅ |
| `flight_number` | ✅ |
| `origin` | ✅ |
| `dest` | ✅ |
| `crs_dep_dt_utc` | ✅ |

This 6-column combination is globally unique (verified: 0 duplicates in 500k-row sample). **Anagha can join on the natural key even if the file is reordered.**

### Verified correctness of current flight_id

Spot-check of 5 rows — `external_features` row N matches `flights_rotation_eligible` row N exactly:

| flight_id | ext origin | flights origin | Match |
|---|---|---|---|
| 0 | MSP | MSP | ✅ |
| 1 | CVG | CVG | ✅ |
| 2 | LGA | LGA | ✅ |
| 3 | GSP | GSP | ✅ |
| 4 | DTW | DTW | ✅ |

Both `dep_count_origin_1h` and `temp_c_prev` are identical whether joined by `flight_id` or by natural key (verified on 10,000-row sample).

### Recommendation for Anagha

Use the **natural key join** — it is robust to any future reordering:

```python
import pandas as pd

KEY = ["flight_date", "reporting_airline", "flight_number",
       "origin", "dest", "crs_dep_dt_utc"]

flights = pd.read_csv("data/processed/flights_rotation_eligible.csv")
ext = pd.read_csv("data/processed/external_features.csv")
merged = flights.merge(ext, on=KEY, how="left")

assert len(merged) == len(flights),   "row multiplication — stop"
assert merged[KEY].duplicated().sum() == 0, "duplicate key — stop"
```

The `flight_id` join is simpler but requires `flights_rotation_eligible.csv` to be in its current order:

```python
flights = pd.read_csv("data/processed/flights_rotation_eligible.csv")
flights["flight_id"] = range(len(flights))   # must add this column
ext = pd.read_csv("data/processed/external_features.csv")
merged = flights.merge(ext, on="flight_id", how="left")
```

---

## 2. Congestion Prediction-Time Validity

### Prediction timestamp

The congestion features use `crs_dep_dt_utc` — the **published scheduled departure time** in UTC. This is set by the airline schedule months in advance.

### Does excluding cancelled flights introduce leakage?

No, but it does introduce a small bias:

- Cancelled flights **were on the published schedule** at prediction time
- They **were not actually operated** (no runway consumption)
- Excluding them causes a slight undercount (~2–4%) of published scheduled movements
- This is **not leakage** — it is a methodological limitation

The congestion features use **CRS scheduled times only**, never actual departure times or cancellation outcomes. No future information enters any calculation.

### Is the 96% coverage claim valid?

Yes — verified from `reports/data_quality_summary.json`:

| Month | Source rows | Rotation-eligible | Coverage | Cancelled |
|---|---|---|---|---|
| January | 547,271 | 518,378 | 94.7% | 20,389 (3.7%) |
| June | 611,132 | 594,973 | 97.4% | 7,876 (1.3%) |
| July | 634,613 | 607,467 | 95.7% | 18,372 (2.9%) |
| August | 619,025 | 598,650 | 96.7% | 12,714 (2.1%) |
| **Total** | **2,412,041** | **2,319,468** | **96.2%** | — |

**Conclusion:** The 96% coverage is valid. Congestion features represent the published schedule with ~4% undercounting from cancelled flights. For a retrospective study this is acceptable. The limitation is documented in `CONGESTION_POPULATION_NOTE` in the source code.

---

## 3. Weather Feature Temporal Interpretation

### Four timestamps that must be distinguished

| Timestamp | Definition | Example |
|---|---|---|
| **Observation date** | Calendar day when weather was measured | `2024-07-31` |
| **Publication date** | When NCEI makes the GSOD file available | ~`2024-08-03` (+3 days, per NCEI README) |
| **Flight date** | Local departure date at origin airport | `2024-08-01` |
| **Prediction timestamp** | When a prediction is made | Retrospective: any time in 2026 |

### For this study (retrospective)

`weather_date = flight_date − 1` avoids using same-day data that includes post-departure observations. The prior day's observations are complete (ended at 23:59Z) before any departure begins on `flight_date`. No data snooping.

### For real-time use (NOT this study)

The July 31 GSOD file would not be in the NCEI database on August 1 morning. These features are therefore **not usable for real-time prediction**.

### Enforcement in code

- `RETROSPECTIVE_ONLY = True` constant exported from `src/weather_features.py`
- `build_weather_features()` raises a `UserWarning` on every call
- Warning text quotes the NCEI README publication-latency statement
- Module docstring has an explicit `⚠️ RETROSPECTIVE STUDY ONLY` section

### Are these features suitable for Anagha's regression?

**Yes** — as prior-day weather controls in a retrospective regression model. The features capture the weather regime that surrounded the flight, appropriate for controlling for environmental confounding. They must not be presented as real-time predictors.

---

## 4. Airport-Network Responsibility

**Confirmed: Person 3's responsibility.**

From README:
> `Dhanush R | Weather/congestion integration and **airport-network analysis** (NOAA/FAA joins, coverage, **network metrics**)`

Person 4 (Anagha) owns: *"Statistical modelling and recovery analysis"* — not network topology.

No other teammate has committed any network analysis code (confirmed via `git log --all`).

**Person 3 delivers:** network metrics CSV files. Person 4 may choose to use betweenness centrality or PageRank as model features — that is modelling, not duplication of Person 3's work.

**Outputs delivered:**
- `data/processed/network_node_metrics.csv` — per-airport metrics (343 rows × 8 cols)
- `data/processed/network_edge_list.csv` — route edge list (6,367 rows × 5 cols)
- `reports/network_analysis_report.md` — descriptive report with causal disclaimer
- 3 PNG visualisations in `reports/`

---

## 5. Final Dataset Validation

| Dataset | Path | Rows | Cols | Size | Readable | Key unique |
|---|---|---|---|---|---|---|
| `external_features.csv` | `data/processed/` | 2,319,468 | 19 | 388 MB | ✅ | ✅ 0 dups |
| `congestion_features.csv` | `data/processed/` | 2,319,468 | 13 | 257 MB | ✅ | ✅ 0 dups |
| `weather_features.csv` | `data/processed/` | 2,319,468 | 14 | 332 MB | ✅ | ✅ 0 dups |
| `weather_obs.csv` | `data/weather/` | 249,698 | 10 | 28 MB | ✅ | ✅ 0 dups |
| `network_node_metrics.csv` | `data/processed/` | 343 | 8 | 14 KB | ✅ | ✅ |
| `network_edge_list.csv` | `data/processed/` | 6,367 | 5 | 158 KB | ✅ | ✅ |
| `airport_station_map.csv` | `data/weather/` | 343 | 10 | 31 KB | ✅ | ✅ |

### Feature missingness (external_features.csv)

| Feature | Missing | % | Explanation |
|---|---|---|---|
| `dep_count_origin_1h` | 0 | 0.000% | — |
| `dep_count_origin_3h` | 0 | 0.000% | — |
| `arr_count_dest_1h` | 0 | 0.000% | — |
| `arr_count_dest_3h` | 0 | 0.000% | — |
| `total_traffic_origin_1h` | 0 | 0.000% | — |
| `temp_c_prev` | 1,886 | 0.081% | Genuine GSOD source gaps (10 airports, verified in raw files) |
| `dewp_c_prev` | 2,020 | 0.087% | Same |
| `visibility_km_prev` | 1,889 | 0.081% | Same |
| `wind_ms_prev` | 1,904 | 0.082% | Same |
| `gust_ms_prev` | 724,562 | **31.238%** | NOAA GSOD 999.9 sentinel = **not reported**, not zero |
| `prcp_mm_prev` | 7,345 | 0.317% | GSOD gaps + dry-day non-reporting convention |

---

## 6. Test Results

**Command:** `python -m pytest tests/ -v`
**Result: 232 passed, 2 skipped, 0 failed**

| Test file | Passed | Skipped | Failed | Coverage type |
|---|---|---|---|---|
| `test_audit_corrections.py` | 43 | 0 | 0 | Synthetic + **real-data** |
| `test_congestion_features.py` | 39 | 0 | 0 | Synthetic + **real-data** |
| `test_data_ingestion.py` | 10 | **2** | 0 | Synthetic (2 skip: need raw BTS files) |
| `test_external_features.py` | 28 | 0 | 0 | Synthetic + **real-data** |
| `test_preprocessing.py` | 37 | 0 | 0 | Synthetic |
| `test_station_matching.py` | 28 | 0 | 0 | Synthetic |
| `test_weather_features.py` | 40 | 0 | 0 | Synthetic + **real-data** |

The 2 skipped tests are `test_find_monthly_files_finds_all_12` and `test_aliases_cover_real_bts_columns` — both require raw BTS monthly CSV files which are gitignored. These are Thrupthi's tests, unrelated to Person 3.

---

## 7. Git Status

```
Branch: feature/person-3-external-features
Commit: 08339ca  feat: implement external weather and congestion features
Remote: origin/feature/person-3-external-features
```

Uncommitted working-tree changes (audit corrections — not yet committed):

| File | Change |
|---|---|
| `src/weather_features.py` | `RETROSPECTIVE_ONLY=True`, runtime `UserWarning`, corrected docstring |
| `src/congestion_features.py` | `CONGESTION_POPULATION_NOTE` constant |
| `src/network_analysis.py` | NEW — full network analysis module |
| `scripts/run_network_analysis.py` | NEW — CLI runner |
| `tests/test_audit_corrections.py` | NEW — 43 tests |
| `docs/person_3_handoff.md` | Prediction-time classification table added |
| `docs/external_features_dictionary.md` | Retrospective label, population note |
| `requirements.txt` | `networkx>=3.4`, `matplotlib>=3.8` added |
| `PERSON_3_FINAL_AUDIT_REPORT.md` | This file |
| `reports/network_*.png` (3 files) | Generated visualisations |
| `reports/network_analysis_report.md` | Generated report |

---

## 8. Remaining Limitations

1. **4-month coverage.** Only Jan, Jun, Jul, Aug 2024. The other 8 months require raw BTS re-download.
2. **Cancelled flights excluded from congestion index (~4%).** CRS times only — no leakage, but slight undercount of published schedule.
3. **Weather features retrospective only.** GSOD publication latency prevents real-time use.
4. **gust_ms_prev 31% null.** NOAA 999.9 = not reported. Do not impute as zero.
5. **XWA matched geographically to 63 km station.** Watford City ASOS, nearest available.
6. **Network metrics descriptive only.** High betweenness ≠ causes delays.

---

## 9. Exact Reproduction Commands

```bash
git clone https://github.com/Dhanush-R-Gowda/ADA-Flight-delay-propagation.git
cd ADA-Flight-delay-propagation
git checkout feature/person-3-external-features
git lfs pull                            # get flights_rotation_eligible.csv (1.3 GB)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m scripts.build_station_map    # station matching
python -m scripts.run_weather          # GSOD download + features (or --skip-download)
python -m scripts.run_congestion       # congestion features
python -m scripts.build_external_features  # integrated dataset
python -m scripts.run_network_analysis # airport network
python -m pytest tests/ -v             # full test suite
# Expected: 232 passed, 2 skipped, 0 failed
```
