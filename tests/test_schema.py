"""Tests for database schema initialization."""

from setu_rp.db.connection import get_connection, upsert
from setu_rp.db.schema import init_db


def test_init_db_creates_tables(db_path):
    with get_connection(db_path) as conn:
        init_db(conn)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row["name"] for row in cursor.fetchall()]

    expected = [
        "governance_documents",
        "issue_comments",
        "labels",
        "pull_request_labels",
        "pull_requests",
        "repositories",
        "review_comments",
        "reviews",
        "sync_metadata",
        "users",
    ]
    # Filter out sqlite internal tables
    app_tables = [t for t in tables if not t.startswith("sqlite_")]
    assert app_tables == expected


def test_init_db_idempotent(db_path):
    with get_connection(db_path) as conn:
        init_db(conn)
        init_db(conn)  # Should not raise
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row["name"] for row in cursor.fetchall()]
        app_tables = [t for t in tables if not t.startswith("sqlite_")]
    assert len(app_tables) == 10


def test_upsert(db_conn):
    upsert(db_conn, "users", {
        "id": 1, "login": "testuser", "type": "User", "name": "Test", "fetched_at": "2024-01-01"
    })
    row = db_conn.execute("SELECT * FROM users WHERE id = 1").fetchone()
    assert row["login"] == "testuser"

    # Upsert with updated name
    upsert(db_conn, "users", {
        "id": 1, "login": "testuser", "type": "User", "name": "Updated", "fetched_at": "2024-01-02"
    })
    row = db_conn.execute("SELECT * FROM users WHERE id = 1").fetchone()
    assert row["name"] == "Updated"


def test_foreign_keys_enforced(db_conn):
    import sqlite3
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO reviews (id, pull_request_id, fetched_at) VALUES (1, 99999, '2024-01-01')"
        )


def test_wal_mode(db_path):
    with get_connection(db_path) as conn:
        result = conn.execute("PRAGMA journal_mode").fetchone()
        assert result[0] == "wal"
