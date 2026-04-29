"""Label collection logic."""

import logging
from datetime import datetime, timezone

from setu_rp.collection.client import GitHubClient
from setu_rp.db.connection import upsert

logger = logging.getLogger(__name__)


def collect_labels(client: GitHubClient, conn, owner: str, name: str):
    """Collect repository labels. Full fetch each run (small dataset)."""
    now = datetime.now(timezone.utc).isoformat()
    path = f"/repos/{owner}/{name}/labels"

    count = 0
    for label in client.get_paginated(path):
        upsert(conn, "labels", {
            "id": label["id"],
            "name": label["name"],
            "description": label.get("description"),
            "fetched_at": now,
        })
        count += 1

    conn.commit()
    logger.info("Label collection complete: %d labels.", count)
    return count
