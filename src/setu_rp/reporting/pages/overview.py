"""Dashboard page: Dataset overview and summary."""

import sqlite3

import pandas as pd
import streamlit as st

try:
    import plotly.express as px

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def render(conn: sqlite3.Connection, run_id: int, contributor_type: str,
           pre_months: int, post_months: int):
    st.title("Dataset Overview")

    run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    from setu_rp.reporting.live_analysis import (
        get_dynamic_window,
        get_monthly_pr_counts,
        get_window_strings,
        is_dynamic,
    )

    dynamic = is_dynamic(run, pre_months, post_months)

    if dynamic:
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        pre_start, pre_end, post_start, post_end = get_window_strings(window)
        monthly_data = get_monthly_pr_counts(conn, pre_start, pre_end, post_start, post_end, run_id)

        # Count PRs and contributors from live data
        ct_clause = ""
        ct_params: list = []
        if contributor_type != "all":
            ct_clause = " AND pm.contributor_type = ?"
            ct_params = [contributor_type]

        pre_prs = conn.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics pm "
            "JOIN pull_requests p ON pm.pull_request_id = p.id "
            f"WHERE pm.analysis_run_id = ? AND p.created_at >= ? AND p.created_at < ? {ct_clause}",
            [run_id, pre_start, pre_end] + ct_params,
        ).fetchone()["cnt"]
        post_prs = conn.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics pm "
            "JOIN pull_requests p ON pm.pull_request_id = p.id "
            f"WHERE pm.analysis_run_id = ? AND p.created_at >= ? AND p.created_at < ? {ct_clause}",
            [run_id, post_start, post_end] + ct_params,
        ).fetchone()["cnt"]
        pre_contribs = conn.execute(
            "SELECT COUNT(DISTINCT pm.author_id) as cnt FROM pr_metrics pm "
            "JOIN pull_requests p ON pm.pull_request_id = p.id "
            f"WHERE pm.analysis_run_id = ? AND p.created_at >= ? AND p.created_at < ? {ct_clause}",
            [run_id, pre_start, pre_end] + ct_params,
        ).fetchone()["cnt"]
        post_contribs = conn.execute(
            "SELECT COUNT(DISTINCT pm.author_id) as cnt FROM pr_metrics pm "
            "JOIN pull_requests p ON pm.pull_request_id = p.id "
            f"WHERE pm.analysis_run_id = ? AND p.created_at >= ? AND p.created_at < ? {ct_clause}",
            [run_id, post_start, post_end] + ct_params,
        ).fetchone()["cnt"]

        window_info = {
            "pre_start": pre_start[:10],
            "pre_end": pre_end[:10],
            "post_start": post_start[:10],
            "post_end": post_end[:10],
            "pre_months": pre_months,
            "post_months": post_months,
            "bot_adoption": run["bot_adoption_date"][:10],
        }
    else:
        pre_prs = run["total_prs_pre"] or 0
        post_prs = run["total_prs_post"] or 0
        pre_contribs = run["total_contributors_pre"] or 0
        post_contribs = run["total_contributors_post"] or 0
        window_info = {
            "pre_start": run["pre_start"][:10],
            "pre_end": run["pre_end"][:10],
            "post_start": run["post_start"][:10],
            "post_end": run["post_end"][:10],
            "pre_months": run["pre_window_months"],
            "post_months": run["post_window_months"],
            "bot_adoption": run["bot_adoption_date"][:10],
        }

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total PRs (Pre)", pre_prs)
    col2.metric("Total PRs (Post)", post_prs)
    col3.metric("Contributors (Pre)", pre_contribs)
    col4.metric("Contributors (Post)", post_contribs)

    st.subheader("Time Windows")
    if dynamic:
        st.caption("Showing dynamically computed windows (sliders differ from stored analysis run).")
    st.markdown(f"""
    | | Start | End | Months |
    |---|---|---|---|
    | **Pre-period** | {window_info['pre_start']} | {window_info['pre_end']} | {window_info['pre_months']} |
    | **Post-period** | {window_info['post_start']} | {window_info['post_end']} | {window_info['post_months']} |
    | **Bot Adoption** | {window_info['bot_adoption']} | | |
    """)

    # Monthly PR counts
    st.subheader("Monthly PR Activity")
    st.info(
        "This chart shows the number of pull requests opened each month, "
        "coloured by pre/post period. A vertical change at the adoption boundary "
        "may suggest an immediate effect on contribution volume."
    )

    if dynamic:
        rows = monthly_data
    else:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT strftime('%Y-%m', p.created_at) as month, pm.period, COUNT(*) as cnt "
                "FROM pr_metrics pm "
                "JOIN pull_requests p ON pm.pull_request_id = p.id "
                "WHERE pm.analysis_run_id = ? "
                "GROUP BY month, pm.period ORDER BY month",
                (run_id,),
            ).fetchall()
        ]

    if rows:
        df = pd.DataFrame(rows)
        if HAS_PLOTLY:
            fig = px.bar(
                df,
                x="month",
                y="cnt",
                color="period",
                barmode="group",
                labels={"cnt": "PR Count", "month": "Month", "period": "Period"},
                color_discrete_map={"pre": "#636EFA", "post": "#EF553B"},
            )
            fig.update_layout(xaxis_tickangle=-45, height=400)
            st.plotly_chart(fig, use_container_width=True)
        else:
            pivot = df.pivot(index="month", columns="period", values="cnt").fillna(0)
            st.bar_chart(pivot)

    # Data quality
    st.subheader("Data Quality")
    if dynamic:
        null_ttm = conn.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics pm "
            "JOIN pull_requests p ON pm.pull_request_id = p.id "
            "WHERE pm.analysis_run_id = ? AND pm.time_to_merge_hours IS NULL "
            "AND p.created_at >= ? AND p.created_at < ?",
            (run_id, window_info["pre_start"], window_info["post_end"]),
        ).fetchone()["cnt"]
        null_ttff = conn.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics pm "
            "JOIN pull_requests p ON pm.pull_request_id = p.id "
            "WHERE pm.analysis_run_id = ? AND pm.time_to_first_feedback_hours IS NULL "
            "AND p.created_at >= ? AND p.created_at < ?",
            (run_id, window_info["pre_start"], window_info["post_end"]),
        ).fetchone()["cnt"]
        total = pre_prs + post_prs
    else:
        null_ttm = conn.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics "
            "WHERE analysis_run_id = ? AND time_to_merge_hours IS NULL",
            (run_id,),
        ).fetchone()["cnt"]
        null_ttff = conn.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics "
            "WHERE analysis_run_id = ? AND time_to_first_feedback_hours IS NULL",
            (run_id,),
        ).fetchone()["cnt"]
        total = (run["total_prs_pre"] or 0) + (run["total_prs_post"] or 0)

    st.markdown(f"""
    - **PRs without merge time:** {null_ttm} / {total} (unmerged or open)
    - **PRs without feedback time:** {null_ttff} / {total} (no external feedback)
    """)
