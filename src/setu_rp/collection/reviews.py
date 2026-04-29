"""Review collection logic."""

import logging
from datetime import datetime, timezone

from setu_rp.collection.client import GitHubClient
from setu_rp.db.connection import upsert

logger = logging.getLogger(__name__)


def collect_reviews(client: GitHubClient, conn, owner: str, name: str,
                    last_sync: str | None = None):
    """Collect reviews for all PRs (or only recently-updated PRs for incremental)."""
    now = datetime.now(timezone.utc).isoformat()

    # Get PR numbers to fetch reviews for
    if last_sync:
        cursor = conn.execute(
            "SELECT number FROM pull_requests WHERE updated_at > ? ORDER BY number",
            (last_sync,),
        )
    else:
        cursor = conn.execute("SELECT number FROM pull_requests ORDER BY number")

    pr_numbers = [row["number"] for row in cursor.fetchall()]
    total_prs = len(pr_numbers)
    logger.info("Fetching reviews for %d PRs.", total_prs)

    count = 0
    for i, pr_number in enumerate(pr_numbers, 1):
        path = f"/repos/{owner}/{name}/pulls/{pr_number}/reviews"
        for review in client.get_paginated(path):
            reviewer = review.get("user")
            if reviewer:
                upsert(conn, "users", {
                    "id": reviewer["id"],
                    "login": reviewer["login"],
                    "type": reviewer.get("type", "User"),
                    "name": reviewer.get("name"),
                    "fetched_at": now,
                })

            # Look up pull_request_id from the PR number
            row = conn.execute(
                "SELECT id FROM pull_requests WHERE number = ?", (pr_number,)
            ).fetchone()
            pr_id = row["id"] if row else None

            upsert(conn, "reviews", {
                "id": review["id"],
                "pull_request_id": pr_id,
                "reviewer_id": reviewer["id"] if reviewer else None,
                "state": review.get("state"),
                "body": review.get("body"),
                "submitted_at": review.get("submitted_at"),
                "fetched_at": now,
            })
            count += 1

        if i % 100 == 0:
            conn.commit()
            logger.info("Reviews: processed %d/%d PRs (%d reviews).", i, total_prs, count)

    conn.commit()
    logger.info("Review collection complete: %d reviews from %d PRs.", count, total_prs)
    return count
