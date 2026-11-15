# Quantifying Aircraft-Rotation Delay Propagation Under Operational and Environmental Confounding


## Team

| Team member | SRN | Main contribution |
|---|---|---|
| Thrupthi G D | PES1UG24AM476 | BTS data ingestion, preprocessing, timestamp reconstruction and data-quality validation |
| Amulya P | PES1UG24AM458 | Aircraft-rotation reconstruction, turnaround analysis, core modelling and hourly-weather experiment |
| Anagha Kaushik | PES1UG24AM459 | Statistical modelling, aircraft-level uncertainty estimates, robustness, recovery and final analysis |
| Dhanush R | PES1UG24AM427 | Weather/station matching, congestion features and airport-network data/metrics |

## Project overview

Aircraft delays can carry forward when the same aircraft operates consecutive flights. This project reconstructs aircraft rotations from U.S. Bureau of Transportation Statistics (BTS) flight records and studies the **adjusted association** between an incoming flight's arrival delay and the next flight's departure delay. Scheduled turnaround time, prior-calendar-day weather and scheduled airport congestion are examined as additional information. The project also studies delay recovery and describes the airport network. The study is observational and does not establish causal effects.

## Data sources and study sample

- **BTS Reporting Carrier On-Time Performance (2024):** flight dates, tail numbers, airports, scheduled/actual times, arrival/departure delays and operational fields. The paper reports 7,079,081 cleaned flights, 6,841,162 rotation-eligible flights and 7,052,817 candidate consecutive-flight pairs.
- **NOAA Global Summary of the Day (GSOD):** historical daily weather summaries matched to airports through weather stations. The paper reports 343 airport matches: 334 by ICAO code, 8 through an extended ICAO list and 1 geographic fallback.
- **Airport reference/time-zone data:** airport identifiers, locations and time-zone information used for timestamp reconstruction, station matching and network construction.

The final GSOD analysis covers **January, June, July and August 2024**. It starts with 6,645,970 valid connections, retains 6,533,629 with incoming destination equal to next origin, applies a scheduled-ground-time cutoff of 180 minutes, restricts to the four feature months, and joins 1,634,841 pairs to weather/congestion features. The final sample contains 5,909 aircraft. See the paper's Table I for the full sample-flow counts.

## Methodology

1. Clean BTS records, standardize fields, validate identifiers and reconstruct UTC timestamps for consistent cross-airport chronology.
2. Link consecutive flights sharing a tail number to form incoming/next-flight rotation pairs.
3. Derive scheduled ground time and turnaround measures; validate pair identifiers against the flight table.
4. Match airports to NOAA GSOD stations and construct prior-calendar-day weather features.
5. Construct scheduled-timetable congestion counts using trailing 1-hour and 3-hour windows. The reference flight is excluded from its own window.
6. Join features to the next flight using a six-column natural key (date, airline, flight number, origin, destination and scheduled UTC departure), not row position.
7. Fit histogram-based gradient-boosted regression models and compare core, core + weather, and core + weather + congestion specifications.
8. Evaluate delay recovery and describe airport-network structure separately from predictive modelling.

### Prediction target and features

The primary outcome is **the next flight's departure delay in minutes**. Core predictors include incoming arrival delay, scheduled ground time, month, weekday, scheduled UTC departure hour and carrier. The weather model adds five variables: temperature, dew point, visibility, wind and precipitation. Gust is collected but excluded from the primary weather model because it is missing in 31.2% of rows and is not imputed as zero. The congestion model adds five scheduled-traffic counts.

### Main model evaluation

The paper reports an 80/20 **aircraft-level** split, so an aircraft does not appear in both training and test sets. Training contains 1,302,931 pairs; testing contains 331,910 pairs across 1,181 aircraft. The model uses 200 boosting iterations, default settings and seed 42. The paper reports:

| Model | Test MAE (min) | Test RMSE (min) |
|---|---:|---:|
| Training-mean baseline | 25.415 | 50.752 |
| Training-median baseline | 20.628 | 53.246 |
| Core | 12.639 | 37.337 |
| Core + weather | 12.569 | 37.225 |
| Core + weather + congestion | 12.531 | 37.216 |

Weather reduces MAE by 0.071 minutes versus core (95% aircraft-bootstrap CI: 0.055–0.087). Congestion reduces MAE by a further 0.038 minutes (95% CI: 0.022–0.054). These gains are statistically detectable but practically small. The additional congestion RMSE gain is not distinguishable from zero. Confidence intervals use paired bootstrap resampling of test aircraft (2,000 resamples).

**Keep the separate earlier hourly-weather experiment distinct.** Amulya's earlier core/weather MAEs (16.253/15.547) came from a different experiment and have not been reproduced on the final paper's sample/split/target setup. They are not part of the GSOD comparison above and should not be compared directly.

## Additional analyses

### Delay recovery

An incoming flight is classified as late when its arrival delay is at least 15 minutes. It is classified as recovered when the next flight's departure delay is below 15 minutes. Of 348,709 late incoming flights, 22.0% were followed by a departure below the 15-minute threshold. Recovery varies with scheduled turnaround: 7.8% for up to 45 minutes, 14.8% for 45–60 minutes, 29.6% for 60–90 minutes and 56.5% for 90–180 minutes. These are associations, not evidence that adding turnaround time causes recovery.

### Airport network

The descriptive network contains 343 airport nodes and 6,367 directed routes, weighted by flight count. The independently rebuilt edge list matched the supplied route counts. The top 20 airports account for 52.2% of departures; 19 airports account for half. Network centrality is descriptive and is not used as a predictive feature or interpreted causally.

## Repository structure

The repository snapshot includes:

- `src/`: preprocessing, airport time-zone handling, weather/station matching, congestion and network feature modules.
- `scripts/`: pipeline, weather, congestion, station-map, external-feature and network-analysis runners.
- `tests/`: unit and audit tests for preprocessing, station matching, weather and congestion features.
- `notebooks/person4_analysis.ipynb`: statistical modelling, model comparisons, robustness, recovery and network analysis.
- `reports/`: data-quality, weather, congestion, station-matching and network reports/figures.
- `results/`: model comparison, bootstrap, robustness, recovery, subgroup and network outputs.
- `docs/`: data dictionary, analysis contract, findings/limitations and handoff documentation.

Large raw and processed datasets are excluded from the repository by `.gitignore`; users need access to the corresponding project data files separately.

## Reproducibility and data requirements

The repository snapshot contains scripts and tests, but the full raw/processed data required to regenerate every result is not bundled. Obtain the exact BTS, weather and processed feature files from the team's shared data location and preserve the versions/order expected by each pipeline.

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
python -m pip install -r requirements.txt
```

Run the available test suite:

```bash
python -m pytest -v
```

The repository provides separate runners under `scripts/` for preprocessing, weather, congestion, external-feature construction and network analysis. Check each runner's arguments and required input paths before execution; do not assume that running one script alone reproduces the final notebook results. The final analysis notebook also requires the corresponding large datasets and result inputs.

**Row-ID caution:** `incoming_row_id` and `next_row_id` in the rotation-pair file are zero-based row positions in the exact `flights_clean_recovered.csv` used when the pairs were generated. Do not sort, filter or rewrite that file before using those IDs. Person 4's final feature integration uses the documented natural key rather than relying on row IDs.

## Limitations
- **Retrospective weather data:** GSOD data is published with a delay, limiting its use for real-time operational prediction.
- **Data coverage:** The analysis uses selected months from 2024 and is restricted to flights with valid aircraft rotations and available features.
- **Predictive scope:** The results show statistical associations, not causal relationships, and may not generalise to all operational conditions.

## Interpretation
Adding weather and congestion features produced statistically significant improvements in prediction accuracy, but the gains were practically small. This suggests that incoming flight delays and scheduled turnaround time explain much of the predictable delay propagation, while weather and airport congestion provide only modest additional information. Recovery rates also increased with longer scheduled ground times, highlighting the importance of turnaround buffers. However, these findings are observational and should not be interpreted as causal effects.

## References

1. AhmadBeygi et al. (2008), analysis of delay propagation in passenger airline networks.
2. Pyrgiotis et al. (2013), modelling delay propagation within an airport network.
3. Beatty et al. (1999), preliminary evaluation of flight delay propagation through an airline schedule.
4. U.S. Bureau of Transportation Statistics, Reporting Carrier On-Time Performance.
5. NOAA National Centers for Environmental Information, Global Summary of the Day.
6. Pedregosa et al. (2011), *Scikit-learn: Machine Learning in Python*.
7. Efron (1979), *Bootstrap Methods: Another Look at the Jackknife*.
