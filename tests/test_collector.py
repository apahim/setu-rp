"""Tests for entity collectors with mocked API responses."""



from setu_rp.collection.pull_requests import collect_pull_requests
from setu_rp.collection.reviews import collect_reviews
from setu_rp.collection.comments import (
    collect_review_comments,
    collect_issue_comments,
    _extract_pr_number_from_url,
)
from setu_rp.collection.labels import collect_labels
from setu_rp.collection.collector import _discover_bot_adoption_date
from setu_rp.db.connection import upsert


def test_extract_pr_number_from_pull_url():
    url = "https://api.github.com/repos/openshift/hypershift/pulls/42"
    assert _extract_pr_number_from_url(url) == 42


def test_extract_pr_number_from_issue_url():
    url = "https://api.github.com/repos/openshift/hypershift/issues/99"
    assert _extract_pr_number_from_url(url) == 99


def test_extract_pr_number_invalid():
    assert _extract_pr_number_from_url("https://example.com") is None


def test_collect_pull_requests(db_conn, sample_pr, mock_client):
    mock_client.get_paginated_with_pages.return_value = iter([(1, [sample_pr])])

    count, last_page = collect_pull_requests(
        mock_client, db_conn, "openshift", "hypershift"
    )

    assert count == 1
    assert last_page == 1

    row = db_conn.execute("SELECT * FROM pull_requests WHERE number = 42").fetchone()
    assert row is not None
    assert row["title"] == "Fix nodepool scaling"
    assert row["state"] == "merged"

    user = db_conn.execute("SELECT * FROM users WHERE login = 'testuser'").fetchone()
    assert user is not None

    labels = db_conn.execute("SELECT COUNT(*) as cnt FROM pull_request_labels").fetchone()
    assert labels["cnt"] == 2


def test_collect_pull_requests_incremental(db_conn, sample_pr, mock_client):
    """Test that incremental sync stops at already-synced PRs."""
    old_pr = dict(sample_pr)
    old_pr["updated_at"] = "2023-12-01T00:00:00Z"
    old_pr["id"] = 999
    old_pr["number"] = 10

    mock_client.get_paginated_with_pages.return_value = iter([
        (1, [sample_pr, old_pr])
    ])

    count, _ = collect_pull_requests(
        mock_client, db_conn, "openshift", "hypershift",
        last_sync="2024-01-01T00:00:00Z",
    )

    assert count == 1  # Only the newer PR


def test_collect_reviews(db_conn, sample_pr, sample_review, mock_client):
    # First insert a PR so the review can reference it
    upsert(db_conn, "users", {
        "id": 100, "login": "testuser", "type": "User",
        "name": "Test", "fetched_at": "2024-01-01",
    })
    upsert(db_conn, "pull_requests", {
        "id": sample_pr["id"], "number": 42, "state": "merged",
        "author_id": 100, "fetched_at": "2024-01-01",
    })
    db_conn.commit()

    mock_client.get_paginated.return_value = iter([sample_review])

    count = collect_reviews(mock_client, db_conn, "openshift", "hypershift")

    assert count == 1
    review = db_conn.execute("SELECT * FROM reviews").fetchone()
    assert review["state"] == "APPROVED"


def test_collect_labels(db_conn, mock_client):
    mock_client.get_paginated.return_value = iter([
        {"id": 1, "name": "bug", "description": "Bug fix"},
        {"id": 2, "name": "feature", "description": "New feature"},
    ])

    count = collect_labels(mock_client, db_conn, "openshift", "hypershift")
    assert count == 2


def test_collect_review_comments(db_conn, sample_pr, sample_review_comment, mock_client):
    # Insert prerequisite PR
    upsert(db_conn, "users", {
        "id": 100, "login": "testuser", "type": "User",
        "name": "Test", "fetched_at": "2024-01-01",
    })
    upsert(db_conn, "pull_requests", {
        "id": sample_pr["id"], "number": 42, "state": "merged",
        "author_id": 100, "fetched_at": "2024-01-01",
    })
    db_conn.commit()

    mock_client.get_paginated_with_pages.return_value = iter([
        (1, [sample_review_comment])
    ])

    count, _ = collect_review_comments(
        mock_client, db_conn, "openshift", "hypershift"
    )
    assert count == 1


def test_collect_issue_comments(db_conn, sample_pr, sample_issue_comment, mock_client):
    upsert(db_conn, "users", {
        "id": 100, "login": "testuser", "type": "User",
        "name": "Test", "fetched_at": "2024-01-01",
    })
    upsert(db_conn, "pull_requests", {
        "id": sample_pr["id"], "number": 42, "state": "merged",
        "author_id": 100, "fetched_at": "2024-01-01",
    })
    db_conn.commit()

    mock_client.get_paginated_with_pages.return_value = iter([
        (1, [sample_issue_comment])
    ])

    count, _ = collect_issue_comments(
        mock_client, db_conn, "openshift", "hypershift"
    )
    assert count == 1


def test_discover_bot_adoption_date(db_conn):
    # Insert a bot user and a review from that bot
    upsert(db_conn, "users", {
        "id": 300, "login": "coderabbit[bot]", "type": "Bot",
        "name": None, "fetched_at": "2024-01-01",
    })
    upsert(db_conn, "users", {
        "id": 100, "login": "testuser", "type": "User",
        "name": "Test", "fetched_at": "2024-01-01",
    })
    upsert(db_conn, "pull_requests", {
        "id": 1, "number": 1, "state": "merged",
        "author_id": 100, "fetched_at": "2024-01-01",
    })
    upsert(db_conn, "reviews", {
        "id": 1, "pull_request_id": 1, "reviewer_id": 300,
        "state": "COMMENTED", "submitted_at": "2024-06-15T10:00:00Z",
        "fetched_at": "2024-01-01",
    })
    upsert(db_conn, "repositories", {
        "id": 1, "owner": "openshift", "name": "hypershift",
        "full_name": "openshift/hypershift", "fetched_at": "2024-01-01",
    })
    db_conn.commit()

    date = _discover_bot_adoption_date(db_conn)
    assert date == "2024-06-15T10:00:00Z"

    repo = db_conn.execute("SELECT bot_adoption_date FROM repositories").fetchone()
    assert repo["bot_adoption_date"] == "2024-06-15T10:00:00Z"
