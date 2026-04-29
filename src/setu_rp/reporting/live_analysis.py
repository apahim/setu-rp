"""Live recomputation engine for dynamic dashboard parameters.

When the user adjusts time-window sliders in the dashboard, this module
re-slices raw PR data from the database and recomputes statistics without
needing to re-run the full analysis pipeline.
"""

import sqlite3

import streamlit as st

from setu_rp.analysis.statistics import (
    choose_and_run_test,
    descriptive_stats,
    run_proportion_test,
)
from setu_rp.analysis.time_windows import TimeWindow, compute_time_windows


def get_dynamic_window(
    conn: sqlite3.Connection, pre_months: int, post_months: int,
    bot_adoption_date: str | None = None,
) -> TimeWindow:
    """Compute time-window boundaries from the adoption date in the DB."""
    return compute_time_windows(conn, pre_months, post_months, bot_adoption_date)


def _build_contributor_filter(contributor_type: str) -> tuple[str, list]:
    """Build SQL WHERE clause fragment for contributor type filtering."""
    if contributor_type != "all":
        return " AND pm.contributor_type = ?", [contributor_type]
    return "", []


@st.cache_data(ttl=300)
def get_pr_data(
    _conn: sqlite3.Connection,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    contributor_type: str,
    run_id: int,
) -> dict[str, dict[str, list]]:
    """Query pr_metrics joined with pull_requests, split by dynamic window.

    Returns dict keyed by metric name, each containing {"pre": [...], "post": [...]}.
    """
    metrics = [
        "time_to_merge_hours",
        "time_to_first_human_feedback_hours",
        "time_to_first_human_review_hours",
        "time_to_first_feedback_hours",
        "review_iterations",
        "total_human_comments",
        "total_bot_comments",
        "human_review_comment_count",
        "bot_review_comment_count",
        "avg_human_sentiment",
        "was_rejected",
    ]

    ct_clause, ct_params = _build_contributor_filter(contributor_type)

    result: dict[str, dict[str, list]] = {m: {"pre": [], "post": []} for m in metrics}

    for period, start, end in [("pre", pre_start, pre_end), ("post", post_start, post_end)]:
        cols = ", ".join(f"pm.{m}" for m in metrics)
        query = (
            f"SELECT {cols} FROM pr_metrics pm "
            f"JOIN pull_requests p ON pm.pull_request_id = p.id "
            f"WHERE pm.analysis_run_id = ? "
            f"AND p.created_at >= ? AND p.created_at < ? "
            f"{ct_clause}"
        )
        params: list = [run_id, start, end] + ct_params
        rows = _conn.execute(query, params).fetchall()

        for row in rows:
            for m in metrics:
                val = row[m]
                if val is not None:
                    result[m][period].append(val)

    return result


@st.cache_data(ttl=300)
def compute_live_stats(
    _conn: sqlite3.Connection,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    contributor_type: str,
    run_id: int,
) -> dict[str, dict[str, dict]]:
    """Compute descriptive statistics for each metric and period.

    Returns dict keyed by metric name, each containing {"pre": stats_dict, "post": stats_dict}.
    """
    pr_data = get_pr_data(_conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id)
    result = {}
    for metric, periods in pr_data.items():
        result[metric] = {
            "pre": descriptive_stats(periods["pre"]),
            "post": descriptive_stats(periods["post"]),
        }
    return result


@st.cache_data(ttl=300)
def compute_live_tests(
    _conn: sqlite3.Connection,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    contributor_type: str,
    run_id: int,
) -> dict[str, dict]:
    """Run statistical tests for each continuous metric.

    Returns dict keyed by metric name with test result dicts.
    """
    pr_data = get_pr_data(_conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id)
    continuous_metrics = [
        "time_to_merge_hours",
        "time_to_first_human_feedback_hours",
        "time_to_first_human_review_hours",
        "time_to_first_feedback_hours",
        "review_iterations",
        "total_human_comments",
        "total_bot_comments",
        "human_review_comment_count",
        "bot_review_comment_count",
        "avg_human_sentiment",
    ]
    result = {}
    for metric in continuous_metrics:
        pre_vals = pr_data[metric]["pre"]
        post_vals = pr_data[metric]["post"]
        test_result = choose_and_run_test(pre_vals, post_vals)
        test_result["metric_name"] = metric
        test_result["contributor_type"] = contributor_type
        result[metric] = test_result
    return result


@st.cache_data(ttl=300)
def compute_live_rejection(
    _conn: sqlite3.Connection,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    contributor_type: str,
    run_id: int,
) -> dict:
    """Compute rejection rate proportion test for dynamic window."""
    ct_clause, ct_params = _build_contributor_filter(contributor_type)

    counts = {}
    for period, start, end in [("pre", pre_start, pre_end), ("post", post_start, post_end)]:
        query = (
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN pm.was_rejected = 1 THEN 1 ELSE 0 END) as rejected "
            "FROM pr_metrics pm "
            "JOIN pull_requests p ON pm.pull_request_id = p.id "
            "WHERE pm.analysis_run_id = ? "
            "AND p.created_at >= ? AND p.created_at < ? "
            f"{ct_clause}"
        )
        params: list = [run_id, start, end] + ct_params
        row = _conn.execute(query, params).fetchone()
        counts[period] = {"total": row["total"], "rejected": row["rejected"] or 0}

    test = run_proportion_test(
        counts["pre"]["rejected"],
        counts["pre"]["total"],
        counts["post"]["rejected"],
        counts["post"]["total"],
    )
    test["pre_rate"] = counts["pre"]["rejected"] / counts["pre"]["total"] if counts["pre"]["total"] > 0 else 0
    test["post_rate"] = counts["post"]["rejected"] / counts["post"]["total"] if counts["post"]["total"] > 0 else 0
    test["pre_total"] = counts["pre"]["total"]
    test["post_total"] = counts["post"]["total"]
    test["pre_rejected"] = counts["pre"]["rejected"]
    test["post_rejected"] = counts["post"]["rejected"]
    return test


@st.cache_data(ttl=300)
def compute_live_retention(
    _conn: sqlite3.Connection,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    contributor_type: str,
    run_id: int,
) -> list[dict]:
    """Compute within-period retention rates for dynamic window.

    Retention = proportion of new contributors with 2+ PRs in the same period.
    Returns a list of dicts with one row per period.
    """
    results = []

    for period, start, end in [("Pre", pre_start, pre_end), ("Post", post_start, post_end)]:
        total, returned = _live_retention_for_period(_conn, run_id, start, end)
        rate = returned / total if total > 0 else 0

        results.append({
            "Period": period,
            "New Contributors": total,
            "Returned (2+ PRs)": returned,
            "Retention Rate": f"{rate:.1%}",
        })

    return results


def _live_retention_for_period(
    conn: sqlite3.Connection, run_id: int, start: str, end: str
) -> tuple[int, int]:
    """Count new contributors and how many submitted 2+ total PRs in a period."""
    # Find authors with at least one 'new' PR in this period
    new_authors = conn.execute(
        "SELECT DISTINCT pm.author_id FROM pr_metrics pm "
        "JOIN pull_requests p ON pm.pull_request_id = p.id "
        "WHERE pm.analysis_run_id = ? "
        "AND p.created_at >= ? AND p.created_at < ? "
        "AND pm.contributor_type = 'new'",
        [run_id, start, end],
    ).fetchall()
    new_ids = {r["author_id"] for r in new_authors}
    if not new_ids:
        return 0, 0

    # Count total PRs (any type) per author in this period
    all_rows = conn.execute(
        "SELECT pm.author_id, COUNT(*) as cnt FROM pr_metrics pm "
        "JOIN pull_requests p ON pm.pull_request_id = p.id "
        "WHERE pm.analysis_run_id = ? "
        "AND p.created_at >= ? AND p.created_at < ? "
        "GROUP BY pm.author_id",
        [run_id, start, end],
    ).fetchall()

    returned = sum(1 for r in all_rows if r["author_id"] in new_ids and r["cnt"] >= 2)
    return len(new_ids), returned


@st.cache_data(ttl=300)
def compute_live_retention_test(
    _conn: sqlite3.Connection,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    contributor_type: str,
    run_id: int,
) -> dict:
    """Compute retention proportion test for dynamic window (pre vs post)."""
    counts = {}
    for period, start, end in [("pre", pre_start, pre_end), ("post", post_start, post_end)]:
        total, returned = _live_retention_for_period(_conn, run_id, start, end)
        counts[period] = {"total": total, "returned": returned}

    return run_proportion_test(
        counts["pre"]["returned"], counts["pre"]["total"],
        counts["post"]["returned"], counts["post"]["total"],
    )


@st.cache_data(ttl=300)
def get_monthly_pr_counts(
    _conn: sqlite3.Connection,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    run_id: int,
) -> list[dict]:
    """Get monthly PR counts for the dynamic window."""
    rows = _conn.execute(
        "SELECT strftime('%%Y-%%m', p.created_at) as month, "
        "CASE WHEN p.created_at < ? THEN 'pre' ELSE 'post' END as period, "
        "COUNT(*) as cnt "
        "FROM pr_metrics pm "
        "JOIN pull_requests p ON pm.pull_request_id = p.id "
        "WHERE pm.analysis_run_id = ? "
        "AND p.created_at >= ? AND p.created_at < ? "
        "GROUP BY month, period ORDER BY month",
        (pre_end, run_id, pre_start, post_end),
    ).fetchall()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def get_monthly_contributors(
    _conn: sqlite3.Connection,
    pre_start: str,
    post_end: str,
    run_id: int,
) -> list[dict]:
    """Get monthly unique contributor counts for the dynamic window."""
    rows = _conn.execute(
        "SELECT strftime('%%Y-%%m', p.created_at) as month, "
        "COUNT(DISTINCT pm.author_id) as contributors "
        "FROM pr_metrics pm "
        "JOIN pull_requests p ON pm.pull_request_id = p.id "
        "WHERE pm.analysis_run_id = ? "
        "AND p.created_at >= ? AND p.created_at < ? "
        "GROUP BY month ORDER BY month",
        (run_id, pre_start, post_end),
    ).fetchall()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def get_monthly_comments(
    _conn: sqlite3.Connection,
    pre_start: str,
    post_end: str,
    run_id: int,
) -> list[dict]:
    """Get monthly average comment counts for the dynamic window."""
    rows = _conn.execute(
        "SELECT strftime('%%Y-%%m', p.created_at) as month, "
        "AVG(pm.total_human_comments) as avg_human, "
        "AVG(pm.total_bot_comments) as avg_bot "
        "FROM pr_metrics pm "
        "JOIN pull_requests p ON pm.pull_request_id = p.id "
        "WHERE pm.analysis_run_id = ? "
        "AND p.created_at >= ? AND p.created_at < ? "
        "GROUP BY month ORDER BY month",
        (run_id, pre_start, post_end),
    ).fetchall()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def get_pr_values_for_boxplot(
    _conn: sqlite3.Connection,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    metric: str,
    contributor_type: str,
    run_id: int,
) -> dict[str, list[float]]:
    """Get raw metric values split by period for box/violin plots."""
    ct_clause, ct_params = _build_contributor_filter(contributor_type)
    result: dict[str, list[float]] = {"pre": [], "post": []}

    for period, start, end in [("pre", pre_start, pre_end), ("post", post_start, post_end)]:
        query = (
            f"SELECT pm.{metric} as val FROM pr_metrics pm "
            f"JOIN pull_requests p ON pm.pull_request_id = p.id "
            f"WHERE pm.analysis_run_id = ? "
            f"AND p.created_at >= ? AND p.created_at < ? "
            f"AND pm.{metric} IS NOT NULL "
            f"{ct_clause}"
        )
        params: list = [run_id, start, end] + ct_params
        rows = _conn.execute(query, params).fetchall()
        result[period] = [r["val"] for r in rows]

    return result


def is_dynamic(run, pre_months: int, post_months: int) -> bool:
    """Check whether the requested window differs from the stored run."""
    return run["pre_window_months"] != pre_months or run["post_window_months"] != post_months


def get_window_strings(window: TimeWindow) -> tuple[str, str, str, str]:
    """Convert TimeWindow boundaries to ISO strings for SQL queries."""
    return (
        window.pre_start.isoformat(),
        window.pre_end.isoformat(),
        window.post_start.isoformat(),
        window.post_end.isoformat(),
    )
