"""RQ2 metrics: comment frequencies, human/bot ratios."""

import sqlite3

from setu_rp.analysis.bot_detection import IS_BOT_SQL


def count_comments_by_type(conn: sqlite3.Connection, pr_id: int) -> dict:
    """Count human and bot comments on a PR, broken down by comment type.

    Joins comments with users table to determine human vs bot.
    Bot detection considers both GitHub's type field and known bot login patterns.

    Args:
        conn: Database connection.
        pr_id: Pull request ID.

    Returns:
        Dict with keys: human_review_comments, bot_review_comments,
        human_issue_comments, bot_issue_comments.
    """
    result = {
        "human_review_comments": 0,
        "bot_review_comments": 0,
        "human_issue_comments": 0,
        "bot_issue_comments": 0,
    }

    # Review comments
    rows = conn.execute(
        "SELECT "
        f"SUM(CASE WHEN {IS_BOT_SQL} THEN 1 ELSE 0 END) as bot_cnt, "
        f"SUM(CASE WHEN {IS_BOT_SQL} THEN 0 ELSE 1 END) as human_cnt "
        "FROM review_comments rc "
        "JOIN users u ON rc.author_id = u.id "
        "WHERE rc.pull_request_id = ?",
        (pr_id,),
    ).fetchone()
    if rows:
        result["bot_review_comments"] = rows["bot_cnt"] or 0
        result["human_review_comments"] = rows["human_cnt"] or 0

    # Issue comments
    rows = conn.execute(
        "SELECT "
        f"SUM(CASE WHEN {IS_BOT_SQL} THEN 1 ELSE 0 END) as bot_cnt, "
        f"SUM(CASE WHEN {IS_BOT_SQL} THEN 0 ELSE 1 END) as human_cnt "
        "FROM issue_comments ic "
        "JOIN users u ON ic.author_id = u.id "
        "WHERE ic.pull_request_id = ?",
        (pr_id,),
    ).fetchone()
    if rows:
        result["bot_issue_comments"] = rows["bot_cnt"] or 0
        result["human_issue_comments"] = rows["human_cnt"] or 0

    return result
