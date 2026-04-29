"""Collection orchestrator: manages sync order, resumability, and bot discovery."""

import logging
from datetime import datetime, timezone

from setu_rp.collection.client import GitHubClient
from setu_rp.collection.comments import collect_issue_comments, collect_review_comments
from setu_rp.collection.labels import collect_labels
from setu_rp.collection.pull_requests import collect_pull_requests
from setu_rp.collection.reviews import collect_reviews
from setu_rp.collection.users import collect_users
from setu_rp.db.connection import upsert

logger = logging.getLogger(__name__)


def _get_sync_metadata(conn, entity_type: str) -> dict | None:
    """Read sync metadata for an entity type."""
    row = conn.execute(
        "SELECT * FROM sync_metadata WHERE entity_type = ?", (entity_type,)
    ).fetchone()
    return dict(row) if row else None


def _update_sync_metadata(conn, entity_type: str, total_items: int,
                          last_page: int = 0):
    """Update sync metadata after successful collection."""
    now = datetime.now(timezone.utc).isoformat()
    upsert(conn, "sync_metadata", {
        "entity_type": entity_type,
        "last_sync": now,
        "last_page": last_page,
        "total_items": total_items,
        "updated_at": now,
    })
    conn.commit()


def _collect_repo_metadata(client: GitHubClient, conn, owner: str, name: str):
    """Fetch and store repository metadata."""
    now = datetime.now(timezone.utc).isoformat()
    resp = client.get(f"/repos/{owner}/{name}")
    repo = resp.json()
    upsert(conn, "repositories", {
        "id": repo["id"],
        "owner": owner,
        "name": name,
        "full_name": repo["full_name"],
        "fetched_at": now,
    })
    conn.commit()
    logger.info("Repository metadata collected: %s", repo["full_name"])


def _discover_bot_adoption_date(conn):
    """Discover bot adoption date from the earliest bot review or comment.

    Scans reviews, review comments, and issue comments from Bot-type users
    to find the earliest activity, then stores it in the repositories table.
    """
    # Find earliest bot review
    result = conn.execute("""
        SELECT MIN(r.submitted_at) as earliest
        FROM reviews r
        JOIN users u ON r.reviewer_id = u.id
        WHERE u.type = 'Bot'
        AND r.submitted_at IS NOT NULL
    """).fetchone()

    earliest_review = result["earliest"] if result else None

    # Find earliest bot review comment
    result = conn.execute("""
        SELECT MIN(rc.created_at) as earliest
        FROM review_comments rc
        JOIN users u ON rc.author_id = u.id
        WHERE u.type = 'Bot'
        AND rc.created_at IS NOT NULL
    """).fetchone()

    earliest_review_comment = result["earliest"] if result else None

    # Find earliest bot issue comment (CodeRabbit and similar bots post here)
    result = conn.execute("""
        SELECT MIN(ic.created_at) as earliest
        FROM issue_comments ic
        JOIN users u ON ic.author_id = u.id
        WHERE u.type = 'Bot'
        AND ic.created_at IS NOT NULL
    """).fetchone()

    earliest_issue_comment = result["earliest"] if result else None

    # Also check PR authorship by code-review bots (not dependabot/CI bots)
    code_review_bots = ("coderabbitai[bot]",)
    result = conn.execute("""
        SELECT MIN(p.created_at) as earliest
        FROM pull_requests p
        JOIN users u ON p.author_id = u.id
        WHERE u.type = 'Bot'
        AND u.login IN ({})
        AND p.created_at IS NOT NULL
    """.format(",".join("?" for _ in code_review_bots)), code_review_bots).fetchone()

    earliest_bot_pr = result["earliest"] if result else None

    # Take the earliest of all sources
    candidates = [d for d in [earliest_review, earliest_review_comment,
                              earliest_issue_comment, earliest_bot_pr] if d]
    if candidates:
        bot_adoption_date = min(candidates)
        conn.execute(
            "UPDATE repositories SET bot_adoption_date = ?", (bot_adoption_date,)
        )
        conn.commit()
        logger.info("Bot adoption date discovered: %s", bot_adoption_date)
        return bot_adoption_date

    logger.warning("No bot activity found — bot adoption date not set.")
    return None


def run_collection(client: GitHubClient, conn, owner: str, name: str):
    """Run the full collection pipeline in the correct order."""
    logger.info("Starting data collection for %s/%s", owner, name)

    # 1. Repository metadata
    _collect_repo_metadata(client, conn, owner, name)

    # 2. Labels (small, no dependencies)
    meta = _get_sync_metadata(conn, "labels")
    count = collect_labels(client, conn, owner, name)
    _update_sync_metadata(conn, "labels", count)

    # 3. Pull requests (needed before reviews/comments)
    meta = _get_sync_metadata(conn, "pull_requests")
    last_sync = meta["last_sync"] if meta else None
    start_page = (meta["last_page"] + 1) if meta and meta["last_page"] > 0 and not last_sync else 1
    count, last_page = collect_pull_requests(
        client, conn, owner, name, last_sync=last_sync, start_page=start_page
    )
    existing = meta["total_items"] if meta else 0
    _update_sync_metadata(conn, "pull_requests", existing + count)

    # 4. Reviews (depends on PRs)
    meta = _get_sync_metadata(conn, "reviews")
    last_sync_reviews = meta["last_sync"] if meta else None
    count = collect_reviews(client, conn, owner, name, last_sync=last_sync_reviews)
    existing = meta["total_items"] if meta else 0
    _update_sync_metadata(conn, "reviews", existing + count)

    # 5. Review comments (repo-wide endpoint)
    meta = _get_sync_metadata(conn, "review_comments")
    last_sync_rc = meta["last_sync"] if meta else None
    start_page = (meta["last_page"] + 1) if meta and meta["last_page"] > 0 and not last_sync_rc else 1
    count, last_page = collect_review_comments(
        client, conn, owner, name, last_sync=last_sync_rc, start_page=start_page
    )
    existing = meta["total_items"] if meta else 0
    _update_sync_metadata(conn, "review_comments", existing + count)

    # 6. Issue comments (repo-wide endpoint)
    meta = _get_sync_metadata(conn, "issue_comments")
    last_sync_ic = meta["last_sync"] if meta else None
    start_page = (meta["last_page"] + 1) if meta and meta["last_page"] > 0 and not last_sync_ic else 1
    count, last_page = collect_issue_comments(
        client, conn, owner, name, last_sync=last_sync_ic, start_page=start_page
    )
    existing = meta["total_items"] if meta else 0
    _update_sync_metadata(conn, "issue_comments", existing + count)

    # 7. Users/contributors (enriches existing user data)
    meta = _get_sync_metadata(conn, "users")
    count = collect_users(client, conn, owner, name)
    _update_sync_metadata(conn, "users", count)

    # 8. Discover bot adoption date
    _discover_bot_adoption_date(conn)

    # 9. Governance documents (optional, graceful on failure)
    try:
        from setu_rp.collection.governance import collect_governance_docs
        count = collect_governance_docs(client, conn, owner, name)
        _update_sync_metadata(conn, "governance_documents", count)
    except Exception:
        logger.warning("Governance document collection failed (non-fatal)", exc_info=True)

    logger.info("Data collection complete for %s/%s.", owner, name)
