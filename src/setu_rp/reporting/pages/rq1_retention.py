"""Dashboard page: RQ1 Contributor Retention Analysis."""

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
    st.title("RQ1: Contributor Retention")

    st.info(
        "**Retention rate** measures the proportion of new contributors who submitted "
        "2 or more PRs within the same period. Pre-period and post-period retention "
        "rates are compared using a proportion z-test to assess whether bot adoption "
        "affected new contributor engagement."
    )

    run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    from setu_rp.reporting.live_analysis import (
        compute_live_retention,
        get_dynamic_window,
        get_monthly_contributors,
        get_window_strings,
        is_dynamic,
    )

    dynamic = is_dynamic(run, pre_months, post_months)

    # Retention rates
    st.subheader("Retention Rates (New Contributors)")

    if dynamic:
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        pre_start, pre_end, post_start, post_end = get_window_strings(window)
        retention_data = compute_live_retention(
            conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id
        )
        st.caption("Computed live from raw PR data for the selected time window.")
    else:
        retention_data = []
        for period in ["pre", "post"]:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM contributor_metrics "
                "WHERE analysis_run_id = ? AND period = ? AND contributor_type = 'new'",
                (run_id, period),
            ).fetchone()["cnt"]
            returned = conn.execute(
                "SELECT COUNT(*) as cnt FROM contributor_metrics "
                "WHERE analysis_run_id = ? AND period = ? AND contributor_type = 'new' "
                "AND returned_in_period = 1",
                (run_id, period),
            ).fetchone()["cnt"]

            rate = returned / total if total > 0 else 0
            retention_data.append({
                "Period": period.title(),
                "New Contributors": total,
                "Returned (2+ PRs)": returned,
                "Retention Rate": f"{rate:.1%}",
            })

    df = pd.DataFrame(retention_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Statistical test results
    st.subheader("Statistical Tests")
    if dynamic:
        st.caption(
            "Retention statistical tests compare pre vs post within-period retention "
            "rates. For dynamic windows, the proportion test is recomputed from raw data."
        )
    tests = conn.execute(
        "SELECT * FROM statistical_tests "
        "WHERE analysis_run_id = ? AND metric_name = 'retention_rate' "
        "ORDER BY contributor_type",
        (run_id,),
    ).fetchall()

    if tests and not dynamic:
        test_df = pd.DataFrame([dict(t) for t in tests])
        cols = ["contributor_type", "test_name", "statistic", "p_value",
                "effect_size", "effect_size_type", "ci_lower", "ci_upper",
                "significant", "notes"]
        display_cols = [c for c in cols if c in test_df.columns]
        st.dataframe(test_df[display_cols], use_container_width=True, hide_index=True)
    elif dynamic:
        st.caption("Statistical test detail is available from the stored analysis run only.")

    # Monthly contributor activity
    st.subheader("Monthly Unique Contributors")
    st.caption(
        "Tracks how many distinct authors opened PRs each month. "
        "A sustained or increasing count post-adoption is a positive retention signal."
    )

    if dynamic:
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        pre_start, pre_end, post_start, post_end = get_window_strings(window)
        month_rows = get_monthly_contributors(conn, pre_start, post_end, run_id)
    else:
        month_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT strftime('%Y-%m', p.created_at) as month, "
                "COUNT(DISTINCT pm.author_id) as contributors "
                "FROM pr_metrics pm "
                "JOIN pull_requests p ON pm.pull_request_id = p.id "
                "WHERE pm.analysis_run_id = ? "
                "GROUP BY month ORDER BY month",
                (run_id,),
            ).fetchall()
        ]

    if month_rows:
        month_df = pd.DataFrame(month_rows)
        if HAS_PLOTLY:
            fig = px.line(
                month_df,
                x="month",
                y="contributors",
                markers=True,
                labels={"contributors": "Unique Contributors", "month": "Month"},
            )
            fig.update_layout(xaxis_tickangle=-45, height=400)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.line_chart(month_df.set_index("month"))
