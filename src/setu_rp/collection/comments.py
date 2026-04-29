"""Review comments and issue comments collection."""

import logging
import re
from datetime import datetime, timezone

from setu_rp.collection.client import GitHubClient
from setu_rp.db.connection import upsert

logger = logging.getLogger(__name__)


def _extract_pr_number_from_url(url: str) -> int | None:
    """Extract PR number from a GitHub API pull request URL."""
    match = re.search(r"/pulls/(\d+)", url)
    if match:
        return int(match.group(1))
    match = re.search(r"/issues/(\d+)", url)
    if match:
        return int(match.group(1))
    return None


def _get_pr_id(conn, pr_number: int) -> int | None:
    """Look up pull_request_id from a PR number."""
    row = conn.execute(
        "SELECT id FROM pull_requests WHERE number = ?", (pr_number,)
    ).fetchone()
    return row["id"] if row else None


def collect_review_comments(client: GitHubClient, conn, owner: str, name: str,
                            last_sync: str | None = None, start_page: int = 1):
    """Collect line-level review comments using the repo-wide endpoint."""
    now = datetime.now(timezone.utc).isoformat()
    path = f"/repos/{owner}/{name}/pulls/comments"
    params = {"sort": "updated", "direction": "desc"}
    if last_sync:
        params["since"] = last_sync

    count = 0
    last_completed_page = 0

    for page_num, items in client.get_paginated_with_pages(
        path, params=params, start_page=start_page
    ):
        for comment in items:
            author = comment.get("user")
            if author:
                upsert(conn, "users", {
                    "id": author["id"],
                    "login": author["login"],
                    "type": author.get("type", "User"),
                    "name": author.get("name"),
                    "fetched_at": now,
                })

            pr_url = comment.get("pull_request_url", "")
            pr_number = _extract_pr_number_from_url(pr_url)
            pr_id = _get_pr_id(conn, pr_number) if pr_number else None

            if pr_id is None:
                continue

            upsert(conn, "review_comments", {
                "id": comment["id"],
                "pull_request_id": pr_id,
                "review_id": comment.get("pull_request_review_id"),
                "author_id": author["id"] if author else None,
                "body": comment.get("body"),
                "path": comment.get("path"),
                "created_at": comment.get("created_at"),
                "updated_at": comment.get("updated_at"),
                "fetched_at": now,
            })
            count += 1

        last_completed_page = page_num
        conn.commit()

        if count % 500 == 0 and count > 0:
            logger.info("Collected %d review comments (page %d)", count, page_num)

    logger.info("Review comment collection complete: %d comments.", count)
    return count, last_completed_page


def collect_issue_comments(client: GitHubClient, conn, owner: str, name: str,
                           last_sync: str | None = None, start_page: int = 1):
    """Collect general issue/PR comments using the repo-wide endpoint."""
    now = datetime.now(timezone.utc).isoformat()
    path = f"/repos/{owner}/{name}/issues/comments"
    params = {"sort": "updated", "direction": "desc"}
    if last_sync:
        params["since"] = last_sync

    count = 0
    last_completed_page = 0

    for page_num, items in client.get_paginated_with_pages(
        path, params=params, start_page=start_page
    ):
        for comment in items:
            # Only include comments on PRs (issues with pull_request_url)
            issue_url = comment.get("issue_url", "")
            pr_number = _extract_pr_number_from_url(issue_url)
            pr_id = _get_pr_id(conn, pr_number) if pr_number else None

            if pr_id is None:
                continue

            author = comment.get("user")
            if author:
                upsert(conn, "users", {
                    "id": author["id"],
                    "login": author["login"],
                    "type": author.get("type", "User"),
                    "name": author.get("name"),
                    "fetched_at": now,
                })

            upsert(conn, "issue_comments", {
                "id": comment["id"],
                "pull_request_id": pr_id,
                "author_id": author["id"] if author else None,
                "body": comment.get("body"),
                "created_at": comment.get("created_at"),
                "updated_at": comment.get("updated_at"),
                "fetched_at": now,
            })
            count += 1

        last_completed_page = page_num
        conn.commit()

        if count % 500 == 0 and count > 0:
            logger.info("Collected %d issue comments (page %d)", count, page_num)

    logger.info("Issue comment collection complete: %d comments.", count)
    return count, last_completed_page
