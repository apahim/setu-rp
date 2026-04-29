"""Dashboard page: Data export (CSV, figures)."""

import csv
import io
import sqlite3

import pandas as pd
import streamlit as st


def render(conn: sqlite3.Connection, run_id: int, contributor_type: str,
           pre_months: int, post_months: int):
    st.title("Data Export")

    run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    from setu_rp.reporting.live_analysis import (
        get_dynamic_window,
        get_window_strings,
        is_dynamic,
    )

    dynamic = is_dynamic(run, pre_months, post_months)

    if dynamic:
        st.warning(
            "Time-window sliders differ from the stored analysis run. "
            "Descriptive statistics and test results below are recomputed live. "
            "Raw per-PR metrics are exported filtered to the dynamic window."
        )
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        pre_start, pre_end, post_start, post_end = get_window_strings(window)

    st.subheader("Export Analysis Tables as CSV")

    if dynamic:
        _export_dynamic(conn, run_id, contributor_type, pre_start, pre_end, post_start, post_end)
    else:
        _export_stored(conn, run_id)

    # APA-style reporting strings
    st.subheader("APA-Style Statistical Reports")
    st.caption(
        "These formatted strings follow APA conventions and can be pasted directly "
        "into a dissertation or paper. An asterisk (*) marks statistically significant results."
    )

    if dynamic:
        from setu_rp.reporting.live_analysis import compute_live_tests
        live_tests = compute_live_tests(conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id)
        for metric, test in live_tests.items():
            _render_apa_report(test, metric, contributor_type)
    else:
        tests = conn.execute(
            "SELECT * FROM statistical_tests "
            "WHERE analysis_run_id = ? ORDER BY metric_name, contributor_type",
            (run_id,),
        ).fetchall()
        for test in tests:
            _render_apa_report(dict(test), test["metric_name"], test["contributor_type"])


def _export_stored(conn: sqlite3.Connection, run_id: int):
    """Export pre-computed tables from the database."""
    tables = [
        ("pr_metrics", "Per-PR Metrics"),
        ("contributor_metrics", "Per-Contributor Metrics"),
        ("period_statistics", "Descriptive Statistics"),
        ("statistical_tests", "Statistical Test Results"),
        ("sensitivity_results", "Sensitivity Analysis"),
    ]

    for table_name, label in tables:
        rows = conn.execute(
            f"SELECT * FROM {table_name} WHERE analysis_run_id = ?", (run_id,)
        ).fetchall()

        if not rows:
            st.markdown(f"**{label}:** No data")
            continue

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(rows[0].keys())
        writer.writerows(rows)
        csv_data = output.getvalue()

        st.download_button(
            label=f"Download {label} ({len(rows)} rows)",
            data=csv_data,
            file_name=f"{table_name}.csv",
            mime="text/csv",
        )


def _export_dynamic(conn: sqlite3.Connection, run_id: int, contributor_type: str,
                    pre_start: str, pre_end: str, post_start: str, post_end: str):
    """Export live-computed data for the dynamic window."""
    from setu_rp.reporting.live_analysis import compute_live_stats, compute_live_tests

    # PR metrics filtered to dynamic window
    rows = conn.execute(
        "SELECT pm.* FROM pr_metrics pm "
        "JOIN pull_requests p ON pm.pull_request_id = p.id "
        "WHERE pm.analysis_run_id = ? "
        "AND p.created_at >= ? AND p.created_at < ?",
        (run_id, pre_start, post_end),
    ).fetchall()

    if rows:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(rows[0].keys())
        writer.writerows(rows)
        st.download_button(
            label=f"Download Per-PR Metrics — dynamic window ({len(rows)} rows)",
            data=output.getvalue(),
            file_name="pr_metrics_dynamic.csv",
            mime="text/csv",
        )

    # Live descriptive stats
    live_stats = compute_live_stats(conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id)
    stats_rows = []
    for metric, periods in live_stats.items():
        for period, s in periods.items():
            row = {"metric_name": metric, "period": period, "contributor_type": contributor_type}
            row.update(s)
            stats_rows.append(row)

    if stats_rows:
        df = pd.DataFrame(stats_rows)
        st.download_button(
            label=f"Download Descriptive Statistics — dynamic ({len(stats_rows)} rows)",
            data=df.to_csv(index=False),
            file_name="period_statistics_dynamic.csv",
            mime="text/csv",
            key="dl_stats_dynamic",
        )

    # Live test results
    live_tests = compute_live_tests(conn, pre_start, pre_end, post_start, post_end, contributor_type, run_id)
    test_rows = list(live_tests.values())
    if test_rows:
        df = pd.DataFrame(test_rows)
        st.download_button(
            label=f"Download Statistical Tests — dynamic ({len(test_rows)} rows)",
            data=df.to_csv(index=False),
            file_name="statistical_tests_dynamic.csv",
            mime="text/csv",
            key="dl_tests_dynamic",
        )


def _render_apa_report(test: dict, metric_name: str, contributor_type: str):
    """Render a single APA-style statistical report."""
    test_name = test.get("test_name", "")
    if test_name == "insufficient_data":
        return

    # Skip if key values are None
    if test.get("statistic") is None or test.get("p_value") is None:
        return

    metric = metric_name.replace("_", " ").title()
    ctype = contributor_type

    if test_name == "welch_t_test":
        pre_n = test.get("pre_n", 0) or 0
        post_n = test.get("post_n", 0) or 0
        effect = test.get("effect_size")
        report = (
            f"**{metric} ({ctype}):** "
            f"t({pre_n + post_n - 2}) = {test['statistic']:.2f}, "
            f"p = {test['p_value']:.3f}"
        )
        if effect is not None:
            report += f", d = {effect:.2f}"
        if test.get("ci_lower") is not None:
            report += f", 95% CI [{test['ci_lower']:.2f}, {test['ci_upper']:.2f}]"
    elif test_name == "mann_whitney_u":
        effect = test.get("effect_size")
        report = (
            f"**{metric} ({ctype}):** "
            f"U = {test['statistic']:.0f}, "
            f"p = {test['p_value']:.3f}"
        )
        if effect is not None:
            report += f", r = {effect:.2f}"
    elif test_name == "proportion_z_test":
        report = (
            f"**{metric} ({ctype}):** "
            f"z = {test['statistic']:.2f}, "
            f"p = {test['p_value']:.3f}"
        )
        if test.get("effect_size") is not None:
            report += f", OR = {test['effect_size']:.2f}"
        if test.get("ci_lower") is not None:
            report += f", 95% CI [{test['ci_lower']:.2f}, {test['ci_upper']:.2f}]"
    else:
        return

    adj_p = test.get("p_value_adjusted")
    if adj_p is not None:
        report += f", p_adj = {adj_p:.3f}"

    adj_sig = test.get("adjusted_significant")
    if adj_sig is not None:
        sig = " *" if adj_sig else ""
    else:
        sig = " *" if test.get("significant") else ""
    st.markdown(report + sig)
