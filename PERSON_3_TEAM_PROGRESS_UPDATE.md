# Team Progress Update — Person 3 (Dhanush)

**Date:** November 13, 2026
**Branch:** `feature/person-3-external-features`

---

## Work Completed

Person 3's external-feature pipeline is implemented, tested, and ready for handoff to Anagha.

**Deliverables finished:**

- **NOAA station matching:** All 343 airports in the dataset mapped to their nearest NOAA ISD weather station (334 via ICAO, 8 US-territory airports via extended ICAO, 1 geographic fallback).
- **Weather features:** Downloaded and processed NOAA GSOD daily summaries for 343 stations across 2023–2024. Generated six prior-day weather features per flight (temperature, dew point, visibility, wind speed, wind gust, precipitation).
- **Congestion features:** Five airport-level scheduled-traffic features per flight using trailing time windows on published airline schedule times only (no actual delay data used).
- **External features integration:** Joined weather and congestion into a single analysis-ready dataset.
- **Airport-network analysis:** Built a directed flight network (343 nodes, 6,367 edges). Computed betweenness centrality, PageRank, in/out degree and strength. Generated three visualisations. DFW has the highest betweenness centrality (0.179), suggesting it is the most critical structural connector in the network.
- **Technical audit and corrections:** Addressed all items from the independent audit — retrospective classification of weather features, congestion population accuracy, matplotlib compatibility fix for Python 3.14, documentation updates.
- **Testing:** 232 tests passing, 2 skipped (require raw BTS files not in repo).

---

## Generated Datasets

| Dataset | Rows | Columns | Purpose |
|---|---|---|---|
| `external_features.csv` | 2,319,468 | 19 | Primary handoff — all 11 features |
| `congestion_features.csv` | 2,319,468 | 13 | Congestion-only |
| `weather_features.csv` | 2,319,468 | 14 | Weather-only |
| `network_node_metrics.csv` | 343 | 8 | Per-airport topology metrics |
| `network_edge_list.csv` | 6,367 | 5 | Directed route edge list |

---

## Important Methodological Notes

1. **Weather features are retrospective only.** NOAA GSOD data is published ~3 days after observations (per NCEI documentation). These features are valid controls in retrospective regression but cannot be used in a real-time prediction system.
2. **Congestion features use scheduled times.** Only CRS (published airline schedule) timestamps are used — no actual delays or outcomes enter the calculation. Safe for predictive modelling.
3. **Natural-key join is preferred.** `external_features.csv` contains a stable 6-column join key (`flight_date`, `reporting_airline`, `flight_number`, `origin`, `dest`, `crs_dep_dt_utc`) that works even if files are reordered. The row-index `flight_id` is a fallback only.
4. **Network metrics are descriptive.** High betweenness at DFW does not mean DFW causes delays — it describes structural position in the schedule network. Use as a covariate where the modelling design supports it.

---

## Testing Status

**Latest test run:** 232 passed · 2 skipped · 0 failed

The 2 skips are Thrupthi's tests that require raw BTS files not stored in the repo.

---

## Dataset Coverage Limitation

The current processed flight dataset covers **January, June, July, and August 2024 only** (~2.32 million rotation-eligible flights). The other 8 months (February–May, September–December) are excluded because the BTS DIVERTED column is absent in those monthly downloads, preventing strict rotation eligibility. Regenerating them requires re-downloading raw BTS CSVs and rerunning Thrupthi's pipeline.

---

## Handoff and Next Steps

- **Person 3 implementation is ready for handoff** to Anagha. Full documentation is in `PERSON_3_TO_PERSON_4_HANDOFF.md`.
- **Dataset transfer is still required.** The large CSVs (~1 GB combined) are stored locally and gitignored. They need to be shared via OneDrive or similar, or Anagha can regenerate them using the commands in the handoff document (~15–30 min).
- **Rotation-pair data from Amulya is a separate dependency.** Anagha will need Amulya's aircraft rotation pairs to run the main regression. That is not part of Person 3's scope.
- Nothing has been committed or pushed yet — pending final team review.
