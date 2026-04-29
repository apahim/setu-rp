"""Dashboard page: RQ2 Maintainer Behavior (comment patterns)."""

import sqlite3

import pandas as pd
import streamlit as st

try:
    import plotly.express as px
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def render(conn: sqlite3.Connection, run_id: int, contributor_type: str,
           pre_months: int, post_months: int):
    st.title("RQ2: Maintainer Review Behavior")

    st.info(
        "RQ2 examines whether the introduction of an LLM code-review bot changes "
        "human reviewer behaviour. Key indicators include the volume of human comments "
        "(do reviewers comment less when a bot is present?) and the ratio of bot to "
        "human feedback."
    )

    run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    from setu_rp.reporting.live_analysis import (
        compute_live_stats,
        compute_live_tests,
        get_dynamic_window,
        get_monthly_comments,
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

    comment_metrics = [
        ("total_human_comments", "Human Comments per PR"),
        ("total_bot_comments", "Bot Comments per PR"),
        ("human_review_comment_count", "Human Review Comments per PR"),
        ("bot_review_comment_count", "Bot Review Comments per PR"),
    ]

    for metric, label in comment_metrics:
        st.subheader(label)

        if dynamic:
            stats_rows = []
            for period in ("pre", "post"):
                s = live_stats[metric][period].copy()
                s["period"] = period
                stats_rows.append(s)
            stats_df = pd.DataFrame(stats_rows)
            display_cols = ["period", "n", "mean", "median", "std_dev"]
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
                display_cols = ["period", "n", "mean", "median", "std_dev"]
                available = [c for c in display_cols if c in stats_df.columns]
                st.dataframe(stats_df[available], use_container_width=True, hide_index=True)

        # Box plot
        if HAS_PLOTLY:
            if dynamic:
                box_data = get_pr_values_for_boxplot(
                    conn, pre_start, pre_end, post_start, post_end, metric, contributor_type, run_id
                )
            else:
                box_data = {"pre": [], "post": []}
                for period in ("pre", "post"):
                    where = f"analysis_run_id = ? AND period = ? AND {metric} IS NOT NULL"
                    params: list = [run_id, period]
                    if contributor_type != "all":
                        where += " AND contributor_type = ?"
                        params.append(contributor_type)
                    rows = conn.execute(
                        f"SELECT {metric} as val FROM pr_metrics WHERE {where}", params
                    ).fetchall()
                    box_data[period] = [r["val"] for r in rows]

            if box_data["pre"] or box_data["post"]:
                fig = go.Figure()
                if box_data["pre"]:
                    fig.add_trace(go.Box(y=box_data["pre"], name="Pre", marker_color="#636EFA"))
                if box_data["post"]:
                    fig.add_trace(go.Box(y=box_data["post"], name="Post", marker_color="#EF553B"))
                fig.update_layout(yaxis_title=label, height=300, showlegend=True)
                st.plotly_chart(fig, use_container_width=True, key=f"box_{metric}")

        # Statistical test
        if dynamic:
            test = live_tests.get(metric)
            if test:
                sig_color = "🟢" if test.get("significant") else "🔴"
                p_str = f"{test['p_value']:.4f}" if test.get("p_value") is not None else "N/A"
                e_str = f"{test['effect_size']:.4f}" if test.get("effect_size") is not None else "N/A"
                st.markdown(
                    f"**Test:** {test['test_name']} | "
                    f"**p-value:** {p_str} {sig_color} | "
                    f"**Effect size:** {e_str} ({test.get('effect_size_type') or 'N/A'})"
                )
        else:
            test = conn.execute(
                "SELECT * FROM statistical_tests "
                "WHERE analysis_run_id = ? AND metric_name = ? AND contributor_type = ?",
                (run_id, metric, contributor_type),
            ).fetchone()
            if test:
                sig_color = "🟢" if test["significant"] else "🔴"
                p_str = f"{test['p_value']:.4f}" if test["p_value"] is not None else "N/A"
                e_str = f"{test['effect_size']:.4f}" if test["effect_size"] is not None else "N/A"
                st.markdown(
                    f"**Test:** {test['test_name']} | "
                    f"**p-value:** {p_str} {sig_color} | "
                    f"**Effect size:** {e_str} ({test['effect_size_type'] or 'N/A'})"
                )

        st.divider()

    # Comment composition over time
    st.subheader("Comment Composition Over Time")
    st.caption(
        "Monthly average human vs bot comment counts per PR. "
        "An increase in bot comments with stable human comments suggests the bot "
        "supplements rather than replaces human review."
    )

    if dynamic:
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        pre_start_s, _, _, post_end_s = get_window_strings(window)
        month_rows = get_monthly_comments(conn, pre_start_s, post_end_s, run_id)
    else:
        month_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT strftime('%Y-%m', p.created_at) as month, "
                "AVG(pm.total_human_comments) as avg_human, "
                "AVG(pm.total_bot_comments) as avg_bot "
                "FROM pr_metrics pm "
                "JOIN pull_requests p ON pm.pull_request_id = p.id "
                "WHERE pm.analysis_run_id = ? "
                "GROUP BY month ORDER BY month",
                (run_id,),
            ).fetchall()
        ]

    if month_rows:
        df = pd.DataFrame(month_rows)
        if HAS_PLOTLY:
            fig = px.line(
                df,
                x="month",
                y=["avg_human", "avg_bot"],
                markers=True,
                labels={"value": "Avg Comments/PR", "month": "Month", "variable": "Type"},
            )
            fig.update_layout(xaxis_tickangle=-45, height=400)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.line_chart(df.set_index("month"))
