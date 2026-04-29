"""Dashboard page: RQ1 Development Efficiency metrics."""

import sqlite3

import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def _format_test_result(test: dict) -> str:
    """Format a statistical test result dict as a markdown string."""
    adj_sig = test.get("adjusted_significant")
    if adj_sig is not None:
        sig_color = "🟢" if adj_sig else "🔴"
    else:
        sig_color = "🟢" if test.get("significant") else "🔴"
    p_val = test.get("p_value")
    adj_p = test.get("p_value_adjusted")
    e_val = test.get("effect_size")
    p_str = f"{p_val:.4f}" if p_val is not None else "N/A"
    e_str = f"{e_val:.4f}" if e_val is not None else "N/A"
    e_type = test.get("effect_size_type") or "N/A"
    result = (
        f"**Test:** {test.get('test_name', 'N/A')} | "
        f"**p-value:** {p_str}"
    )
    if adj_p is not None:
        result += f" (FDR-adjusted: {adj_p:.4f})"
    result += f" {sig_color} | **Effect size:** {e_str} ({e_type})"
    return result


def render(conn: sqlite3.Connection, run_id: int, contributor_type: str,
           pre_months: int, post_months: int):
    st.title("RQ1: Development Efficiency")

    st.info(
        "These metrics capture how quickly and smoothly PRs move through code review. "
        "**Time to Merge (TTM)**, **Time to First Human Feedback**, and "
        "**Time to First Human Review** are measured in hours; "
        "**Review Iterations** counts round-trips. Human-only metrics exclude bot accounts "
        "(e.g. CodeRabbit, CI bots) to isolate genuine reviewer responsiveness. "
        "The **Rejection Rate** tracks the proportion of closed-unmerged PRs "
        "(open PRs are excluded as their outcome is unknown)."
    )

    run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    from setu_rp.reporting.live_analysis import (
        compute_live_rejection,
        compute_live_stats,
        compute_live_tests,
        get_dynamic_window,
        get_pr_values_for_boxplot,
        get_window_strings,
        is_dynamic,
    )

    dynamic = is_dynamic(run, pre_months, post_months)

    if dynamic:
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        pre_start, pre_end, post_start, post_end = get_window_strings(window)
        live_stats = compute_live_stats(conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id)
        live_tests = compute_live_tests(conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id)

    metrics = [
        ("time_to_merge_hours", "Time to Merge (hours)"),
        ("time_to_first_human_feedback_hours", "Time to First Human Feedback (hours)"),
        ("time_to_first_human_review_hours", "Time to First Human Review (hours)"),
        ("time_to_first_feedback_hours", "Time to First Feedback — all users (hours)"),
        ("review_iterations", "Review Iterations"),
    ]

    for metric, label in metrics:
        st.subheader(label)

        if dynamic:
            # Build stats dataframe from live computation
            stats_rows = []
            for period in ("pre", "post"):
                s = live_stats[metric][period]
                s["period"] = period
                stats_rows.append(s)
            stats_df = pd.DataFrame(stats_rows)
            display_cols = ["period", "n", "mean", "median", "std_dev", "q1", "q3"]
            available = [c for c in display_cols if c in stats_df.columns]
            st.dataframe(stats_df[available], use_container_width=True, hide_index=True)
        else:
            stats = conn.execute(
                "SELECT * FROM period_statistics "
                "WHERE analysis_run_id = ? AND metric_name = ? AND contributor_type = ?",
                (run_id, metric, contributor_type),
            ).fetchall()
            if stats:
                stats_df = pd.DataFrame([dict(s) for s in stats])
                display_cols = ["period", "n", "mean", "median", "std_dev", "q1", "q3"]
                available = [c for c in display_cols if c in stats_df.columns]
                st.dataframe(stats_df[available], use_container_width=True, hide_index=True)

        # Box/violin plot
        if dynamic:
            box_data = get_pr_values_for_boxplot(
                conn, pre_start, pre_end, post_start, post_end, metric, contributor_type, run_id
            )
        else:
            box_data = {"pre": [], "post": []}
            for period in ("pre", "post"):
                where = "analysis_run_id = ? AND period = ? AND {} IS NOT NULL".format(metric)
                params: list = [run_id, period]
                if contributor_type != "all":
                    where += " AND contributor_type = ?"
                    params.append(contributor_type)
                rows = conn.execute(
                    f"SELECT {metric} as val FROM pr_metrics WHERE {where}", params
                ).fetchall()
                box_data[period] = [r["val"] for r in rows]

        if HAS_PLOTLY and (box_data["pre"] or box_data["post"]):
            fig = go.Figure()
            if box_data["pre"]:
                fig.add_trace(go.Box(y=box_data["pre"], name="Pre", marker_color="#636EFA"))
            if box_data["post"]:
                fig.add_trace(go.Box(y=box_data["post"], name="Post", marker_color="#EF553B"))
            fig.update_layout(
                yaxis_title=label,
                height=350,
                showlegend=True,
            )
            st.plotly_chart(fig, use_container_width=True, key=f"box_{metric}")
        else:
            col1, col2 = st.columns(2)
            for period, col in [("pre", col1), ("post", col2)]:
                values = box_data[period]
                if values:
                    import numpy as np
                    with col:
                        st.metric(f"{period.title()} Median", f"{float(np.median(values)):.1f}")

        # Statistical test
        if dynamic:
            test = live_tests.get(metric)
            if test:
                st.markdown(_format_test_result(test))
        else:
            test = conn.execute(
                "SELECT * FROM statistical_tests "
                "WHERE analysis_run_id = ? AND metric_name = ? AND contributor_type = ?",
                (run_id, metric, contributor_type),
            ).fetchone()
            if test:
                st.markdown(_format_test_result(dict(test)))

        st.divider()

    # Rejection rate
    st.subheader("PR Rejection Rate")
    st.caption(
        "The rejection rate is the proportion of PRs that were closed without merging. "
        "A two-proportion z-test checks whether rejection rates differ significantly "
        "between pre and post periods."
    )

    if dynamic:
        rej = compute_live_rejection(conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id)
        st.metric("Pre Rejection Rate", f"{rej['pre_rate']:.1%}")
        st.metric("Post Rejection Rate", f"{rej['post_rate']:.1%}")
        p_str = f"{rej['p_value']:.4f}" if rej.get("p_value") is not None else "N/A"
        st.markdown(f"**Proportion z-test p-value:** {p_str}")
    else:
        test = conn.execute(
            "SELECT * FROM statistical_tests "
            "WHERE analysis_run_id = ? AND metric_name = 'rejection_rate' AND contributor_type = ?",
            (run_id, contributor_type),
        ).fetchone()

        for period in ("pre", "post"):
            where = "analysis_run_id = ? AND period = ?"
            params_rej: list = [run_id, period]
            if contributor_type != "all":
                where += " AND contributor_type = ?"
                params_rej.append(contributor_type)

            total = conn.execute(
                f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {where}", params_rej
            ).fetchone()["cnt"]
            rejected = conn.execute(
                f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {where} AND was_rejected = 1",
                params_rej,
            ).fetchone()["cnt"]
            rate = rejected / total if total > 0 else 0
            st.metric(f"{period.title()} Rejection Rate", f"{rate:.1%}")

        if test:
            p_str = f"{test['p_value']:.4f}" if test["p_value"] is not None else "N/A"
            st.markdown(f"**Proportion z-test p-value:** {p_str}")
