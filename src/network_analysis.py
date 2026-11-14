"""Airport network analysis.

Owned by Dhanush R (Person 3).

Computes descriptive network metrics for the US airport flight network using
aircraft-rotation-eligible flight pairs from the BTS On-Time Performance data.

Network definition
------------------
Nodes
    Each unique US airport IATA code that appears as an origin or destination
    in ``flights_rotation_eligible.csv``.

Edges
    A **directed** edge from airport A to airport B exists for every distinct
    (origin, dest) pair in the dataset.  Directed edges are used because
    delay propagation is directional: a delay leaving ATL for ORD does not
    imply an equivalent delay on the ORD-to-ATL direction.

Edge weights
    ``flight_count`` — total number of rotation-eligible flights on that
    directed route over the study period (Jan, Jun, Jul, Aug 2024).  Higher
    weight = more traffic, denser operational link.

    Two supplementary weight columns are also computed:
    ``mean_dep_delay_min`` — average departure delay on the route (minutes).
    ``mean_arr_delay_min`` — average arrival delay on the route (minutes).
    These are descriptive summaries, not causal estimates.

Metrics computed
----------------
Per-node:
    ``in_degree``           Number of distinct origin airports that fly TO this airport.
    ``out_degree``          Number of distinct destination airports this airport flies TO.
    ``in_strength``         Sum of flight_count on all incoming edges (weighted in-degree).
    ``out_strength``        Sum of flight_count on all outgoing edges (weighted out-degree).
    ``total_strength``      in_strength + out_strength (total traffic volume).
    ``betweenness_centrality``  Fraction of shortest paths (unweighted) passing through
                            this node.  High betweenness = important routing hub.
    ``pagerank``            PageRank on the weighted directed graph.  Airports with
                            high-traffic incoming edges from other high-PageRank airports
                            score higher.

Network-level:
    ``n_nodes``             Total airports (nodes).
    ``n_edges``             Total directed routes (edges).
    ``density``             Actual edges / possible edges.
    ``n_weakly_connected_components``  Number of weakly connected components.
    ``n_strongly_connected_components``  Strongly connected components.

Causal-claim disclaimer
-----------------------
These are **descriptive** metrics only.  Betweenness centrality or PageRank
being high for an airport does NOT imply that airport causes more delays
across the network.  Delay propagation requires causal analysis (which is
Anagha's modelling work in Phase 4), not network topology alone.

Usage
-----
::

    python -m scripts.run_network_analysis

    # Or import directly:
    from src.network_analysis import build_flight_network, compute_node_metrics

Outputs
-------
``data/processed/network_edge_list.csv``   Directed edge list with weights.
``data/processed/network_node_metrics.csv``   Per-airport metrics.
``reports/network_analysis_report.md``     Summary report.
``reports/network_degree_distribution.png``  Degree distribution plot (if matplotlib available).
``reports/network_top_hubs.png``           Top-hub bar chart (if matplotlib available).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

__all__ = [
    "build_edge_list",
    "build_flight_network",
    "compute_node_metrics",
    "compute_network_summary",
    "run",
]


# ---------------------------------------------------------------------------
# 1. Build edge list from flight data
# ---------------------------------------------------------------------------


def build_edge_list(
    flights_path: str,
    chunksize: int = 200_000,
) -> pd.DataFrame:
    """Aggregate per-route (origin, dest) statistics from the flight dataset.

    Parameters
    ----------
    flights_path : str
        Path to ``flights_rotation_eligible.csv`` or similar cleaned BTS file.
        Must contain ``origin``, ``dest``, ``dep_delay``, ``arr_delay`` columns.
    chunksize : int
        Rows per chunk for memory-efficient processing.

    Returns
    -------
    pd.DataFrame
        One row per directed (origin, dest) pair with columns:
        ``origin``, ``dest``, ``flight_count``,
        ``mean_dep_delay_min``, ``mean_arr_delay_min``.
    """
    dep_sums: dict[tuple[str, str], list] = {}

    for chunk in pd.read_csv(
        flights_path,
        usecols=["origin", "dest", "dep_delay", "arr_delay"],
        chunksize=chunksize,
        low_memory=False,
    ):
        for (origin, dest), grp in chunk.groupby(["origin", "dest"], sort=False):
            key = (origin, dest)
            dep_sums.setdefault(key, [0, 0.0, 0.0, 0, 0])
            dep_sums[key][0] += len(grp)                                   # count
            dep_sums[key][1] += grp["dep_delay"].sum(skipna=True)          # dep_delay sum
            dep_sums[key][2] += grp["dep_delay"].notna().sum()             # dep_delay n
            dep_sums[key][3] += grp["arr_delay"].sum(skipna=True)          # arr_delay sum
            dep_sums[key][4] += grp["arr_delay"].notna().sum()             # arr_delay n

    rows = []
    for (origin, dest), v in dep_sums.items():
        count, dep_sum, dep_n, arr_sum, arr_n = v
        rows.append({
            "origin": origin,
            "dest": dest,
            "flight_count": count,
            "mean_dep_delay_min": round(dep_sum / dep_n, 3) if dep_n > 0 else float("nan"),
            "mean_arr_delay_min": round(arr_sum / arr_n, 3) if arr_n > 0 else float("nan"),
        })

    edge_df = pd.DataFrame(rows).sort_values(
        ["flight_count"], ascending=False
    ).reset_index(drop=True)
    return edge_df


# ---------------------------------------------------------------------------
# 2. Build NetworkX directed graph
# ---------------------------------------------------------------------------


def build_flight_network(edge_df: pd.DataFrame) -> nx.DiGraph:
    """Build a directed weighted NetworkX graph from an edge list.

    Parameters
    ----------
    edge_df : pd.DataFrame
        Output of ``build_edge_list`` — must have
        ``origin``, ``dest``, ``flight_count`` columns.

    Returns
    -------
    nx.DiGraph
        Directed graph. Edge attributes: ``flight_count``,
        ``mean_dep_delay_min``, ``mean_arr_delay_min``.
        Node attribute: ``iata`` (the airport code, same as node ID).
    """
    G = nx.DiGraph()

    for _, row in edge_df.iterrows():
        G.add_edge(
            row["origin"],
            row["dest"],
            flight_count=int(row["flight_count"]),
            mean_dep_delay_min=float(row["mean_dep_delay_min"])
            if not pd.isna(row["mean_dep_delay_min"]) else None,
            mean_arr_delay_min=float(row["mean_arr_delay_min"])
            if not pd.isna(row["mean_arr_delay_min"]) else None,
        )

    # Set node attribute
    for node in G.nodes():
        G.nodes[node]["iata"] = node

    return G


# ---------------------------------------------------------------------------
# 3a. Manual PageRank (power iteration, avoids scipy dependency)
# ---------------------------------------------------------------------------


def _pagerank_manual(
    G: nx.DiGraph,
    weight: str | None = "flight_count",
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1.0e-6,
) -> dict:
    """Pure-Python power-iteration PageRank, avoiding the scipy dependency
    that ``nx.pagerank`` requires in networkx >= 3.4.

    Parameters
    ----------
    G : nx.DiGraph
    weight : str or None
        Edge attribute to use as weight.  None = unweighted.
    alpha : float
        Damping factor (default 0.85, standard).
    max_iter, tol : int, float
        Convergence parameters.

    Returns
    -------
    dict  {node: pagerank_score}
    """
    nodes = list(G.nodes())
    n = len(nodes)
    if n == 0:
        return {}

    idx = {v: i for i, v in enumerate(nodes)}
    rank = np.full(n, 1.0 / n)
    dangling_nodes = [v for v in nodes if G.out_degree(v) == 0]

    # Build out-weight sums
    out_w: dict = {}
    for v in nodes:
        if weight:
            out_w[v] = sum(d.get(weight, 1) for _, _, d in G.out_edges(v, data=True))
        else:
            out_w[v] = G.out_degree(v)
        if out_w[v] == 0:
            out_w[v] = 1  # dangling: treat as uniform

    for _ in range(max_iter):
        rank_prev = rank.copy()
        dangling_sum = alpha * sum(rank_prev[idx[v]] for v in dangling_nodes) / n
        new_rank = np.full(n, (1.0 - alpha) / n + dangling_sum)
        for u, v, data in G.edges(data=True):
            w = data.get(weight, 1) if weight else 1
            new_rank[idx[v]] += alpha * rank_prev[idx[u]] * w / out_w[u]
        err = np.abs(new_rank - rank_prev).sum()
        rank = new_rank
        if err < tol:
            break

    total = rank.sum()
    return {v: float(rank[idx[v]] / total) for v in nodes}


# ---------------------------------------------------------------------------
# 3. Compute per-node metrics
# ---------------------------------------------------------------------------


def compute_node_metrics(G: nx.DiGraph) -> pd.DataFrame:
    """Compute per-airport descriptive network metrics.

    Parameters
    ----------
    G : nx.DiGraph
        Directed flight network from ``build_flight_network``.

    Returns
    -------
    pd.DataFrame
        One row per airport (node), sorted descending by ``total_strength``.
        Columns: ``iata``, ``in_degree``, ``out_degree``,
        ``in_strength``, ``out_strength``, ``total_strength``,
        ``betweenness_centrality``, ``pagerank``.

    Notes
    -----
    Betweenness centrality is computed on the unweighted graph (treating each
    directed edge as equally important) and normalised by (n-1)(n-2).  This
    is consistent with the standard definition and allows comparison across
    different-sized subgraphs.

    PageRank is computed on the weighted graph using ``flight_count`` as
    edge weight.  Airports with high PageRank receive traffic from many
    other high-traffic airports.
    """
    nodes = list(G.nodes())

    # Degree
    in_deg = dict(G.in_degree())
    out_deg = dict(G.out_degree())

    # Weighted strength (sum of flight_count on in/out edges)
    in_str = {n: sum(d.get("flight_count", 1) for _, _, d in G.in_edges(n, data=True))
              for n in nodes}
    out_str = {n: sum(d.get("flight_count", 1) for _, _, d in G.out_edges(n, data=True))
               for n in nodes}

    # Betweenness centrality (unweighted, normalised)
    print("  Computing betweenness centrality ...")
    bc = nx.betweenness_centrality(G, normalized=True)

    # PageRank (weighted by flight_count) — manual power iteration to avoid
    # scipy dependency, which networkx 3.4.x requires for nx.pagerank().
    print("  Computing PageRank ...")
    pr = _pagerank_manual(G, weight="flight_count")

    rows = []
    for node in nodes:
        rows.append({
            "iata": node,
            "in_degree": in_deg[node],
            "out_degree": out_deg[node],
            "in_strength": in_str[node],
            "out_strength": out_str[node],
            "total_strength": in_str[node] + out_str[node],
            "betweenness_centrality": round(bc[node], 6),
            "pagerank": round(pr[node], 8),
        })

    return (
        pd.DataFrame(rows)
        .sort_values("total_strength", ascending=False)
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# 4. Network-level summary
# ---------------------------------------------------------------------------


def compute_network_summary(G: nx.DiGraph, edge_df: pd.DataFrame) -> dict[str, Any]:
    """Compute scalar network-level summary statistics.

    Parameters
    ----------
    G : nx.DiGraph
        Directed flight network.
    edge_df : pd.DataFrame
        Edge list with flight counts.

    Returns
    -------
    dict
        Network-level metrics.
    """
    n = G.number_of_nodes()
    m = G.number_of_edges()

    return {
        "n_nodes": n,
        "n_edges": m,
        "density": round(nx.density(G), 6),
        "n_weakly_connected_components": nx.number_weakly_connected_components(G),
        "n_strongly_connected_components": nx.number_strongly_connected_components(G),
        "total_flights": int(edge_df["flight_count"].sum()),
        "max_flights_single_route": int(edge_df["flight_count"].max()),
        "busiest_route": f"{edge_df.iloc[0]['origin']}-{edge_df.iloc[0]['dest']}",
        "mean_in_degree": round(sum(d for _, d in G.in_degree()) / n, 2) if n else 0,
        "mean_out_degree": round(sum(d for _, d in G.out_degree()) / n, 2) if n else 0,
    }


# ---------------------------------------------------------------------------
# 5. Visualisation helpers
# ---------------------------------------------------------------------------

def _apply_matplotlib_py314_patch():
    """Monkey-patch matplotlib.path.Path.__deepcopy__ for Python 3.14 compatibility.

    Python 3.14 changed copy.deepcopy behaviour for C extension types, causing
    infinite recursion in matplotlib.path.Path.__deepcopy__.  This patch
    replaces the broken method with a safe shallow-copy equivalent.

    Safe to call multiple times (idempotent).
    """
    try:
        from matplotlib.path import Path as _MplPath
        if getattr(_MplPath, "_py314_patched", False):
            return  # already patched
        def _safe_deepcopy(self, memo):
            from matplotlib.path import Path as P
            return P(self.vertices.copy(),
                     self.codes.copy() if self.codes is not None else None,
                     readonly=False)
        _MplPath.__deepcopy__ = _safe_deepcopy
        _MplPath._py314_patched = True
    except Exception:
        pass  # if matplotlib isn't installed, nothing to patch


def _plot_degree_distribution(node_metrics: pd.DataFrame, out_path: str) -> None:
    """Plot in-degree and out-degree distributions."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        _apply_matplotlib_py314_patch()
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available — skipping degree distribution plot")
        return

    try:
        fig, axes = plt.subplots(1, 2, figsize=(12, 4))

        for ax, col, title in [
            (axes[0], "in_degree",  "In-degree distribution\n(# airports flying TO each airport)"),
            (axes[1], "out_degree", "Out-degree distribution\n(# airports each airport flies TO)"),
        ]:
            vals = node_metrics[col].values
            ax.hist(vals, bins=30, edgecolor="white", linewidth=0.5, color="#2196F3", alpha=0.85)
            ax.set_xlabel("Degree")
            ax.set_ylabel("Count")
            ax.set_title(title, fontsize=10)
            ax.axvline(vals.mean(), color="red", linestyle="--", linewidth=1,
                       label=f"Mean = {vals.mean():.1f}")
            ax.legend(fontsize=8)

        fig.suptitle("US Airport Flight Network — Degree Distribution (Jan/Jun/Jul/Aug 2024)",
                     fontsize=11, fontweight="bold")
        plt.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close("all")
        print(f"  Saved: {out_path}")
    except Exception as exc:
        print(f"  WARNING: degree distribution plot failed: {exc}")
        plt.close("all")


def _plot_top_hubs(node_metrics: pd.DataFrame, out_path: str, top_n: int = 20) -> None:
    """Plot top-N airports by total traffic strength and PageRank."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        _apply_matplotlib_py314_patch()
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available — skipping hub plot")
        return

    try:
        top = node_metrics.head(top_n).copy()
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # Total strength
        axes[0].barh(top["iata"][::-1], top["total_strength"][::-1],
                     color="#1976D2", alpha=0.85)
        axes[0].set_xlabel("Total flights (in + out)")
        axes[0].set_title(f"Top {top_n} airports by total traffic", fontsize=10)
        axes[0].tick_params(axis="y", labelsize=8)

        # PageRank
        top_pr = node_metrics.nlargest(top_n, "pagerank")
        axes[1].barh(top_pr["iata"][::-1], top_pr["pagerank"][::-1],
                     color="#388E3C", alpha=0.85)
        axes[1].set_xlabel("PageRank (power iteration)")
        axes[1].set_title(f"Top {top_n} airports by PageRank", fontsize=10)
        axes[1].tick_params(axis="y", labelsize=8)

        fig.suptitle("US Airport Flight Network — Hub Analysis (Jan/Jun/Jul/Aug 2024)",
                     fontsize=11, fontweight="bold")
        plt.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close("all")
        print(f"  Saved: {out_path}")
    except Exception as exc:
        print(f"  WARNING: hub plot failed: {exc}")
        plt.close("all")


def _plot_route_frequency(edge_df: pd.DataFrame, out_path: str, top_n: int = 25) -> None:
    """Plot top-N busiest directed routes by flight count."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        _apply_matplotlib_py314_patch()
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available — skipping route frequency plot")
        return

    try:
        top = edge_df.head(top_n).copy()
        top["route"] = top["origin"] + "→" + top["dest"]

        fig, ax = plt.subplots(figsize=(10, 8))
        bars = ax.barh(top["route"][::-1], top["flight_count"][::-1],
                       color="#E65100", alpha=0.85)
        ax.set_xlabel("Number of flights (Jan/Jun/Jul/Aug 2024)")
        ax.set_title(f"Top {top_n} Busiest Directed Routes", fontsize=12, fontweight="bold")
        ax.tick_params(axis="y", labelsize=8)

        # Annotate with mean departure delay
        for i, (_, row) in enumerate(top[::-1].iterrows()):
            if not pd.isna(row["mean_dep_delay_min"]):
                ax.text(row["flight_count"] + 20, i,
                        f"+{row['mean_dep_delay_min']:.1f} min",
                        va="center", fontsize=7, color="#555")

        plt.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close("all")
        print(f"  Saved: {out_path}")
    except Exception as exc:
        print(f"  WARNING: route frequency plot failed: {exc}")
        plt.close("all")


# ---------------------------------------------------------------------------
# 6. Report writer
# ---------------------------------------------------------------------------


def _write_network_report(
    summary: dict,
    node_metrics: pd.DataFrame,
    edge_df: pd.DataFrame,
    out_path: str,
) -> None:
    """Write a Markdown network analysis report."""
    top10 = node_metrics.head(10)
    top10_bc = node_metrics.nlargest(10, "betweenness_centrality")
    top10_pr = node_metrics.nlargest(10, "pagerank")

    lines = [
        "# Airport Network Analysis Report",
        "",
        "**Owned by:** Dhanush R (Person 3)",
        "**Study period:** January, June, July, August 2024",
        "**Data source:** `data/processed/flights_rotation_eligible.csv`",
        "",
        "---",
        "",
        "## 1. Network Definition",
        "",
        "| Property | Value |",
        "|---|---|",
        "| Node type | US airport (IATA code) |",
        "| Edge type | Directed (origin → destination) |",
        "| Edge weight | `flight_count` — total rotation-eligible flights on route |",
        f"| Nodes (airports) | **{summary['n_nodes']}** |",
        f"| Edges (directed routes) | **{summary['n_edges']}** |",
        f"| Total flights represented | **{summary['total_flights']:,}** |",
        f"| Network density | {summary['density']} |",
        f"| Weakly connected components | {summary['n_weakly_connected_components']} |",
        f"| Strongly connected components | {summary['n_strongly_connected_components']} |",
        f"| Mean in-degree | {summary['mean_in_degree']} |",
        f"| Mean out-degree | {summary['mean_out_degree']} |",
        "",
        "---",
        "",
        "## 2. ⚠️ Causal Disclaimer",
        "",
        "All metrics below are **descriptive** summaries of the flight schedule topology.",
        "High betweenness centrality or high PageRank at an airport does NOT imply that",
        "airport causes more delays across the network. Identifying causal delay",
        "propagation requires the aircraft-rotation pair analysis (Amulya, Person 2)",
        "and statistical modelling (Anagha, Person 4). These network metrics are",
        "control variables and descriptive context, not causal estimates.",
        "",
        "---",
        "",
        "## 3. Top 10 Airports by Total Traffic",
        "",
        "| IATA | In-strength | Out-strength | Total | In-deg | Out-deg |",
        "|---|---|---|---|---|---|",
    ]

    for _, row in top10.iterrows():
        lines.append(
            f"| **{row['iata']}** | {row['in_strength']:,} | {row['out_strength']:,} | "
            f"{row['total_strength']:,} | {row['in_degree']} | {row['out_degree']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 4. Top 10 Airports by Betweenness Centrality",
        "",
        "_Betweenness centrality (normalised): fraction of all shortest paths "
        "(unweighted) in the network that pass through this airport._",
        "",
        "| IATA | Betweenness | In-strength | Out-strength |",
        "|---|---|---|---|",
    ]

    for _, row in top10_bc.iterrows():
        lines.append(
            f"| **{row['iata']}** | {row['betweenness_centrality']:.4f} | "
            f"{row['in_strength']:,} | {row['out_strength']:,} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 5. Top 10 Airports by PageRank",
        "",
        "_PageRank (weighted by flight_count): airports receiving traffic from "
        "other high-traffic airports score higher._",
        "",
        "| IATA | PageRank | In-strength | Out-strength |",
        "|---|---|---|---|",
    ]

    for _, row in top10_pr.iterrows():
        lines.append(
            f"| **{row['iata']}** | {row['pagerank']:.6f} | "
            f"{row['in_strength']:,} | {row['out_strength']:,} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 6. Busiest Routes",
        "",
        "| Origin | Dest | Flights | Mean Dep Delay (min) |",
        "|---|---|---|---|",
    ]

    for _, row in edge_df.head(10).iterrows():
        lines.append(
            f"| {row['origin']} | {row['dest']} | {row['flight_count']:,} | "
            f"{row['mean_dep_delay_min']:.1f} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 7. Data Limitations",
        "",
        "- **4-month coverage:** January, June, July, August 2024 only.",
        "  Network topology for other months may differ.",
        "- **Rotation-eligible cohort only:** Cancelled and diverted flights are excluded.",
        "  These represent ~3–5% of BTS source records for these months.",
        "- **No international flights:** All airports are US domestic only.",
        "",
        "---",
        "",
        "## 8. Visualisations",
        "",
        "- `reports/network_degree_distribution.png` — in-degree and out-degree histograms",
        "- `reports/network_top_hubs.png` — top-20 airports by traffic and PageRank",
        "",
        f"*Report generated {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} UTC.*",
    ]

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(argv: list[str] | None = None) -> int:
    """CLI entry point for airport network analysis."""
    parser = argparse.ArgumentParser(
        description=(
            "Compute descriptive airport network metrics from rotation-eligible "
            "flight data.\n\n"
            "CAUTION: network metrics are descriptive only — they do NOT imply "
            "causal delay propagation."
        )
    )
    parser.add_argument(
        "--flights",
        default=os.path.join("data", "processed", "flights_rotation_eligible.csv"),
        help="Path to cleaned BTS flight CSV.",
    )
    parser.add_argument(
        "--edge-out",
        default=os.path.join("data", "processed", "network_edge_list.csv"),
        help="Destination for edge-list CSV.",
    )
    parser.add_argument(
        "--node-out",
        default=os.path.join("data", "processed", "network_node_metrics.csv"),
        help="Destination for node-metrics CSV.",
    )
    parser.add_argument(
        "--report",
        default=os.path.join("reports", "network_analysis_report.md"),
        help="Destination for Markdown report.",
    )
    args = parser.parse_args(argv)

    print("=== Airport Network Analysis ===")
    print(f"Input: {args.flights}")

    print("Step 1: Building edge list ...")
    edge_df = build_edge_list(args.flights)
    os.makedirs(os.path.dirname(os.path.abspath(args.edge_out)), exist_ok=True)
    edge_df.to_csv(args.edge_out, index=False)
    print(f"  {len(edge_df):,} directed edges -> {args.edge_out}")

    print("Step 2: Building network ...")
    G = build_flight_network(edge_df)
    summary = compute_network_summary(G, edge_df)
    print(f"  Nodes: {summary['n_nodes']}  Edges: {summary['n_edges']}")
    print(f"  Density: {summary['density']}  "
          f"WCC: {summary['n_weakly_connected_components']}  "
          f"SCC: {summary['n_strongly_connected_components']}")

    print("Step 3: Computing node metrics ...")
    node_metrics = compute_node_metrics(G)
    os.makedirs(os.path.dirname(os.path.abspath(args.node_out)), exist_ok=True)
    node_metrics.to_csv(args.node_out, index=False)
    print(f"  Node metrics -> {args.node_out}")

    print("Step 4: Generating visualisations ...")
    _plot_degree_distribution(node_metrics,
                              os.path.join("reports", "network_degree_distribution.png"))
    _plot_top_hubs(node_metrics,
                   os.path.join("reports", "network_top_hubs.png"))
    _plot_route_frequency(edge_df,
                          os.path.join("reports", "network_route_frequency.png"))

    print("Step 5: Writing report ...")
    _write_network_report(summary, node_metrics, edge_df, args.report)
    print(f"  Report -> {args.report}")

    print()
    print("=== Network Analysis Summary ===")
    print(f"  Nodes:   {summary['n_nodes']}")
    print(f"  Edges:   {summary['n_edges']}")
    print(f"  Flights: {summary['total_flights']:,}")
    print(f"  Busiest route: {summary['busiest_route']} "
          f"({summary['max_flights_single_route']:,} flights)")
    top1 = node_metrics.iloc[0]
    print(f"  Most-trafficked airport: {top1['iata']} "
          f"({top1['total_strength']:,} flights)")
    top1_bc = node_metrics.nlargest(1, 'betweenness_centrality').iloc[0]
    print(f"  Highest betweenness: {top1_bc['iata']} "
          f"({top1_bc['betweenness_centrality']:.4f})")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
