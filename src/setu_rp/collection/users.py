"""User/contributor collection logic."""

import logging
from datetime import datetime, timezone

from setu_rp.collection.client import GitHubClient
from setu_rp.db.connection import upsert

logger = logging.getLogger(__name__)


def collect_users(client: GitHubClient, conn, owner: str, name: str):
    """Collect repository contributors. Full fetch each run (small dataset)."""
    now = datetime.now(timezone.utc).isoformat()
    path = f"/repos/{owner}/{name}/contributors"

    count = 0
    for contributor in client.get_paginated(path):
        # Contributors endpoint returns minimal info — fetch full user details
        user_resp = client.get(f"/users/{contributor['login']}")
        user_data = user_resp.json()

        upsert(conn, "users", {
            "id": user_data["id"],
            "login": user_data["login"],
            "type": user_data.get("type", "User"),
            "name": user_data.get("name"),
            "fetched_at": now,
        })
        count += 1

        if count % 50 == 0:
            conn.commit()
            logger.info("Collected %d contributors.", count)

    conn.commit()
    logger.info("User collection complete: %d contributors.", count)
    return count
