# Airport Network Analysis Report

**Owned by:** Dhanush R (Person 3)
**Study period:** January, June, July, August 2024
**Data source:** `data/processed/flights_rotation_eligible.csv`

---

## 1. Network Definition

| Property | Value |
|---|---|
| Node type | US airport (IATA code) |
| Edge type | Directed (origin → destination) |
| Edge weight | `flight_count` — total rotation-eligible flights on route |
| Nodes (airports) | **343** |
| Edges (directed routes) | **6367** |
| Total flights represented | **2,319,468** |
| Network density | 0.054277 |
| Weakly connected components | 1 |
| Strongly connected components | 9 |
| Mean in-degree | 18.56 |
| Mean out-degree | 18.56 |

---

## 2. ⚠️ Causal Disclaimer

All metrics below are **descriptive** summaries of the flight schedule topology.
High betweenness centrality or high PageRank at an airport does NOT imply that
airport causes more delays across the network. Identifying causal delay
propagation requires the aircraft-rotation pair analysis (Amulya, Person 2)
and statistical modelling (Anagha, Person 4). These network metrics are
control variables and descriptive context, not causal estimates.

---

## 3. Top 10 Airports by Total Traffic

| IATA | In-strength | Out-strength | Total | In-deg | Out-deg |
|---|---|---|---|---|---|
| **ATL** | 109,884 | 112,363 | 222,247 | 152 | 156 |
| **DFW** | 105,096 | 105,887 | 210,983 | 180 | 183 |
| **DEN** | 104,114 | 104,456 | 208,570 | 176 | 177 |
| **ORD** | 90,558 | 91,425 | 181,983 | 156 | 159 |
| **CLT** | 69,777 | 71,132 | 140,909 | 125 | 129 |
| **LAX** | 65,296 | 65,236 | 130,532 | 104 | 106 |
| **LAS** | 61,898 | 61,533 | 123,431 | 128 | 128 |
| **PHX** | 60,893 | 60,898 | 121,791 | 108 | 108 |
| **SEA** | 57,500 | 57,466 | 114,966 | 85 | 85 |
| **LGA** | 50,627 | 51,612 | 102,239 | 81 | 82 |

---

## 4. Top 10 Airports by Betweenness Centrality

_Betweenness centrality (normalised): fraction of all shortest paths (unweighted) in the network that pass through this airport._

| IATA | Betweenness | In-strength | Out-strength |
|---|---|---|---|
| **DFW** | 0.1786 | 105,096 | 105,887 |
| **DEN** | 0.1775 | 104,114 | 104,456 |
| **ORD** | 0.1052 | 90,558 | 91,425 |
| **ATL** | 0.0927 | 109,884 | 112,363 |
| **MSP** | 0.0749 | 43,240 | 43,299 |
| **ANC** | 0.0653 | 7,599 | 7,570 |
| **SEA** | 0.0473 | 57,500 | 57,466 |
| **LAS** | 0.0463 | 61,898 | 61,533 |
| **DTW** | 0.0455 | 43,014 | 43,274 |
| **SLC** | 0.0415 | 38,239 | 38,149 |

---

## 5. Top 10 Airports by PageRank

_PageRank (weighted by flight_count): airports receiving traffic from other high-traffic airports score higher._

| IATA | PageRank | In-strength | Out-strength |
|---|---|---|---|
| **DFW** | 0.048106 | 105,096 | 105,887 |
| **DEN** | 0.046639 | 104,114 | 104,456 |
| **ATL** | 0.042334 | 109,884 | 112,363 |
| **ORD** | 0.036352 | 90,558 | 91,425 |
| **CLT** | 0.026163 | 69,777 | 71,132 |
| **SEA** | 0.023659 | 57,500 | 57,466 |
| **LAX** | 0.021977 | 65,296 | 65,236 |
| **PHX** | 0.021805 | 60,893 | 60,898 |
| **LAS** | 0.021587 | 61,898 | 61,533 |
| **MSP** | 0.020158 | 43,240 | 43,299 |

---

## 6. Busiest Routes

| Origin | Dest | Flights | Mean Dep Delay (min) |
|---|---|---|---|
| HNL | OGG | 3,911 | 3.2 |
| OGG | HNL | 3,909 | 2.6 |
| LAX | SFO | 3,591 | 10.4 |
| SFO | LAX | 3,584 | 11.9 |
| ORD | LGA | 3,463 | 26.3 |
| LGA | ORD | 3,462 | 24.8 |
| JFK | LAX | 3,296 | 14.3 |
| LAX | JFK | 3,259 | 19.0 |
| LAS | LAX | 3,131 | 15.7 |
| LAX | LAS | 3,131 | 15.3 |

---

## 7. Data Limitations

- **4-month coverage:** January, June, July, August 2024 only.
  Network topology for other months may differ.
- **Rotation-eligible cohort only:** Cancelled and diverted flights are excluded.
  These represent ~3–5% of BTS source records for these months.
- **No international flights:** All airports are US domestic only.

---

## 8. Visualisations

- `reports/network_degree_distribution.png` — in-degree and out-degree histograms
- `reports/network_top_hubs.png` — top-20 airports by traffic and PageRank

*Report generated 2026-11-13 14:52 UTC.*
