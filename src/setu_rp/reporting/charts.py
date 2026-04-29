"""Reusable chart functions for both interactive (plotly) and static (matplotlib) output."""

import sqlite3

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

matplotlib.use("Agg")


def box_plot_pre_post(
    conn: sqlite3.Connection,
    run_id: int,
    metric: str,
    title: str,
    ylabel: str,
    contributor_type: str = "all",
    output_path: str | None = None,
):
    """Create a box plot comparing pre vs post for a continuous metric.

    Args:
        conn: Database connection.
        run_id: Analysis run ID.
        metric: Column name in pr_metrics.
        title: Chart title.
        ylabel: Y-axis label.
        contributor_type: Filter by contributor type ('all', 'new', 'established').
        output_path: If set, save figure to this path. Otherwise return the figure.

    Returns:
        matplotlib Figure.
    """
    data = {}
    for period in ("pre", "post"):
        where = "analysis_run_id = ? AND period = ? AND {} IS NOT NULL".format(metric)
        params: list = [run_id, period]
        if contributor_type != "all":
            where += " AND contributor_type = ?"
            params.append(contributor_type)
        rows = conn.execute(
            f"SELECT {metric} FROM pr_metrics WHERE {where}", params
        ).fetchall()
        data[period] = [r[0] for r in rows]

    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(
        [data.get("pre", []), data.get("post", [])],
        labels=["Pre-bot", "Post-bot"],
        patch_artist=True,
    )
    colors = ["#4C72B0", "#DD8452"]
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    return fig


def bar_chart_rates(
    labels: list[str],
    pre_rates: list[float],
    post_rates: list[float],
    title: str,
    ylabel: str,
    output_path: str | None = None,
):
    """Create a grouped bar chart comparing pre/post rates.

    Args:
        labels: Category labels (e.g., ['All', 'New', 'Established']).
        pre_rates: Pre-period rates.
        post_rates: Post-period rates.
        title: Chart title.
        ylabel: Y-axis label.
        output_path: If set, save figure to this path.

    Returns:
        matplotlib Figure.
    """
    x = np.arange(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x - width / 2, pre_rates, width, label="Pre-bot", color="#4C72B0", alpha=0.7)
    ax.bar(x + width / 2, post_rates, width, label="Post-bot", color="#DD8452", alpha=0.7)

    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    return fig


def timeline_chart(
    conn: sqlite3.Connection,
    run_id: int,
    output_path: str | None = None,
):
    """Create a timeline showing pre/post windows and PR density.

    Returns:
        matplotlib Figure.
    """
    run = conn.execute(
        "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
    ).fetchone()

    # Monthly PR counts (join with pull_requests for created_at)
    rows = conn.execute(
        "SELECT strftime('%Y-%m', p.created_at) as month, COUNT(*) as cnt "
        "FROM pr_metrics pm "
        "JOIN pull_requests p ON pm.pull_request_id = p.id "
        "WHERE pm.analysis_run_id = ? "
        "GROUP BY month ORDER BY month",
        (run_id,),
    ).fetchall()

    months = [r["month"] for r in rows]
    counts = [r["cnt"] for r in rows]

    fig, ax = plt.subplots(figsize=(12, 5))
    colors = []
    adoption = run["bot_adoption_date"][:7]  # YYYY-MM
    for m in months:
        colors.append("#4C72B0" if m < adoption else "#DD8452")

    ax.bar(range(len(months)), counts, color=colors, alpha=0.7)
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels(months, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("PR Count")
    ax.set_title("Monthly PR Activity (Blue=Pre, Orange=Post)")

    # Add adoption date line
    if adoption in months:
        idx = months.index(adoption)
        ax.axvline(x=idx - 0.5, color="red", linestyle="--", linewidth=2, label="Bot Adoption")
        ax.legend()

    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    return fig


def sensitivity_line_chart(
    conn: sqlite3.Connection,
    run_id: int,
    metric: str,
    contributor_type: str = "all",
    output_path: str | None = None,
):
    """Line chart of effect size and p-value across window sizes.

    Returns:
        matplotlib Figure.
    """
    rows = conn.execute(
        "SELECT window_months, effect_size, p_value FROM sensitivity_results "
        "WHERE analysis_run_id = ? AND metric_name = ? AND contributor_type = ? "
        "ORDER BY window_months",
        (run_id, metric, contributor_type),
    ).fetchall()

    if not rows:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No sensitivity data", ha="center", va="center")
        return fig

    windows = [r["window_months"] for r in rows]
    effects = [r["effect_size"] or 0 for r in rows]
    pvals = [r["p_value"] or 1 for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    ax1.plot(windows, effects, "o-", color="#4C72B0")
    ax1.set_xlabel("Window Size (months)")
    ax1.set_ylabel("Effect Size")
    ax1.set_title(f"Effect Size: {metric}")
    ax1.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    ax1.grid(alpha=0.3)

    ax2.plot(windows, pvals, "o-", color="#DD8452")
    ax2.axhline(y=0.05, color="red", linestyle="--", alpha=0.5, label="p=0.05")
    ax2.set_xlabel("Window Size (months)")
    ax2.set_ylabel("p-value")
    ax2.set_title(f"p-value: {metric}")
    ax2.legend()
    ax2.grid(alpha=0.3)

    fig.suptitle(f"Sensitivity Analysis: {metric} ({contributor_type})")
    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    return fig
