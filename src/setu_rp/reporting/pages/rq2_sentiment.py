"""Dashboard page: RQ2 Sentiment Analysis."""

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
    st.title("RQ2: Comment Sentiment Analysis")

    st.info(
        "Sentiment is measured using the VADER lexicon, which produces a compound score "
        "from **-1** (most negative) to **+1** (most positive). Comments are preprocessed "
        "to remove code blocks, inline code, URLs, and @mentions. Code-review terms "
        "(LGTM, nit, PTAL) are added to the lexicon. Only **human** comments are analysed."
    )

    run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    from setu_rp.reporting.live_analysis import (
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

    # --- Avg Human Sentiment per PR ---
    st.subheader("Average Human Sentiment per PR")

    metric = "avg_human_sentiment"

    if dynamic:
        live_stats = compute_live_stats(
            conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id
        )
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
            fig.update_layout(
                yaxis_title="Compound Sentiment Score",
                height=350,
                showlegend=True,
            )
            st.plotly_chart(fig, use_container_width=True, key="box_sentiment")

    # Statistical test
    if dynamic:
        live_tests = compute_live_tests(
            conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id
        )
        test = live_tests.get(metric)
        if test:
            _render_test(test)
    else:
        test = conn.execute(
            "SELECT * FROM statistical_tests "
            "WHERE analysis_run_id = ? AND metric_name = ? AND contributor_type = ?",
            (run_id, metric, contributor_type),
        ).fetchone()
        if test:
            _render_test(dict(test))

    st.divider()

    # --- Sentiment distribution histogram ---
    st.subheader("Sentiment Score Distribution")
    st.caption(
        "Distribution of per-comment compound sentiment scores for human comments. "
        "Scores near 0 are neutral, positive scores indicate approval/praise, "
        "negative scores indicate criticism/concern."
    )

    _render_distribution(conn, run_id, pre_months, post_months, run)

    st.divider()

    # --- Monthly sentiment trend ---
    st.subheader("Monthly Sentiment Trend")
    st.caption("Average compound sentiment of human comments per month.")

    _render_monthly_trend(conn, run_id, pre_months, post_months, run)


def _render_test(test: dict):
    """Render a statistical test result."""
    adj_sig = test.get("adjusted_significant")
    if adj_sig is not None:
        sig_color = "\U0001f7e2" if adj_sig else "\U0001f534"
    else:
        sig_color = "\U0001f7e2" if test.get("significant") else "\U0001f534"
    p_str = f"{test['p_value']:.4f}" if test.get("p_value") is not None else "N/A"
    e_str = f"{test['effect_size']:.4f}" if test.get("effect_size") is not None else "N/A"
    result = (
        f"**Test:** {test.get('test_name', 'N/A')} | "
        f"**p-value:** {p_str}"
    )
    adj_p = test.get("p_value_adjusted")
    if adj_p is not None:
        result += f" (FDR-adjusted: {adj_p:.4f})"
    result += f" {sig_color} | **Effect size:** {e_str} ({test.get('effect_size_type') or 'N/A'})"
    st.markdown(result)


def _render_distribution(conn, run_id, pre_months, post_months, run):
    """Render sentiment distribution histograms."""
    from setu_rp.reporting.live_analysis import (
        get_dynamic_window,
        get_window_strings,
        is_dynamic,
    )

    dynamic = is_dynamic(run, pre_months, post_months)

    if dynamic:
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        pre_start, pre_end, post_start, post_end = get_window_strings(window)
        rows = conn.execute(
            "SELECT cs.compound_score, cs.period "
            "FROM comment_sentiments cs "
            "JOIN pull_requests p ON cs.pull_request_id = p.id "
            "WHERE cs.analysis_run_id = ? AND cs.is_bot = 0 "
            "AND p.created_at >= ? AND p.created_at < ?",
            (run_id, pre_start, post_end),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT compound_score, period "
            "FROM comment_sentiments "
            "WHERE analysis_run_id = ? AND is_bot = 0",
            (run_id,),
        ).fetchall()

    if not rows:
        st.caption("No sentiment data available. Run `make analyze` to compute.")
        return

    df = pd.DataFrame([dict(r) for r in rows])

    if HAS_PLOTLY:
        fig = px.histogram(
            df, x="compound_score", color="period",
            nbins=50, barmode="overlay", opacity=0.7,
            labels={"compound_score": "Compound Score", "period": "Period"},
        )
        fig.update_layout(height=350)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.bar_chart(df.groupby("period")["compound_score"].mean())


def _render_monthly_trend(conn, run_id, pre_months, post_months, run):
    """Render monthly average sentiment line chart."""
    from setu_rp.reporting.live_analysis import (
        get_dynamic_window,
        get_window_strings,
        is_dynamic,
    )

    dynamic = is_dynamic(run, pre_months, post_months)

    if dynamic:
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        pre_start, _, _, post_end = get_window_strings(window)
        date_filter = "AND p.created_at >= ? AND p.created_at < ?"
        params: list = [run_id, pre_start, post_end]
    else:
        date_filter = ""
        params = [run_id]

    rows = conn.execute(
        "SELECT strftime('%%Y-%%m', p.created_at) as month, "
        "AVG(cs.compound_score) as avg_sentiment, "
        "COUNT(*) as comment_count "
        "FROM comment_sentiments cs "
        "JOIN pull_requests p ON cs.pull_request_id = p.id "
        f"WHERE cs.analysis_run_id = ? AND cs.is_bot = 0 {date_filter} "
        "GROUP BY month ORDER BY month",
        params,
    ).fetchall()

    if not rows:
        st.caption("No sentiment data available.")
        return

    df = pd.DataFrame([dict(r) for r in rows])

    if HAS_PLOTLY:
        fig = px.line(
            df, x="month", y="avg_sentiment", markers=True,
            labels={"avg_sentiment": "Avg Compound Score", "month": "Month"},
        )
        fig.update_layout(xaxis_tickangle=-45, height=350)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.line_chart(df.set_index("month")["avg_sentiment"])
