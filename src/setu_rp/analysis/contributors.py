"""Contributor classification and retention computation."""

import sqlite3


def classify_contributor(
    conn: sqlite3.Connection,
    user_id: int,
    pr_created_at: str,
) -> str:
    """Classify a contributor as 'new' or 'established'.

    A contributor is 'new' if they have no PRs created before the given PR's
    creation date. Otherwise they are 'established'.

    Args:
        conn: Database connection.
        user_id: GitHub user ID.
        pr_created_at: ISO 8601 timestamp of the PR being classified.

    Returns:
        'new' or 'established'.
    """
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM pull_requests "
        "WHERE author_id = ? AND created_at < ?",
        (user_id, pr_created_at),
    ).fetchone()
    return "new" if row["cnt"] == 0 else "established"


def compute_retention(
    conn: sqlite3.Connection,
    run_id: int,
):
    """Compute within-period retention for new contributors.

    A new contributor "returned" if they submitted 2+ total PRs in the same
    period. Because classify_contributor labels the first PR as 'new' and
    subsequent PRs as 'established', we query pr_metrics directly to count
    all PRs by the author in that period.

    Updates the returned_in_period column on the 'new' contributor_metrics row.
    """
    for period in ("pre", "post"):
        new_rows = conn.execute(
            "SELECT id, user_id FROM contributor_metrics "
            "WHERE analysis_run_id = ? AND period = ? AND contributor_type = 'new'",
            (run_id, period),
        ).fetchall()

        for row in new_rows:
            # Count total PRs by this user in this period from pr_metrics
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM pr_metrics "
                "WHERE analysis_run_id = ? AND period = ? AND author_id = ?",
                (run_id, period, row["user_id"]),
            ).fetchone()["cnt"]

            returned = 1 if total >= 2 else 0
            conn.execute(
                "UPDATE contributor_metrics SET returned_in_period = ? WHERE id = ?",
                (returned, row["id"]),
            )
