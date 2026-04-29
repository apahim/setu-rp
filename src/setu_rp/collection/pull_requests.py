"""Pull request collection logic."""

import logging
from datetime import datetime, timezone

from setu_rp.collection.client import GitHubClient
from setu_rp.db.connection import upsert

logger = logging.getLogger(__name__)


def collect_pull_requests(client: GitHubClient, conn, owner: str, name: str,
                          last_sync: str | None = None, start_page: int = 1):
    """Collect all pull requests, with incremental support.

    Returns (count, last_completed_page).
    """
    now = datetime.now(timezone.utc).isoformat()
    path = f"/repos/{owner}/{name}/pulls"
    params = {
        "state": "all",
        "sort": "updated",
        "direction": "desc",
    }

    count = 0
    last_completed_page = 0

    for page_num, items in client.get_paginated_with_pages(
        path, params=params, start_page=start_page
    ):
        stop_early = False
        for pr in items:
            # Incremental: stop when we hit PRs not updated since last sync
            if last_sync and pr["updated_at"] <= last_sync:
                stop_early = True
                break

            # Upsert author
            user = pr["user"]
            if user:
                upsert(conn, "users", {
                    "id": user["id"],
                    "login": user["login"],
                    "type": user.get("type", "User"),
                    "name": user.get("name"),
                    "fetched_at": now,
                })

            # Determine state
            state = "merged" if pr.get("merged_at") else pr["state"]

            upsert(conn, "pull_requests", {
                "id": pr["id"],
                "number": pr["number"],
                "title": pr.get("title"),
                "state": state,
                "author_id": user["id"] if user else None,
                "created_at": pr.get("created_at"),
                "updated_at": pr.get("updated_at"),
                "closed_at": pr.get("closed_at"),
                "merged_at": pr.get("merged_at"),
                "merge_commit_sha": pr.get("merge_commit_sha"),
                "additions": pr.get("additions"),
                "deletions": pr.get("deletions"),
                "changed_files": pr.get("changed_files"),
                "body": pr.get("body"),
                "fetched_at": now,
            })

            # Extract labels
            for label in pr.get("labels", []):
                upsert(conn, "labels", {
                    "id": label["id"],
                    "name": label["name"],
                    "description": label.get("description"),
                    "fetched_at": now,
                })
                # Junction table — use raw SQL for composite PK
                conn.execute(
                    "INSERT OR IGNORE INTO pull_request_labels "
                    "(pull_request_id, label_id) VALUES (?, ?)",
                    (pr["id"], label["id"]),
                )

            count += 1

        last_completed_page = page_num
        conn.commit()

        if count % 500 == 0 and count > 0:
            logger.info("Collected %d PRs (page %d)", count, page_num)

        if stop_early:
            logger.info("Incremental sync complete — reached already-synced PRs.")
            break

    logger.info("Pull request collection complete: %d PRs.", count)
    return count, last_completed_page
