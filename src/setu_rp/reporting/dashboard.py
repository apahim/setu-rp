"""Streamlit dashboard entry point."""

import argparse
import sqlite3
import sys
from pathlib import Path

import streamlit as st


def get_db_path() -> str:
    """Get database path from Streamlit args or default."""
    # Streamlit passes args after '--'
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="data/hypershift.db")
    # Parse only known args to avoid conflicts with streamlit's own args
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args.db_path


@st.cache_resource
def get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get a cached database connection."""
    path = Path(db_path)
    if not path.exists():
        st.error(f"Database not found at {db_path}. Run `make collect` and `make analyze` first.")
        st.stop()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def main():
    st.set_page_config(
        page_title="LLM Bot Impact Analysis",
        page_icon="📊",
        layout="wide",
    )

    db_path = get_db_path()
    conn = get_db_connection(db_path)

    # Check for analysis data
    run = conn.execute("SELECT * FROM analysis_runs ORDER BY id DESC LIMIT 1").fetchone()
    if run is None:
        st.error("No analysis data found. Run `make analyze` first.")
        st.stop()

    # Sidebar controls
    st.sidebar.title("Analysis Controls")

    run_id = run["id"]
    st.sidebar.markdown(f"**Run:** {run['run_at'][:19]}")
    st.sidebar.markdown(f"**Bot adoption:** {run['bot_adoption_date'][:10]}")

    contributor_filter = st.sidebar.selectbox(
        "Contributor Type",
        ["all", "new", "established"],
        format_func=lambda x: x.title(),
    )

    # Dynamic time-window controls
    st.sidebar.divider()
    st.sidebar.subheader("Time Windows")

    pre_months = st.sidebar.slider(
        "Pre-window (months)",
        min_value=1,
        max_value=12,
        value=run["pre_window_months"],
    )
    post_months = st.sidebar.slider(
        "Post-window (months)",
        min_value=1,
        max_value=12,
        value=run["post_window_months"],
    )

    # Show whether we're using stored or live results
    from setu_rp.reporting.live_analysis import get_dynamic_window, is_dynamic

    dynamic = is_dynamic(run, pre_months, post_months)

    if dynamic:
        window = get_dynamic_window(conn, pre_months, post_months, run["bot_adoption_date"])
        st.sidebar.info(
            f"**Live recomputation**\n\n"
            f"Pre: {window.pre_start.strftime('%Y-%m-%d')} to {window.pre_end.strftime('%Y-%m-%d')}\n\n"
            f"Post: {window.post_start.strftime('%Y-%m-%d')} to {window.post_end.strftime('%Y-%m-%d')}"
        )
    else:
        st.sidebar.success(
            f"**Using stored results**\n\n"
            f"Pre: {run['pre_start'][:10]} to {run['pre_end'][:10]}\n\n"
            f"Post: {run['post_start'][:10]} to {run['post_end'][:10]}"
        )

    # Page navigation
    page = st.sidebar.radio(
        "Page",
        [
            "Overview",
            "RQ1: Retention",
            "RQ1: Efficiency",
            "RQ2: Behavior",
            "RQ2: Sentiment",
            "RQ2: Governance",
            "Sensitivity",
            "Export",
        ],
    )

    # Import and render pages
    from setu_rp.reporting.pages import (
        data_export,
        overview,
        rq1_efficiency,
        rq1_retention,
        rq2_behavior,
        rq2_governance,
        rq2_sentiment,
        sensitivity,
    )

    page_map = {
        "Overview": overview.render,
        "RQ1: Retention": rq1_retention.render,
        "RQ1: Efficiency": rq1_efficiency.render,
        "RQ2: Behavior": rq2_behavior.render,
        "RQ2: Sentiment": rq2_sentiment.render,
        "RQ2: Governance": rq2_governance.render,
        "Sensitivity": sensitivity.render,
        "Export": data_export.render,
    }

    page_map[page](conn, run_id, contributor_filter, pre_months, post_months)


if __name__ == "__main__":
    main()
