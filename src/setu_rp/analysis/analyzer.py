"""Analysis orchestrator: run_analysis() entry point."""

import logging
import sqlite3
from datetime import datetime, timezone

from setu_rp.analysis.bot_detection import NOT_BOT_SQL
from setu_rp.analysis.contributors import classify_contributor, compute_retention
from setu_rp.analysis.governance import analyze_governance
from setu_rp.analysis.metrics_rq1 import (
    compute_rejection,
    compute_review_iterations,
    compute_time_to_first_feedback,
    compute_time_to_first_human_feedback,
    compute_time_to_first_human_review,
    compute_time_to_merge,
)
from setu_rp.analysis.metrics_rq2 import count_comments_by_type
from setu_rp.analysis.sentiment import aggregate_pr_sentiment, compute_comment_sentiments
from setu_rp.analysis.schema import init_analysis_tables
from setu_rp.analysis.statistics import (
    benjamini_hochberg,
    choose_and_run_test,
    descriptive_stats,
    run_proportion_test,
    wilson_ci,
)
from setu_rp.analysis.time_windows import (
    TimeWindow,
    classify_pr_period,
    compute_time_windows,
)

logger = logging.getLogger(__name__)

CONTINUOUS_METRICS = [
    "time_to_merge_hours",
    "time_to_first_feedback_hours",
    "time_to_first_human_feedback_hours",
    "time_to_first_human_review_hours",
    "review_iterations",
    "total_human_comments",
    "total_bot_comments",
    "human_review_comment_count",
    "bot_review_comment_count",
    "avg_human_sentiment",
]


def run_analysis(
    conn: sqlite3.Connection,
    pre_months: int = 6,
    post_months: int = 6,
    bot_adoption_date: str | None = None,
    sensitivity_windows: list[int] | None = None,
):
    """Run the full analysis pipeline.

    1. Initialize analysis tables
    2. Compute time windows
    3. Create analysis_runs record
    4. Compute per-PR metrics (RQ1 + RQ2)
    5. Aggregate per-contributor metrics
    6. Compute descriptive statistics
    7. Run statistical tests
    8. Run sensitivity analysis
    """
    init_analysis_tables(conn)

    window = compute_time_windows(conn, pre_months, post_months, bot_adoption_date)
    logger.info(
        "Time windows: pre=[%s, %s), post=[%s, %s)",
        window.pre_start.isoformat(),
        window.pre_end.isoformat(),
        window.post_start.isoformat(),
        window.post_end.isoformat(),
    )

    # Clear previous results for this window configuration
    _clear_previous_run(conn, window)

    run_id = _create_analysis_run(conn, window)
    logger.info("Created analysis run %d", run_id)

    # Step 4: Per-PR metrics
    _compute_pr_metrics(conn, run_id, window)

    # Step 4b: Sentiment analysis
    compute_comment_sentiments(
        conn, run_id,
        window.pre_start.isoformat(), window.pre_end.isoformat(),
        window.post_start.isoformat(), window.post_end.isoformat(),
    )
    aggregate_pr_sentiment(conn, run_id)

    # Update run totals
    _update_run_totals(conn, run_id)

    # Step 5: Contributor metrics
    _compute_contributor_metrics(conn, run_id)
    compute_retention(conn, run_id)

    # Step 6: Descriptive statistics
    _compute_period_statistics(conn, run_id)

    # Step 7: Statistical tests
    _run_statistical_tests(conn, run_id)

    # Step 7b: FDR correction
    _apply_fdr_correction(conn, run_id)

    # Step 8: Sensitivity analysis
    if sensitivity_windows:
        _run_sensitivity_analysis(conn, run_id, window, sensitivity_windows)

    # Step 9: Governance analysis (graceful if no data)
    analyze_governance(conn, run_id, window)

    conn.commit()
    logger.info("Analysis complete for run %d", run_id)
    return run_id


def _clear_previous_run(conn: sqlite3.Connection, window: TimeWindow):
    """Remove previous analysis data to allow re-runs."""
    conn.execute("DELETE FROM governance_changes")
    conn.execute("DELETE FROM sensitivity_results")
    conn.execute("DELETE FROM statistical_tests")
    conn.execute("DELETE FROM period_statistics")
    conn.execute("DELETE FROM comment_sentiments")
    conn.execute("DELETE FROM contributor_metrics")
    conn.execute("DELETE FROM pr_metrics")
    conn.execute("DELETE FROM analysis_runs")


def _create_analysis_run(conn: sqlite3.Connection, window: TimeWindow) -> int:
    """Create an analysis_runs record and return its ID."""
    cursor = conn.execute(
        "INSERT INTO analysis_runs "
        "(run_at, bot_adoption_date, pre_window_months, post_window_months, "
        "pre_start, pre_end, post_start, post_end) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            window.bot_adoption_date.isoformat(),
            window.pre_months,
            window.post_months,
            window.pre_start.isoformat(),
            window.pre_end.isoformat(),
            window.post_start.isoformat(),
            window.post_end.isoformat(),
        ),
    )
    return cursor.lastrowid


def _compute_pr_metrics(
    conn: sqlite3.Connection, run_id: int, window: TimeWindow
):
    """Compute and store per-PR metrics for all PRs in the time windows."""
    prs = conn.execute(
        "SELECT pr.id, pr.number, pr.state, pr.author_id, pr.created_at, pr.merged_at, "
        "pr.additions, pr.deletions, pr.changed_files "
        "FROM pull_requests pr "
        "JOIN users u ON pr.author_id = u.id "
        "WHERE pr.created_at >= ? AND pr.created_at < ? "
        f"AND {NOT_BOT_SQL} "
        "ORDER BY pr.created_at",
        (window.pre_start.isoformat(), window.post_end.isoformat()),
    ).fetchall()

    count = 0
    for pr in prs:
        pr_dict = dict(pr)
        period = classify_pr_period(pr_dict["created_at"], window)
        if period is None:
            continue

        contributor_type = classify_contributor(
            conn, pr_dict["author_id"], pr_dict["created_at"]
        )
        ttm = compute_time_to_merge(pr_dict)
        ttff = compute_time_to_first_feedback(conn, pr_dict["id"], pr_dict["created_at"])
        ttff_human = compute_time_to_first_human_feedback(
            conn, pr_dict["id"], pr_dict["created_at"]
        )
        ttfr_human = compute_time_to_first_human_review(
            conn, pr_dict["id"], pr_dict["created_at"]
        )
        iterations = compute_review_iterations(conn, pr_dict["id"])
        rejected = compute_rejection(pr_dict)
        comments = count_comments_by_type(conn, pr_dict["id"])

        total_human = comments["human_review_comments"] + comments["human_issue_comments"]
        total_bot = comments["bot_review_comments"] + comments["bot_issue_comments"]

        conn.execute(
            "INSERT OR REPLACE INTO pr_metrics "
            "(pull_request_id, analysis_run_id, period, author_id, contributor_type, "
            "time_to_merge_hours, time_to_first_feedback_hours, "
            "time_to_first_human_feedback_hours, time_to_first_human_review_hours, "
            "review_iterations, "
            "was_rejected, human_review_comment_count, bot_review_comment_count, "
            "human_issue_comment_count, bot_issue_comment_count, "
            "total_human_comments, total_bot_comments, "
            "additions, deletions, changed_files) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pr_dict["id"],
                run_id,
                period,
                pr_dict["author_id"],
                contributor_type,
                ttm,
                ttff,
                ttff_human,
                ttfr_human,
                iterations,
                rejected,
                comments["human_review_comments"],
                comments["bot_review_comments"],
                comments["human_issue_comments"],
                comments["bot_issue_comments"],
                total_human,
                total_bot,
                pr_dict["additions"],
                pr_dict["deletions"],
                pr_dict["changed_files"],
            ),
        )
        count += 1

    logger.info("Computed metrics for %d PRs", count)


def _update_run_totals(conn: sqlite3.Connection, run_id: int):
    """Update the analysis_runs record with totals."""
    for period in ("pre", "post"):
        pr_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics WHERE analysis_run_id = ? AND period = ?",
            (run_id, period),
        ).fetchone()["cnt"]
        contributor_count = conn.execute(
            "SELECT COUNT(DISTINCT author_id) as cnt FROM pr_metrics "
            "WHERE analysis_run_id = ? AND period = ?",
            (run_id, period),
        ).fetchone()["cnt"]
        conn.execute(
            f"UPDATE analysis_runs SET total_prs_{period} = ?, total_contributors_{period} = ? "
            "WHERE id = ?",
            (pr_count, contributor_count, run_id),
        )


def _compute_contributor_metrics(conn: sqlite3.Connection, run_id: int):
    """Aggregate per-contributor per-period summary metrics."""
    rows = conn.execute(
        "SELECT author_id, period, contributor_type, "
        "COUNT(*) as pr_count, "
        "SUM(CASE WHEN time_to_merge_hours IS NOT NULL THEN 1 ELSE 0 END) as merged_count, "
        "AVG(time_to_merge_hours) as avg_ttm, "
        "AVG(review_iterations) as avg_iterations "
        "FROM pr_metrics WHERE analysis_run_id = ? "
        "GROUP BY author_id, period, contributor_type",
        (run_id,),
    ).fetchall()

    for row in rows:
        conn.execute(
            "INSERT OR REPLACE INTO contributor_metrics "
            "(analysis_run_id, user_id, period, contributor_type, "
            "pr_count, merged_pr_count, avg_time_to_merge_hours, avg_review_iterations) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                row["author_id"],
                row["period"],
                row["contributor_type"],
                row["pr_count"],
                row["merged_count"],
                row["avg_ttm"],
                row["avg_iterations"],
            ),
        )


def _compute_period_statistics(conn: sqlite3.Connection, run_id: int):
    """Compute descriptive statistics for each metric, period, contributor type."""
    contributor_types = ["all", "new", "established"]

    for metric in CONTINUOUS_METRICS:
        for period in ("pre", "post"):
            for ctype in contributor_types:
                where = "analysis_run_id = ? AND period = ?"
                params: list = [run_id, period]
                if ctype != "all":
                    where += " AND contributor_type = ?"
                    params.append(ctype)

                rows = conn.execute(
                    f"SELECT {metric} FROM pr_metrics WHERE {where} AND {metric} IS NOT NULL",
                    params,
                ).fetchall()
                values = [row[0] for row in rows]
                stats_dict = descriptive_stats(values)

                conn.execute(
                    "INSERT OR REPLACE INTO period_statistics "
                    "(analysis_run_id, metric_name, period, contributor_type, "
                    "n, mean, median, std_dev, min_val, max_val, q1, q3) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        metric,
                        period,
                        ctype,
                        stats_dict["n"],
                        stats_dict["mean"],
                        stats_dict["median"],
                        stats_dict["std_dev"],
                        stats_dict["min_val"],
                        stats_dict["max_val"],
                        stats_dict["q1"],
                        stats_dict["q3"],
                    ),
                )


def _run_statistical_tests(conn: sqlite3.Connection, run_id: int):
    """Run hypothesis tests for each metric and contributor type."""
    contributor_types = ["all", "new", "established"]

    for metric in CONTINUOUS_METRICS:
        for ctype in contributor_types:
            where_base = "analysis_run_id = ?"
            params_base: list = [run_id]
            if ctype != "all":
                where_base += " AND contributor_type = ?"
                params_base.append(ctype)

            pre_rows = conn.execute(
                f"SELECT {metric} FROM pr_metrics "
                f"WHERE {where_base} AND period = 'pre' AND {metric} IS NOT NULL",
                params_base,
            ).fetchall()
            post_rows = conn.execute(
                f"SELECT {metric} FROM pr_metrics "
                f"WHERE {where_base} AND period = 'post' AND {metric} IS NOT NULL",
                params_base,
            ).fetchall()

            pre_vals = [r[0] for r in pre_rows]
            post_vals = [r[0] for r in post_rows]

            result = choose_and_run_test(pre_vals, post_vals)
            result["metric_name"] = metric
            result["contributor_type"] = ctype
            result["analysis_run_id"] = run_id

            conn.execute(
                "INSERT OR REPLACE INTO statistical_tests "
                "(analysis_run_id, metric_name, contributor_type, test_name, "
                "statistic, p_value, effect_size, effect_size_type, "
                "ci_lower, ci_upper, pre_n, post_n, significant, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result["analysis_run_id"],
                    result["metric_name"],
                    result["contributor_type"],
                    result["test_name"],
                    result["statistic"],
                    result["p_value"],
                    result["effect_size"],
                    result["effect_size_type"],
                    result["ci_lower"],
                    result["ci_upper"],
                    result["pre_n"],
                    result["post_n"],
                    result["significant"],
                    result["notes"],
                ),
            )

    # Retention rate: within-period retention for new contributors
    # Compare pre-period vs post-period retention rates using proportion z-test
    pre_total = conn.execute(
        "SELECT COUNT(*) as cnt FROM contributor_metrics "
        "WHERE analysis_run_id = ? AND period = 'pre' AND contributor_type = 'new'",
        (run_id,),
    ).fetchone()["cnt"]
    pre_returned = conn.execute(
        "SELECT COUNT(*) as cnt FROM contributor_metrics "
        "WHERE analysis_run_id = ? AND period = 'pre' AND contributor_type = 'new' "
        "AND returned_in_period = 1",
        (run_id,),
    ).fetchone()["cnt"]

    post_total = conn.execute(
        "SELECT COUNT(*) as cnt FROM contributor_metrics "
        "WHERE analysis_run_id = ? AND period = 'post' AND contributor_type = 'new'",
        (run_id,),
    ).fetchone()["cnt"]
    post_returned = conn.execute(
        "SELECT COUNT(*) as cnt FROM contributor_metrics "
        "WHERE analysis_run_id = ? AND period = 'post' AND contributor_type = 'new' "
        "AND returned_in_period = 1",
        (run_id,),
    ).fetchone()["cnt"]

    result = run_proportion_test(pre_returned, pre_total, post_returned, post_total)

    pre_rate = pre_returned / pre_total if pre_total > 0 else 0.0
    post_rate = post_returned / post_total if post_total > 0 else 0.0
    pre_ci_lo, pre_ci_hi = wilson_ci(pre_returned, pre_total)
    post_ci_lo, post_ci_hi = wilson_ci(post_returned, post_total)

    conn.execute(
        "INSERT OR REPLACE INTO statistical_tests "
        "(analysis_run_id, metric_name, contributor_type, test_name, "
        "statistic, p_value, effect_size, effect_size_type, "
        "ci_lower, ci_upper, pre_n, post_n, significant, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            "retention_rate",
            "new",
            result["test_name"],
            result["statistic"],
            result["p_value"],
            result["effect_size"],
            result["effect_size_type"],
            result["ci_lower"],
            result["ci_upper"],
            result["pre_n"],
            result["post_n"],
            result["significant"],
            f"Pre: {pre_returned}/{pre_total} = {pre_rate:.3f} "
            f"[{pre_ci_lo:.3f}, {pre_ci_hi:.3f}], "
            f"Post: {post_returned}/{post_total} = {post_rate:.3f} "
            f"[{post_ci_lo:.3f}, {post_ci_hi:.3f}]",
        ),
    )

    # Proportion test for rejection rate (excluding open PRs — outcome unknown)
    for ctype in contributor_types:
        # Get pre/post rejection counts (only closed PRs: was_rejected IS NOT NULL)
        pre_where = "analysis_run_id = ? AND period = 'pre' AND was_rejected IS NOT NULL"
        pre_params: list = [run_id]
        if ctype != "all":
            pre_where += " AND contributor_type = ?"
            pre_params.append(ctype)

        pre_total_rej = conn.execute(
            f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {pre_where}", pre_params
        ).fetchone()["cnt"]
        pre_rejected = conn.execute(
            f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {pre_where} AND was_rejected = 1",
            pre_params,
        ).fetchone()["cnt"]

        post_where = "analysis_run_id = ? AND period = 'post' AND was_rejected IS NOT NULL"
        post_params_rej: list = [run_id]
        if ctype != "all":
            post_where += " AND contributor_type = ?"
            post_params_rej.append(ctype)

        post_total_rej = conn.execute(
            f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {post_where}", post_params_rej
        ).fetchone()["cnt"]
        post_rejected = conn.execute(
            f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {post_where} AND was_rejected = 1",
            post_params_rej,
        ).fetchone()["cnt"]

        result = run_proportion_test(pre_rejected, pre_total_rej, post_rejected, post_total_rej)
        conn.execute(
            "INSERT OR REPLACE INTO statistical_tests "
            "(analysis_run_id, metric_name, contributor_type, test_name, "
            "statistic, p_value, effect_size, effect_size_type, "
            "ci_lower, ci_upper, pre_n, post_n, significant, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                "rejection_rate",
                ctype,
                result["test_name"],
                result["statistic"],
                result["p_value"],
                result["effect_size"],
                result["effect_size_type"],
                result["ci_lower"],
                result["ci_upper"],
                result["pre_n"],
                result["post_n"],
                result["significant"],
                result["notes"],
            ),
        )


def _apply_fdr_correction(conn: sqlite3.Connection, run_id: int):
    """Apply Benjamini-Hochberg FDR correction to all statistical tests in a run."""
    rows = conn.execute(
        "SELECT id, p_value FROM statistical_tests WHERE analysis_run_id = ?",
        (run_id,),
    ).fetchall()

    p_values = [(row["id"], row["p_value"]) for row in rows]
    corrections = benjamini_hochberg(p_values)

    for row_id, adj_p, adj_sig in corrections:
        conn.execute(
            "UPDATE statistical_tests SET p_value_adjusted = ?, adjusted_significant = ? "
            "WHERE id = ?",
            (adj_p, 1 if adj_sig else 0, row_id),
        )


def _run_sensitivity_analysis(
    conn: sqlite3.Connection,
    run_id: int,
    base_window: TimeWindow,
    window_sizes: list[int],
):
    """Run analysis across multiple window sizes and record results."""
    logger.info("Running sensitivity analysis for windows: %s", window_sizes)

    for months in window_sizes:
        window = compute_time_windows(
            conn, months, months, base_window.bot_adoption_date.isoformat()
        )

        # Get PRs in this window (excluding bot authors)
        prs = conn.execute(
            "SELECT pr.id, pr.created_at FROM pull_requests pr "
            "JOIN users u ON pr.author_id = u.id "
            "WHERE pr.created_at >= ? AND pr.created_at < ? "
            f"AND {NOT_BOT_SQL}",
            (window.pre_start.isoformat(), window.post_end.isoformat()),
        ).fetchall()

        # Build metric arrays per period
        pre_ids, post_ids = [], []
        for pr in prs:
            period = classify_pr_period(pr["created_at"], window)
            if period == "pre":
                pre_ids.append(pr["id"])
            elif period == "post":
                post_ids.append(pr["id"])

        # Continuous metrics
        for metric in CONTINUOUS_METRICS:
            for ctype in ["all", "new", "established"]:
                pre_vals = _get_metric_values(conn, run_id, metric, pre_ids, ctype)
                post_vals = _get_metric_values(conn, run_id, metric, post_ids, ctype)

                pre_mean = float(sum(pre_vals) / len(pre_vals)) if pre_vals else None
                post_mean = float(sum(post_vals) / len(post_vals)) if post_vals else None

                test_result = choose_and_run_test(pre_vals, post_vals)

                conn.execute(
                    "INSERT OR REPLACE INTO sensitivity_results "
                    "(analysis_run_id, window_months, metric_name, contributor_type, "
                    "pre_mean, post_mean, effect_size, p_value, pre_n, post_n) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        months,
                        metric,
                        ctype,
                        pre_mean,
                        post_mean,
                        test_result["effect_size"],
                        test_result["p_value"],
                        len(pre_vals),
                        len(post_vals),
                    ),
                )

        # Rejection rate (proportion test) across window sizes
        for ctype in ["all", "new", "established"]:
            pre_rej = _get_rejection_counts(conn, run_id, pre_ids, ctype)
            post_rej = _get_rejection_counts(conn, run_id, post_ids, ctype)

            result = run_proportion_test(
                pre_rej["rejected"], pre_rej["total"],
                post_rej["rejected"], post_rej["total"],
            )
            pre_rate = pre_rej["rejected"] / pre_rej["total"] if pre_rej["total"] else None
            post_rate = post_rej["rejected"] / post_rej["total"] if post_rej["total"] else None

            conn.execute(
                "INSERT OR REPLACE INTO sensitivity_results "
                "(analysis_run_id, window_months, metric_name, contributor_type, "
                "pre_mean, post_mean, effect_size, p_value, pre_n, post_n) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, months, "rejection_rate", ctype,
                    pre_rate, post_rate,
                    result["effect_size"], result["p_value"],
                    pre_rej["total"], post_rej["total"],
                ),
            )

        # Retention rate across window sizes (within-period for new contributors)
        pre_ret = _get_retention_counts(conn, run_id, pre_ids)
        post_ret = _get_retention_counts(conn, run_id, post_ids)

        ret_result = run_proportion_test(
            pre_ret["returned"], pre_ret["total"],
            post_ret["returned"], post_ret["total"],
        )
        pre_rate = pre_ret["returned"] / pre_ret["total"] if pre_ret["total"] else None
        post_rate = post_ret["returned"] / post_ret["total"] if post_ret["total"] else None

        conn.execute(
            "INSERT OR REPLACE INTO sensitivity_results "
            "(analysis_run_id, window_months, metric_name, contributor_type, "
            "pre_mean, post_mean, effect_size, p_value, pre_n, post_n) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, months, "retention_rate", "new",
                pre_rate, post_rate,
                ret_result["effect_size"], ret_result["p_value"],
                pre_ret["total"], post_ret["total"],
            ),
        )

    logger.info("Sensitivity analysis complete")


def _get_metric_values(
    conn: sqlite3.Connection,
    run_id: int,
    metric: str,
    pr_ids: list[int],
    contributor_type: str,
) -> list[float]:
    """Get metric values for a set of PR IDs, filtered by contributor type."""
    if not pr_ids:
        return []
    placeholders = ",".join("?" for _ in pr_ids)
    where = f"pull_request_id IN ({placeholders}) AND analysis_run_id = ? AND {metric} IS NOT NULL"
    params: list = list(pr_ids) + [run_id]
    if contributor_type != "all":
        where += " AND contributor_type = ?"
        params.append(contributor_type)
    rows = conn.execute(f"SELECT {metric} FROM pr_metrics WHERE {where}", params).fetchall()
    return [row[0] for row in rows]


def _get_rejection_counts(
    conn: sqlite3.Connection,
    run_id: int,
    pr_ids: list[int],
    contributor_type: str,
) -> dict:
    """Get rejection counts for a set of PR IDs (excluding open PRs)."""
    if not pr_ids:
        return {"rejected": 0, "total": 0}
    placeholders = ",".join("?" for _ in pr_ids)
    where = (
        f"pull_request_id IN ({placeholders}) AND analysis_run_id = ? "
        "AND was_rejected IS NOT NULL"
    )
    params: list = list(pr_ids) + [run_id]
    if contributor_type != "all":
        where += " AND contributor_type = ?"
        params.append(contributor_type)
    total = conn.execute(
        f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {where}", params
    ).fetchone()["cnt"]
    rejected = conn.execute(
        f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {where} AND was_rejected = 1", params
    ).fetchone()["cnt"]
    return {"rejected": rejected, "total": total}


def _get_retention_counts(
    conn: sqlite3.Connection,
    run_id: int,
    pr_ids: list[int],
) -> dict:
    """Get within-period retention counts for new contributors.

    A new contributor "returned" if they have 2+ total PRs in the set
    (their first 'new' PR plus at least one more 'established' PR).
    """
    if not pr_ids:
        return {"returned": 0, "total": 0}

    placeholders = ",".join("?" for _ in pr_ids)
    params: list = list(pr_ids) + [run_id]

    # Find authors who have at least one 'new' PR in this set
    new_authors = conn.execute(
        f"SELECT DISTINCT author_id FROM pr_metrics "
        f"WHERE pull_request_id IN ({placeholders}) AND analysis_run_id = ? "
        f"AND contributor_type = 'new'",
        params,
    ).fetchall()
    new_author_ids = {r["author_id"] for r in new_authors}

    if not new_author_ids:
        return {"returned": 0, "total": 0}

    # Count total PRs (any type) per author in this set, filtered to new authors
    all_rows = conn.execute(
        f"SELECT author_id, COUNT(*) as cnt FROM pr_metrics "
        f"WHERE pull_request_id IN ({placeholders}) AND analysis_run_id = ? "
        f"GROUP BY author_id",
        params,
    ).fetchall()

    total = len(new_author_ids)
    returned = sum(1 for r in all_rows if r["author_id"] in new_author_ids and r["cnt"] >= 2)
    return {"returned": returned, "total": total}
