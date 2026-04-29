"""Governance document collection: version history of project governance files."""

import base64
import logging
from datetime import datetime, timezone

from setu_rp.collection.client import GitHubClient

logger = logging.getLogger(__name__)

DEFAULT_TRACKED_FILES = [
    "CONTRIBUTING.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "OWNERS",
    "REVIEWERS",
]


def collect_governance_docs(
    client: GitHubClient,
    conn,
    owner: str,
    repo: str,
    tracked_files: list[str] | None = None,
):
    """Collect version history of governance files from GitHub.

    For each tracked file, fetches the commit history and content at each commit.
    Upserts into the governance_documents table.

    Returns total number of document versions collected.
    """
    files = tracked_files or DEFAULT_TRACKED_FILES
    now = datetime.now(timezone.utc).isoformat()
    total = 0

    for file_path in files:
        logger.info("Collecting governance history for %s", file_path)
        try:
            commits = _get_file_commits(client, owner, repo, file_path)
        except Exception:
            logger.warning("Could not fetch commits for %s (may not exist)", file_path)
            continue

        for commit_info in commits:
            sha = commit_info["sha"]
            commit_date = commit_info["date"]
            author_login = commit_info["author_login"]

            # Check if already collected
            existing = conn.execute(
                "SELECT id FROM governance_documents "
                "WHERE file_path = ? AND commit_sha = ?",
                (file_path, sha),
            ).fetchone()
            if existing:
                continue

            content = _get_file_content(client, owner, repo, file_path, sha)

            conn.execute(
                "INSERT OR REPLACE INTO governance_documents "
                "(file_path, commit_sha, commit_date, author_login, content, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (file_path, sha, commit_date, author_login, content, now),
            )
            total += 1

        conn.commit()
        logger.info("Collected %d versions for %s", total, file_path)

    logger.info("Governance collection complete: %d document versions", total)
    return total


def _get_file_commits(
    client: GitHubClient, owner: str, repo: str, file_path: str
) -> list[dict]:
    """Get all commits that modified a specific file."""
    commits = []
    for item in client.get_paginated(
        f"/repos/{owner}/{repo}/commits",
        params={"path": file_path},
    ):
        author = item.get("author") or {}
        commit_data = item.get("commit", {})
        committer_date = commit_data.get("committer", {}).get("date")
        commits.append({
            "sha": item["sha"],
            "date": committer_date,
            "author_login": author.get("login"),
        })
    return commits


def _get_file_content(
    client: GitHubClient, owner: str, repo: str, file_path: str, sha: str
) -> str | None:
    """Get file content at a specific commit SHA."""
    try:
        resp = client.get(
            f"/repos/{owner}/{repo}/contents/{file_path}",
            params={"ref": sha},
        )
        data = resp.json()
        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("content")
    except Exception:
        logger.warning("Could not fetch content of %s at %s", file_path, sha[:8])
        return None
