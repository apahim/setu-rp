"""RQ1 metrics: retention, time-to-merge, rejection, iterations, TTFF."""

import sqlite3
from datetime import datetime

from setu_rp.analysis.bot_detection import NOT_BOT_SQL


def compute_time_to_merge(pr: dict) -> float | None:
    """Compute time from PR creation to merge in hours.

    Args:
        pr: Dict with 'created_at' and 'merged_at' keys (ISO 8601 strings).

    Returns:
        Hours as float, or None if not merged.
    """
    if not pr.get("merged_at"):
        return None
    created = datetime.fromisoformat(pr["created_at"])
    merged = datetime.fromisoformat(pr["merged_at"])
    delta = merged - created
    return delta.total_seconds() / 3600


def compute_time_to_first_feedback(
    conn: sqlite3.Connection,
    pr_id: int,
    pr_created_at: str,
) -> float | None:
    """Compute time from PR creation to first review or comment in hours.

    Considers reviews, review comments, and issue comments. Excludes
    comments by the PR author.

    Args:
        conn: Database connection.
        pr_id: Pull request ID.
        pr_created_at: ISO 8601 timestamp of PR creation.

    Returns:
        Hours as float, or None if no feedback found.
    """
    # Find the PR author to exclude self-comments
    author_row = conn.execute(
        "SELECT author_id FROM pull_requests WHERE id = ?", (pr_id,)
    ).fetchone()
    author_id = author_row["author_id"] if author_row else None

    earliest = None

    # Check reviews
    row = conn.execute(
        "SELECT MIN(submitted_at) as earliest FROM reviews "
        "WHERE pull_request_id = ? AND reviewer_id != ? AND submitted_at IS NOT NULL",
        (pr_id, author_id),
    ).fetchone()
    if row and row["earliest"]:
        earliest = row["earliest"]

    # Check review comments
    row = conn.execute(
        "SELECT MIN(created_at) as earliest FROM review_comments "
        "WHERE pull_request_id = ? AND author_id != ? AND created_at IS NOT NULL",
        (pr_id, author_id),
    ).fetchone()
    if row and row["earliest"]:
        if earliest is None or row["earliest"] < earliest:
            earliest = row["earliest"]

    # Check issue comments
    row = conn.execute(
        "SELECT MIN(created_at) as earliest FROM issue_comments "
        "WHERE pull_request_id = ? AND author_id != ? AND created_at IS NOT NULL",
        (pr_id, author_id),
    ).fetchone()
    if row and row["earliest"]:
        if earliest is None or row["earliest"] < earliest:
            earliest = row["earliest"]

    if earliest is None:
        return None

    created = datetime.fromisoformat(pr_created_at)
    first_feedback = datetime.fromisoformat(earliest)
    delta = first_feedback - created
    return delta.total_seconds() / 3600


def compute_time_to_first_human_feedback(
    conn: sqlite3.Connection,
    pr_id: int,
    pr_created_at: str,
) -> float | None:
    """Compute time from PR creation to first human (non-bot) feedback in hours.

    Same as compute_time_to_first_feedback but excludes bot users,
    giving the true time until a human reviewer engages.

    Args:
        conn: Database connection.
        pr_id: Pull request ID.
        pr_created_at: ISO 8601 timestamp of PR creation.

    Returns:
        Hours as float, or None if no human feedback found.
    """
    author_row = conn.execute(
        "SELECT author_id FROM pull_requests WHERE id = ?", (pr_id,)
    ).fetchone()
    author_id = author_row["author_id"] if author_row else None

    earliest = None

    # Check reviews (human only)
    row = conn.execute(
        "SELECT MIN(r.submitted_at) as earliest FROM reviews r "
        "JOIN users u ON r.reviewer_id = u.id "
        "WHERE r.pull_request_id = ? AND r.reviewer_id != ? "
        f"AND {NOT_BOT_SQL} AND r.submitted_at IS NOT NULL",
        (pr_id, author_id),
    ).fetchone()
    if row and row["earliest"]:
        earliest = row["earliest"]

    # Check review comments (human only)
    row = conn.execute(
        "SELECT MIN(rc.created_at) as earliest FROM review_comments rc "
        "JOIN users u ON rc.author_id = u.id "
        "WHERE rc.pull_request_id = ? AND rc.author_id != ? "
        f"AND {NOT_BOT_SQL} AND rc.created_at IS NOT NULL",
        (pr_id, author_id),
    ).fetchone()
    if row and row["earliest"]:
        if earliest is None or row["earliest"] < earliest:
            earliest = row["earliest"]

    # Check issue comments (human only)
    row = conn.execute(
        "SELECT MIN(ic.created_at) as earliest FROM issue_comments ic "
        "JOIN users u ON ic.author_id = u.id "
        "WHERE ic.pull_request_id = ? AND ic.author_id != ? "
        f"AND {NOT_BOT_SQL} AND ic.created_at IS NOT NULL",
        (pr_id, author_id),
    ).fetchone()
    if row and row["earliest"]:
        if earliest is None or row["earliest"] < earliest:
            earliest = row["earliest"]

    if earliest is None:
        return None

    created = datetime.fromisoformat(pr_created_at)
    first_feedback = datetime.fromisoformat(earliest)
    delta = first_feedback - created
    return delta.total_seconds() / 3600


def compute_time_to_first_human_review(
    conn: sqlite3.Connection,
    pr_id: int,
    pr_created_at: str,
) -> float | None:
    """Compute time from PR creation to first formal human review in hours.

    Only considers formal review submissions (APPROVED, CHANGES_REQUESTED,
    COMMENTED) from human users, not inline comments or bot reviews.

    Args:
        conn: Database connection.
        pr_id: Pull request ID.
        pr_created_at: ISO 8601 timestamp of PR creation.

    Returns:
        Hours as float, or None if no human review found.
    """
    author_row = conn.execute(
        "SELECT author_id FROM pull_requests WHERE id = ?", (pr_id,)
    ).fetchone()
    author_id = author_row["author_id"] if author_row else None

    row = conn.execute(
        "SELECT MIN(r.submitted_at) as earliest FROM reviews r "
        "JOIN users u ON r.reviewer_id = u.id "
        "WHERE r.pull_request_id = ? AND r.reviewer_id != ? "
        f"AND {NOT_BOT_SQL} AND r.submitted_at IS NOT NULL",
        (pr_id, author_id),
    ).fetchone()

    if not row or not row["earliest"]:
        return None

    created = datetime.fromisoformat(pr_created_at)
    first_review = datetime.fromisoformat(row["earliest"])
    delta = first_review - created
    return delta.total_seconds() / 3600


def compute_review_iterations(conn: sqlite3.Connection, pr_id: int) -> int:
    """Count CHANGES_REQUESTED reviews as a proxy for review iterations.

    Args:
        conn: Database connection.
        pr_id: Pull request ID.

    Returns:
        Number of CHANGES_REQUESTED reviews.
    """
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM reviews "
        "WHERE pull_request_id = ? AND state = 'CHANGES_REQUESTED'",
        (pr_id,),
    ).fetchone()
    return row["cnt"]


def compute_rejection(pr: dict) -> int | None:
    """Determine if a PR was rejected (closed without merge).

    Args:
        pr: Dict with 'state' and 'merged_at' keys.

    Returns:
        1 if rejected, 0 if merged/closed-with-merge, None if still open.
    """
    if pr.get("state") == "open":
        return None
    if pr.get("state") == "closed" and not pr.get("merged_at"):
        return 1
    return 0
