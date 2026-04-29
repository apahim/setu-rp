"""Dashboard page: RQ2 Governance Document Analysis."""

import sqlite3
from datetime import datetime as dt

import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def render(conn: sqlite3.Connection, run_id: int, contributor_type: str,
           pre_months: int, post_months: int):
    st.title("RQ2: Governance Document Changes")

    st.info(
        "**Governance analysis** tracks changes to contribution guidelines, PR templates, "
        "and ownership files (CONTRIBUTING.md, OWNERS, etc.) to assess whether maintainers "
        "adjusted review policies after bot adoption."
    )

    run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    # Check if governance data exists
    try:
        changes = conn.execute(
            "SELECT * FROM governance_changes WHERE analysis_run_id = ? ORDER BY change_date",
            (run_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        st.warning("Governance analysis tables not found. Run `make analyze` first.")
        return

    if not changes:
        st.warning(
            "No governance document changes found. This may mean:\n"
            "- Governance documents haven't been collected yet (`make collect`)\n"
            "- No changes were made to tracked files during the analysis window"
        )
        return

    changes_data = [dict(c) for c in changes]
    df = pd.DataFrame(changes_data)

    # Summary metrics
    st.subheader("Summary")
    pre_changes = df[df["period"] == "pre"]
    post_changes = df[df["period"] == "post"]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Pre-period Changes", len(pre_changes))
    with col2:
        st.metric("Post-period Changes", len(post_changes))
    with col3:
        bot_count = int(df["bot_related"].sum()) if "bot_related" in df.columns else 0
        st.metric("Bot-related Changes", bot_count)
    with col4:
        outside = df[df["period"].isna()]
        st.metric("Outside Window", len(outside))

    # Category breakdown
    if "category" in df.columns:
        st.subheader("Changes by Category")
        cat_data = []
        categories = df["category"].dropna().unique()
        for cat in sorted(categories):
            cat_df = df[df["category"] == cat]
            pre_n = len(cat_df[cat_df["period"] == "pre"])
            post_n = len(cat_df[cat_df["period"] == "post"])
            bot_n = int(cat_df["bot_related"].sum()) if "bot_related" in cat_df.columns else 0
            cat_data.append({
                "Category": cat,
                "Pre": pre_n,
                "Post": post_n,
                "Total": len(cat_df),
                "Bot-related": bot_n,
            })
        if cat_data:
            st.dataframe(pd.DataFrame(cat_data), use_container_width=True, hide_index=True)

    # Timeline of changes
    st.subheader("Change Timeline")

    if HAS_PLOTLY:
        fig = go.Figure()
        for file_path in df["file_path"].unique():
            file_df = df[df["file_path"] == file_path]
            marker_colors = [
                "red" if r else "blue"
                for r in file_df["bot_related"]
            ] if "bot_related" in file_df.columns else None
            fig.add_trace(go.Scatter(
                x=pd.to_datetime(file_df["change_date"]),
                y=file_df["lines_added"] + file_df["lines_removed"],
                mode="markers+lines",
                name=file_path,
                marker=dict(color=marker_colors, size=10) if marker_colors else None,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Date: %{x}<br>"
                    "Lines: +%{customdata[1]} -%{customdata[2]}<br>"
                    "Category: %{customdata[3]}<br>"
                    "Bot-related: %{customdata[4]}<br>"
                    "<extra></extra>"
                ),
                customdata=file_df[[
                    "file_path", "lines_added", "lines_removed",
                    "category", "bot_related",
                ]].values,
            ))
        # Add adoption date marker
        bot_date = dt.fromisoformat(run["bot_adoption_date"][:10])
        fig.add_shape(
            type="line", x0=bot_date, x1=bot_date, y0=0, y1=1,
            yref="paper", line=dict(dash="dash", color="red"),
        )
        fig.add_annotation(
            x=bot_date, y=1, yref="paper",
            text="Bot adoption", showarrow=False, yshift=10,
        )
        fig.update_layout(
            xaxis_title="Date", yaxis_title="Lines Changed",
            height=400, showlegend=True,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.dataframe(df[["change_date", "file_path", "lines_added", "lines_removed"]],
                      use_container_width=True, hide_index=True)

    # Bot-related changes detail
    if "bot_related" in df.columns and bot_count > 0:
        st.subheader("Bot-related Changes")
        bot_df = df[df["bot_related"] == 1]
        for _, row in bot_df.iterrows():
            period_label = row["period"] or "outside window"
            with st.expander(
                f"{row['change_date'][:10]} — {row['file_path']} ({period_label})"
            ):
                st.markdown(f"**Category:** {row.get('category', 'N/A')}")
                st.markdown(f"**Lines:** +{row['lines_added']} -{row['lines_removed']}")
                if row.get("keywords_added"):
                    st.markdown(f"**Keywords added:** {row['keywords_added']}")
                if row.get("diff_excerpt"):
                    st.code(row["diff_excerpt"], language="diff")

    # Change details table
    st.subheader("All Changes")
    display_cols = ["change_date", "file_path", "period", "category",
                    "bot_related", "change_type", "lines_added", "lines_removed",
                    "keywords_added", "keywords_removed"]
    available_cols = [c for c in display_cols if c in df.columns]
    st.dataframe(df[available_cols], use_container_width=True, hide_index=True)

    # Diff excerpts
    st.subheader("Change Content")
    if "diff_excerpt" in df.columns:
        for _, row in df.iterrows():
            excerpt = row.get("diff_excerpt", "")
            if not excerpt:
                continue
            period_label = row["period"] or "outside"
            bot_tag = " [BOT]" if row.get("bot_related") else ""
            cat_tag = f" ({row.get('category', '')})" if row.get("category") else ""
            with st.expander(
                f"{row['change_date'][:10]} — {row['file_path']}"
                f" [{period_label}]{cat_tag}{bot_tag}"
            ):
                st.code(excerpt, language="diff")

    # Keyword summary
    st.subheader("Keyword Analysis")
    all_kw_added = []
    all_kw_removed = []
    for _, row in df.iterrows():
        if row.get("keywords_added"):
            all_kw_added.extend(row["keywords_added"].split(","))
        if row.get("keywords_removed"):
            all_kw_removed.extend(row["keywords_removed"].split(","))

    if all_kw_added or all_kw_removed:
        kw_data = []
        all_kw = set(all_kw_added + all_kw_removed)
        for kw in sorted(all_kw):
            kw_data.append({
                "Keyword": kw,
                "Times Added": all_kw_added.count(kw),
                "Times Removed": all_kw_removed.count(kw),
            })
        st.dataframe(pd.DataFrame(kw_data), use_container_width=True, hide_index=True)
    else:
        st.caption("No governance keywords detected in changes.")
