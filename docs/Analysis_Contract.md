# Analysis Contract: Person 4 (Anagha), v1.1

**Project:** ADA Flight Delay Propagation
**Author:** Anagha Kaushik
**Last updated:** 15 Nov 2026
**Status:** results complete; team decisions recorded in section 14 (replies received 15 Nov 2026); items marked **[PENDING]** await a teammate's check; items marked **[TODO]** are filled in at hand-off.

Tags: **[CONFIRMED]** = from a teammate's document or my own verified check. **[PROPOSED]** = my choice, pending team agreement.

---

## 1. Research question and wording

How much of an inbound flight's arrival delay is associated with the departure delay of the **next flight flown by the same aircraft**, after adjusting for operational and environmental factors (scheduled turnaround, weather, airport congestion)?

**Wording rule [CONFIRMED by team reply]:** results are **adjusted associations**, not causal effects. Dhanush's handoff says "causal association"; this report uses "adjusted association" unless the team agrees a causal design is in scope.

---

## 2. Data sources and verification

| Item | File | Owner | Verified facts |
|---|---|---|---|
| Flights table | `flights_clean_recovered.csv` | Amulya / Thrupthi | 7,079,081 rows x 94 cols; index 0 to n-1; contains `arr_delay`, `dep_delay`, `rotation_eligible`, `diverted`, `diversion_status_inferred_from_arrival` **[CONFIRMED]** |
| Rotation pairs | `aircraft_rotation_pairs_2024_recovered.csv` | Amulya | 7,052,817 pairs; 6,645,970 `connection_valid = True`; 406,847 excluded; ids 0 to 7,079,080 **[CONFIRMED]** |
| External features | `external_features.csv` | Dhanush | 2,319,468 x 19; 0 duplicate natural keys; Jan, Jun, Jul, Aug 2024 only; `weather_date = flight_date - 1` for all rows **[CONFIRMED]** |
| Network | `network_node_metrics.csv`, `network_edge_list.csv` | Dhanush | 343 airports, 6,367 routes; independently rebuilt with 0 mismatches **[CONFIRMED]** |

**Id verification [CONFIRMED]:** pair row ids are zero-based positions in `flights_clean_recovered.csv` (Amulya). Checked on all pairs: tail number match 100%, incoming destination match 100%, next origin match 100%. The flights file was never sorted or filtered before id lookups.

**Do not join by id across tables.** Pair ids index a 7.08M-row table; Dhanush's `flight_id` indexes a 2.32M-row table. External features are attached **only** through the six-column natural key: `flight_date`, `reporting_airline`, `flight_number`, `origin`, `dest`, `crs_dep_dt_utc`.

Commit hashes (Amulya's branch, Dhanush's branch `feature/person-3-external-features`, mine): **[TODO]**

---

## 3. Unit of analysis

One **aircraft rotation pair**: an incoming flight and the next flight on the same `tail_number`. Pair key (`incoming_row_id`, `next_row_id`) is unique **[CONFIRMED]**. Each flight can appear in two pairs (as "next" in one and "incoming" in another), so rows are chained, not independent.

---

## 4. Variables

**Outcome:** `dep_delay` of the next flight (minutes). **Main exposure:** `arr_delay` of the incoming flight (minutes).

**Core features:** incoming arrival delay, scheduled ground time, month, day of week, departure hour (UTC) of the next flight, carrier.

**Congestion** (Dhanush; scheduled times only, safe at prediction time): `dep_count_origin_1h`, `dep_count_origin_3h`, `arr_count_dest_1h`, `arr_count_dest_3h`, `total_traffic_origin_1h`.

**Weather** (Dhanush; prior-day NOAA GSOD, **retrospective control only**): `temp_c_prev`, `dewp_c_prev`, `visibility_km_prev`, `wind_ms_prev`, `prcp_mm_prev`. `gust_ms_prev` excluded (see section 8).

**Network** (Dhanush): `betweenness_centrality`, `total_strength`, `pagerank`, degree and strength per airport. Used descriptively only (section 8).

**Features are attached to the NEXT (outbound) flight [CONFIRMED by team reply]:** next-flight conditions are the primary setup. Incoming-destination conditions may be explored later as a separate sensitivity experiment, provided they are available at prediction time; **not run here**.

---

## 5. Pair inclusion and exclusions [CONFIRMED]

| Step | Pairs | Removed |
|---|---|---|
| Rotation pairs (Amulya) | 7,052,817 | |
| `connection_valid = True` | 6,645,970 | 406,847 |
| Airport-consistent (incoming dest = next origin) | 6,533,629 | 112,341 (1.69%) |
| Both delays non-missing | 6,533,629 | 0 |
| Scheduled ground time <= 180 min | 4,854,175 | 1,679,454 |
| Next flight in Jan, Jun, Jul, Aug 2024 | **1,634,841** | |
| Matched to external features (natural key, `validate="many_to_one"`) | 1,634,841 (100% in every month) | 0 |

- **Ground-time cutoff [CONFIRMED by team reply]:** 180 min primary; sensitivity at 120, 360, 720, 1440 min. Amulya confirmed no upper bound exists in her validity rules, so a 5,161-minute gap can be marked valid. Rationale: beyond a few hours the aircraft has typically stayed overnight.
- **Airport mismatches:** 112,341 pairs marked valid had incoming destination different from next origin. Of these, 56,539 involve inferred diversions and 55,802 are not diverted (real chain gaps). All excluded. **Amulya confirmed** (15 Nov 2026) that she will add the `incoming_dest == next_origin` condition to the validity checks and asked us to keep excluding these 112,341 pairs. Cross-check: her modelling pipeline already excludes discontinuous pairs, since her training rows (5,226,896) plus test rows (1,306,733) equal 6,533,629, exactly our airport-consistent count.
- **Missing delay values:** none among the airport-consistent valid pairs (valid pairs require actual arrival and departure times), so no delay value is imputed and no pair is dropped for a missing delay. Recovery denominators are the late pairs (348,709 of 1,634,841).
- Only flights that actually operated are in the sample (cancelled flights are outside the analysis).
- Minimum scheduled ground time in the modelling set is 0 minutes; retained, noted as a data-quality caveat.
- Modelling set: **5,909 aircraft**.

---

## 6. Inferred labels

- `diversion_status_inferred_from_arrival` marks inferred diversion statuses (Amulya). Inferred labels are never described as directly observed.
- In the modelling set: **283 pairs (0.017%)** have an inferred incoming status; the next flight's status is observed for all 1,634,841 pairs.
- Recovery rate is 22.01% without them versus 22.02% with them: no material effect.
- The blank `diverted` pattern in other months should be verified before claiming those months rely on inferred statuses **[TODO]**.

---

## 7. Prediction time and interpretation

- **Congestion:** built from scheduled (CRS) times only; available at prediction time **[CONFIRMED]**.
- **Weather:** prior-day GSOD summaries, published about 3 days after the observation date. **Retrospective only.** Never described as real-time or "weather at prediction time" **[CONFIRMED]**.
- Weather differs from Amulya's earlier model (hourly observations within 120 min before arrival). Results are **separate experiments** and are not compared directly.
- The incoming arrival delay is known before the next scheduled departure for these pairs (overlapping actual flights are excluded in Amulya's validity rules). Timestamp-order check: **[TODO]**.

---

## 8. Missing-data policy

| Feature | Policy |
|---|---|
| `gust_ms_prev` (31.2% missing; 999.9 = not reported) | Never filled with 0. Excluded from main models. Tested with a "gust reported" flag: MAE change -0.004 (CI -0.013 to 0.004), no benefit. |
| `prcp_mm_prev` (0.32% missing; some dry days appear as missing) | Not filled with 0; handled by the model's native missing-value support. |
| Other weather (about 0.1% missing) | Kept; the model handles missing values natively. All 1,634,841 rows stay in every model (about 0.4% have some missing weather value), so core, weather and congestion models use **identical rows**. |
| `flight_number` missing in 1 row of external features | Logged; that row cannot be matched by the six-column key. |
| Network metrics | Computed on all four months (including test period) and constant per airport. Used **descriptively only**, not as predictors. |

---

## 9. Validation checks

- [x] Flights file has 7,079,081 rows; index 0 to n-1; unsorted.
- [x] Pair ids resolve correctly (tail, incoming dest, next origin: 100%).
- [x] Pair key unique; no id out of range.
- [x] External features: 0 duplicate natural keys; `weather_date = flight_date - 1`; four months only.
- [x] Join uses `validate="many_to_one"`; row count unchanged; 100% match.
- [x] Airport discontinuity check: 112,341 excluded.
- [x] Network edge list rebuilt independently: 0 mismatches.
- [x] Reproduction of the pipeline is deterministic (identical `core+weather` MAE on re-run).
- [ ] Hand spot-check of about 10 matched pairs against source rows **[TODO]**
- [ ] Timestamp-order check (incoming arrival before next departure) **[TODO]**
- [ ] Re-run notebook top to bottom from a clean kernel **[TODO]**

---

## 10. Models, split and evaluation

- **Split:** 80/20 by `tail_number` (no aircraft in both sets), seed 42. Train 1,302,931 pairs; test 331,910 pairs; 1,181 test aircraft. Further splitting by month: train Jan, Jun, Jul and test Aug (robustness).
- **Model:** `HistGradientBoostingRegressor`, 200 iterations, seed 42, default settings (untuned); carrier as categorical.
- **Models compared on identical test rows:** (1) training-mean baseline, (2) training-median baseline, (3) core, (4) core + weather, (5) core + weather + congestion.
- **Metrics:** MAE (primary) and RMSE (heavy-tailed delays inflate RMSE).
- **Uncertainty:** paired bootstrap over **test aircraft**, 2,000 resamples (1,000 in robustness runs), seed 42, 95% percentile intervals on metric differences.
- **Amulya's baseline (MAE 16.253 vs 15.547) is reported as context and not reproduced here.** Her reported split (details to be re-checked by her against the saved run output **[PENDING]**): chronological, earliest 80% for training and latest 20% for testing; cutoff 2024-10-20 13:25:00 UTC; 5,226,896 training rows and 1,306,733 test rows; both models on identical test rows; seed 42 configured (the split itself is deterministic). Her test period (after 20 Oct) lies outside Jan/Jun/Jul/Aug, the only months with Dhanush's prior-day features, so those features cannot be evaluated on her split. Her weather is also a different definition (hourly observations before arrival). This analysis is therefore a **separately defined experiment**; the two sets of numbers are not compared directly.

---

## 11. Results summary

**Main comparison (test set).**

| Model | MAE (min) | RMSE (min) |
|---|---|---|
| Baseline (mean) | 25.415 | 50.752 |
| Baseline (median) | 20.628 | 53.246 |
| Core | 12.639 | 37.337 |
| Core + weather | 12.569 | 37.225 |
| Core + weather + congestion | 12.531 | 37.216 |

| Comparison | MAE gain (95% CI) | RMSE gain (95% CI) |
|---|---|---|
| Median baseline to core | 7.99 (7.77 to 8.21) | 15.91 (15.42 to 16.43) |
| Core to + weather | 0.071 (0.055 to 0.087) | 0.112 (0.055 to 0.176) |
| + weather to + congestion | 0.038 (0.022 to 0.054) | 0.010 (-0.041 to 0.062) |
| Core to + weather + congestion | 0.109 (0.092 to 0.125) | 0.122 (0.062 to 0.182) |

**Robustness.** Gains keep their sign and CIs exclude 0 across all five ground-time cutoffs and on an unseen month (August: weather 0.086, congestion 0.107). Gust adds nothing. Trimming the top 1% of delays preserves the ordering; RMSE roughly halves, so MAE is the primary metric.

**Subgroups (descriptive).** Weather gain largest in January (0.175) and for incoming delays of 15 to 60 min (0.150); near zero for delays above 60 min. For the 15 busiest origins, weather gain is 0.139 and congestion gain is -0.230; for other airports 0.014 and +0.259.

**Recovery [CONFIRMED by team reply].** late = incoming arrival delay >= 15 min; recovered = next departure delay < 15 min (against scheduled departure). 348,709 late pairs (21.3%); **22.0% recovered** (CI 21.7% to 22.3%, 5,742 aircraft). Recovery by scheduled ground time: 7.8% (<= 45 min), 14.8% (45 to 60), 29.6% (60 to 90), 56.5% (90 to 180). The recovery rate depends strongly on the thresholds (sensitivity grid in `results/recovery_sensitivity.csv`). Denominator: all late pairs. No missing delay values exist in the sample.

**Network.** Nodes = 343 airports; directed edges = 6,367 routes weighted by flight count. Top 10 airports = 33.7% of departures; top 20 = 52.2%; 19 airports carry half. Correlations of centrality with mean departure delay are weak (Spearman 0.06 to 0.21, 170 airports). Descriptive only.

Full tables and wording: see `findings_and_limitations.md`.

---

## 12. Interpretation limits

- Results are associations, not causal effects. Centrality and feature importance do not establish causal influence.
- Weather is prior-day and retrospective; it is not a real-time predictor.
- The weather and congestion gains are statistically reliable but practically very small (under 1% of MAE). The main predictive information is the incoming delay and the scheduled turnaround.
- Subgroup and per-airport differences are not formally tested.
- Intervals cover test-aircraft sampling only, not re-fitting, other seeds or tuning.

---

## 13. Limitations

1. Weather and congestion cover only four months (Jan, Jun, Jul, Aug 2024).
2. Prior-day weather cannot capture within-day conditions; differs from the hourly weather used in Amulya's model.
3. Congestion slightly undercounts (about 4%; cancelled flights absent from the schedule index).
4. Cancelled flights are outside the analysis.
5. Flights are not independent (shared aircraft, airports, days; chained pairs); aircraft-level resampling mitigates but does not remove this.
6. One split, one model family, untuned.
7. Heavy-tailed delays inflate RMSE.
8. Small subgroups are noisy; subgroup results are untested.
9. Recovery results depend on thresholds; 283 pairs have inferred status.
10. 1.69% of valid pairs had an airport mismatch and were excluded.
11. Amulya's hourly-weather baseline (MAE 16.253 vs 15.547) is on a different, later test period and weather definition and is not reproduced here; her weather gain (0.71 min, 4.3%) cannot be compared with ours (0.07 min, 0.56%) because period, weather definition and pair selection differ.
12. XWA weather station match is approximate (63 km).
13. Network metrics are computed on four months only.

---

## 14. Open items for the team

| # | Decision | Status |
|---|---|---|
| 1 | Ground-time cutoff: 180 min primary | **Agreed** (team reply, 15 Nov 2026) |
| 2 | Recovery definition (15 / 15 min) | **Agreed**; document denominators and missing-delay handling (done, section 5 and 11) |
| 3 | "Adjusted association" wording | **Agreed** |
| 4 | Network ownership (Dhanush produces, I validate and interpret) | **Agreed** |
| 5 | Features from next flight only, or also incoming destination | **Agreed**: next flight primary; incoming destination only as a separate sensitivity experiment if available at prediction time (not run) |
| 6 | Amulya's train/test split for baseline reproduction | **Received** (chronological, cutoff 2024-10-20 13:25 UTC, 5,226,896 / 1,306,733 rows); Amulya to re-verify against saved output **[PENDING]**. Not reproducible with the four-month features. |
| 7 | Network metrics used descriptively only | **Agreed** in team reply (keeping them out of predictive features) |

Also agreed: Amulya will add the continuity condition to her validity checks.

---

## 15. Reproducibility record

| Item | Value |
|---|---|
| Random seed | 42 |
| Python | 3.13 (per environment path); package versions **[TODO: `pip list`]** |
| Cutoff, row counts | as in section 5 |
| Date coverage | Features and modelling set: Jan, Jun, Jul, Aug 2024. Pairs file: all of 2024. |
| Commits | **[TODO]** |
| Notebook | **[TODO: final name]** |

**Outputs in `results/`:** `model_comparison_bootstrap.csv`, `robustness_checks.csv`, `subgroup_month.csv`, `subgroup_severity.csv`, `subgroup_weather_available.csv`, `subgroup_top15_airports.csv`, `recovery_sensitivity.csv`, `recovery_by_ground_time.csv`, `recovery_by_ground_time_ci.csv`, `network_top_airports.csv`; figures `fig1_model_performance.png`, `fig2_gains_with_ci.png`, `fig3_robustness.png`, `fig4_recovery.png`, `network_hub_dominance.png`, `network_top20.png`, `network_delay_vs_traffic.png`.

**Intermediate file (not committed):** `data/processed/modelling_table_v1.csv.gz`.

**To reproduce:** load the three source files from the team drive into `data/processed/`, activate the virtual environment, then run the notebook top to bottom (Kernel, Restart and Run All).