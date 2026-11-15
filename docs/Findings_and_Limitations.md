# Person 4 Findings and Limitations (DRAFT v0.1)

**Author:** Anagha Kaushik
**Data:** Jan, Jun, Jul, Aug 2024 (the months covered by the external features)
**Status:** draft for the author to edit. Team decisions confirmed 15 Nov 2026 (cutoff, recovery definition, wording, network, feature timing). Items marked **[PENDING]** await a teammate's check.

> Wording rule: all results are **adjusted associations**, not causal effects. Weather is a prior-day summary and is **retrospective only**; it is not a real-time predictor.

---

## 1. Sample and definitions

| Step | Pairs |
|---|---|
| Rotation pairs (Amulya) | 7,052,817 |
| Valid connections | 6,645,970 |
| Remove airport mismatch (incoming destination differs from next origin) | 6,533,629 (112,341 removed, 1.69%) |
| Scheduled ground time <= 180 min | 4,854,175 |
| In the four months with external features | **1,634,841** |
| Matched to weather and congestion features | 1,634,841 (100%) |

- Unit: one aircraft rotation pair (incoming flight, next flight on the same tail number). 5,909 aircraft.
- Outcome: departure delay of the next flight (minutes). Main exposure: arrival delay of the incoming flight.
- Only flights that actually operated are included; cancelled flights are outside the analysis.
- 283 pairs (0.017%) have an inferred incoming diversion status; results are unchanged without them.

Delay distribution (modelling set): median incoming arrival delay -5 min, mean 5.2; median next departure delay -1 min, mean 15.2; 99th percentiles 166 and 199 min; maximums above 1,400 min. Raw correlation between the two delays: 0.649 (unadjusted).

---

## 2. Prediction models

**Setup.** Split by aircraft (80/20, seed 42): 1,302,931 training pairs, 331,910 test pairs, 1,181 test aircraft. Gradient-boosted trees (HistGradientBoostingRegressor, 200 iterations, seed 42, untuned). Models compared on identical test rows. Weather and congestion come from Dhanush's file, joined to the next flight.

| Model | MAE (min) | RMSE (min) |
|---|---|---|
| Baseline: training mean | 25.415 | 50.752 |
| Baseline: training median | 20.628 | 53.246 |
| Core | 12.639 | 37.337 |
| Core + weather | 12.569 | 37.225 |
| Core + weather + congestion | 12.531 | 37.216 |

**Paired bootstrap (2,000 resamples of test aircraft, 95% intervals).**

| Comparison | MAE gain (min) | MAE 95% CI | RMSE gain (min) | RMSE 95% CI |
|---|---|---|---|---|
| Median baseline to core | 7.99 (38.7%) | 7.77 to 8.21 | 15.91 (29.9%) | 15.42 to 16.43 |
| Core to + weather | 0.071 (0.56%) | 0.055 to 0.087 | 0.112 (0.30%) | 0.055 to 0.176 |
| + weather to + congestion | 0.038 (0.30%) | 0.022 to 0.054 | 0.010 (0.03%) | -0.041 to 0.062 |
| Core to + weather + congestion | 0.109 (0.86%) | 0.092 to 0.125 | 0.122 (0.33%) | 0.062 to 0.182 |

**What this supports.**
- Knowing the incoming flight's delay and the scheduled turnaround explains most of the predictable next-flight delay (MAE falls by about 39% versus the median baseline).
- Prior-day weather and scheduled congestion each add a statistically reliable but very small improvement (about 0.04 to 0.1 minutes of MAE, under 1%).
- For RMSE, congestion's additional gain is not distinguishable from zero.

**What it does not support.** It does not show that weather or congestion cause delays, and the weather gain is too small to call practically important.

---

## 3. Robustness

**Ground-time cutoff (MAE gain, 95% CI).**

| Cutoff (min) | Weather vs core | Congestion vs weather |
|---|---|---|
| 120 | 0.047 (0.032 to 0.061) | 0.115 (0.096 to 0.133) |
| 180 (main) | 0.071 (0.056 to 0.086) | 0.038 (0.022 to 0.054) |
| 360 | 0.073 (0.056 to 0.089) | 0.082 (0.065 to 0.099) |
| 720 | 0.053 (0.039 to 0.069) | 0.085 (0.073 to 0.099) |
| 1440 | 0.091 (0.076 to 0.107) | 0.042 (0.027 to 0.057) |

Every interval excludes zero, so the direction does not depend on the cutoff. The congestion gain varies from about 0.04 to 0.12 minutes, so it should be reported as a range. MAE is not comparable across rows (each uses a different set of pairs); compare gains only.

**Unseen month (train Jan, Jun, Jul; test Aug).** Weather gain 0.086 (0.074 to 0.098); congestion gain 0.107 (0.093 to 0.120). Gains persist on a month the model did not see (a single, summer held-out month).

**Gust variable.** Adding `gust_ms_prev` plus a reported-flag changed MAE by -0.004 (CI -0.013 to 0.004): no benefit. Gust was excluded from the main models.

**Trimming extreme delays** (top 1%; 327,308 of 331,910 test pairs kept; point estimates only, since trimming on the outcome is not a valid way to score a predictor). Ordering and gap sizes are unchanged (MAE 10.600, 10.539, 10.494 for core, + weather, + weather + congestion). RMSE roughly halves (37.2 to 18.4), showing that a few very long delays drive RMSE; MAE is therefore the main metric.

---

## 4. Subgroups (descriptive, no intervals)

- **Month:** weather gain is largest in January (0.175 min, n = 70,457) and smallest in June (0.030). Congestion gain: June 0.060, July 0.121, January -0.021, August -0.020.
- **Incoming delay size:** weather gain is largest for moderate delays (15 to 60 min: 0.150) and near zero for delays above 60 min (0.009).
- **Airports:** the 15 busiest origins (n = 150,149): weather gain 0.139, congestion gain -0.230. All other airports (n = 181,761): weather 0.014, congestion +0.259. The congestion effect therefore has opposite signs in the two groups. A possible reason (not tested) is that congestion counts partly stand in for airport identity, which the model does not otherwise see.
- **Missing weather:** only 1,026 pairs; their apparent "gain" of 0.486 min cannot be due to weather and illustrates that small subgroups are noisy.

Subgroup differences were not formally tested; treat them as descriptive patterns.

---

## 5. Recovery

**Definition (agreed):** late = incoming arrival delay >= 15 min; recovered = next departure delay < 15 min (against scheduled departure). Absorbed delay = incoming arrival delay minus next departure delay. Denominator = all late pairs; no delay values are missing in the analysis sample (valid pairs require actual times), so nothing is imputed.

- 348,709 of 1,634,841 pairs (21.3%) have a late incoming flight. **22.0% recovered** (95% CI 21.7% to 22.3%, aircraft bootstrap, 5,742 aircraft). Median absorbed delay: 5 min (middle half -6 to 16).
- Excluding the 283 inferred pairs: 22.01% (essentially identical).

**Sensitivity (% recovered):**

| Late if arrival delay >= | Next dep. < 5 min | < 15 min | < 30 min | n late |
|---|---|---|---|---|
| 5 min | 22.9 | 38.5 | 57.2 | 512,304 |
| 15 min (main) | 11.8 | 22.0 | 41.9 | 348,709 |
| 30 min | 6.3 | 10.2 | 21.4 | 221,661 |
| 60 min | 3.1 | 4.8 | 7.7 | 110,853 |

**By scheduled ground time (95% CI):**

| Ground time (min) | Recovered | CI | n late |
|---|---|---|---|
| <= 45 | 7.8% | 7.6 to 8.1 | 97,632 |
| 45 to 60 | 14.8% | 14.5 to 15.1 | 113,668 |
| 60 to 90 | 29.6% | 29.2 to 30.0 | 94,100 |
| 90 to 180 | 56.5% | 56.0 to 57.0 | 43,309 |

**Interpretation.** Most late arrivals pass delay on to the next flight, and recovery rises steeply with scheduled turnaround time. This is an association: turnaround times are chosen by airlines for reasons that may also relate to delay risk (aircraft type, hub, time of day), so the data do not show that adding slack would cause recovery. Quote the definition with every rate, because the rate depends heavily on the thresholds. Use the median, not the mean, for absorbed delay (extreme values distort the mean).

---

## 6. Airport network

Dhanush's edge list was independently rebuilt from the flight data: 6,367 routes, 0 count mismatches, 2,319,468 flights, delay differences at most 0.0005 minutes.

- **Definition:** nodes = 343 airports; directed edges = routes (origin to destination); weight = flight count (Jan, Jun, Jul, Aug 2024).
- **Concentration:** top 1 airport (ATL) = 4.8% of departures; top 5 = 20.9%; top 10 = 33.7%; top 20 = 52.2%; top 50 = 79.2%. 19 of 343 airports carry half of all departures; 216 airports connect to 10 or fewer destinations.
- **Delay and position (170 airports with at least 1,000 departures, Spearman):** weak correlations with mean departure delay: out-strength 0.08, PageRank 0.06, out-degree 0.19, betweenness 0.21. Mean departure delay among the top 20 airports ranges from about 12 min (LAX) to about 27 min (CLT), with no clear link to size.
- Metrics describe the four-month schedule network. **Centrality is not evidence that an airport causes or spreads delays.** Metrics were computed on all four months and are used descriptively only (not as model features).

---

## 7. Limitations

1. Weather and congestion cover only four months of 2024.
2. Weather is a prior-day daily summary: it cannot capture within-day changes and is **retrospective only** (published about 3 days late). It is not the same weather definition as the hourly arrival-time weather in Amulya's earlier model, so the two sets of results are not directly comparable.
3. Congestion is built from the scheduled timetable and slightly undercounts (cancelled flights are absent; about 4%).
4. Cancelled flights are outside the analysis; results describe flights that operated.
5. Flights are not independent (shared aircraft, airports, days; each flight is in two pairs). Aircraft-level resampling reduces but does not remove this. Intervals cover test-aircraft variability only, not re-fitting, other seeds or tuned hyperparameters.
6. One train/test split, one model family, untuned.
7. Heavy-tailed delays inflate RMSE; MAE is the primary metric.
8. Subgroup results are descriptive and untested; small subgroups are noisy.
9. Recovery results depend on the chosen thresholds. Inferred statuses affect 283 pairs (0.017%) in this sample, but 8 other months of 2024 rely on inferred diversion status (to be verified).
10. About 1.69% of pairs marked valid had an airport mismatch and were excluded.
11. Amulya's hourly-weather baseline (MAE 16.253 vs 15.547, 4.3% gain) used a chronological split (test after 2024-10-20 13:25 UTC; 5,226,896 train / 1,306,733 test rows; cutoff and counts to be re-verified by her **[PENDING]**). That test period is outside the four months with our prior-day features, and the weather definition differs, so the two experiments are not comparable. Her weather gain (0.71 min) is about ten times ours (0.07 min); the difference is consistent with hourly pre-arrival weather carrying more information than prior-day summaries, but this was not tested.
12. Station match for one airport (XWA) is approximate (63 km).

---

## 8. Reproduction (fill in before hand-off)

- Notebook: `notebooks/person4_analysis.ipynb` 
- Results: `results/` (model_comparison_bootstrap.csv, robustness_checks.csv, subgroup_*.csv, recovery_*.csv, network_top_airports.csv, figures fig1 to fig4 and network_*.png)
- Seed: 42.
- Data files are not committed (too large); obtain from the team drive.