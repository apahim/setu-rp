"""Dashboard page: Sensitivity analysis across window sizes."""

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
    st.title("Sensitivity Analysis")

    st.info(
        "Sensitivity analysis tests whether the statistical findings are robust to "
        "the choice of time-window size. Each row shows the effect size and p-value "
        "when the analysis is repeated with a different symmetric window. Consistent "
        "results across window sizes strengthen confidence in the findings."
    )

    rows = conn.execute(
        "SELECT * FROM sensitivity_results "
        "WHERE analysis_run_id = ? AND contributor_type = ? "
        "ORDER BY metric_name, window_months",
        (run_id, contributor_type),
    ).fetchall()

    if not rows:
        st.info("No sensitivity analysis data. Re-run analysis with sensitivity_windows configured.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    metrics = df["metric_name"].unique()

    for metric in metrics:
        st.subheader(metric.replace("_", " ").title())
        mdf = df[df["metric_name"] == metric].copy()

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Effect Size vs Window Size**")
            if HAS_PLOTLY:
                fig = px.line(
                    mdf,
                    x="window_months",
                    y="effect_size",
                    markers=True,
                    labels={"effect_size": "Effect Size", "window_months": "Window (months)"},
                )
                fig.update_layout(height=300)
                st.plotly_chart(fig, use_container_width=True, key=f"effect_{metric}")
            else:
                chart_data = mdf.set_index("window_months")[["effect_size"]].rename(
                    columns={"effect_size": "Effect Size"}
                )
                st.line_chart(chart_data)

        with col2:
            st.markdown("**p-value vs Window Size**")
            if HAS_PLOTLY:
                fig = px.line(
                    mdf,
                    x="window_months",
                    y="p_value",
                    markers=True,
                    labels={"p_value": "p-value", "window_months": "Window (months)"},
                )
                fig.add_hline(y=0.05, line_dash="dash", line_color="red",
                              annotation_text="α = 0.05")
                fig.update_layout(height=300)
                st.plotly_chart(fig, use_container_width=True, key=f"pval_{metric}")
            else:
                chart_data = mdf.set_index("window_months")[["p_value"]].rename(
                    columns={"p_value": "p-value"}
                )
                st.line_chart(chart_data)

        # Full results table
        display_cols = ["window_months", "pre_n", "post_n", "pre_mean",
                        "post_mean", "effect_size", "p_value"]
        available = [c for c in display_cols if c in mdf.columns]
        st.dataframe(mdf[available], use_container_width=True, hide_index=True)
        st.divider()

    st.caption(
        "These results are pre-computed during `make analyze` and are not affected "
        "by the time-window sliders in the sidebar."
    )
