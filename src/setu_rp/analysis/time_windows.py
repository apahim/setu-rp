"""Time window computation and PR period classification."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from dateutil.relativedelta import relativedelta


@dataclass
class TimeWindow:
    bot_adoption_date: datetime
    pre_start: datetime
    pre_end: datetime
    post_start: datetime
    post_end: datetime
    pre_months: int
    post_months: int


def compute_time_windows(
    conn: sqlite3.Connection,
    pre_months: int,
    post_months: int,
    bot_adoption_date: str | None = None,
) -> TimeWindow:
    """Compute pre/post time windows around the bot adoption date.

    Args:
        conn: Database connection.
        pre_months: Number of months before adoption for the pre-period.
        post_months: Number of months after adoption for the post-period.
        bot_adoption_date: Override adoption date (ISO 8601 string). If None,
            reads from the repositories table.

    Returns:
        TimeWindow with computed boundaries.

    Raises:
        ValueError: If no bot adoption date is found.
    """
    if bot_adoption_date is None:
        row = conn.execute(
            "SELECT bot_adoption_date FROM repositories WHERE bot_adoption_date IS NOT NULL LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError(
                "No bot_adoption_date found in repositories table. "
                "Run collection first or set it in config.yaml."
            )
        bot_adoption_date = row["bot_adoption_date"]

    adoption = datetime.fromisoformat(bot_adoption_date)

    pre_end = adoption
    pre_start = adoption - relativedelta(months=pre_months)
    post_start = adoption
    post_end = adoption + relativedelta(months=post_months)

    return TimeWindow(
        bot_adoption_date=adoption,
        pre_start=pre_start,
        pre_end=pre_end,
        post_start=post_start,
        post_end=post_end,
        pre_months=pre_months,
        post_months=post_months,
    )


def classify_pr_period(pr_created_at: str, window: TimeWindow) -> str | None:
    """Classify a PR into 'pre', 'post', or None (outside both windows).

    Args:
        pr_created_at: ISO 8601 timestamp of PR creation.
        window: TimeWindow defining the boundaries.

    Returns:
        'pre', 'post', or None.
    """
    created = datetime.fromisoformat(pr_created_at)
    if window.pre_start <= created < window.pre_end:
        return "pre"
    if window.post_start <= created < window.post_end:
        return "post"
    return None
