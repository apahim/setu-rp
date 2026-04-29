"""Tests for governance document collection and analysis."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from setu_rp.analysis.governance import analyze_governance
from setu_rp.analysis.schema import init_analysis_tables
from setu_rp.analysis.time_windows import TimeWindow


@pytest.fixture
def governance_db(db_conn):
    """DB with collection + analysis schemas initialized."""
    init_analysis_tables(db_conn)
    return db_conn


@pytest.fixture
def window():
    """Standard time window for testing."""
    return TimeWindow(
        bot_adoption_date=datetime(2024, 6, 1),
        pre_start=datetime(2024, 3, 1),
        pre_end=datetime(2024, 6, 1),
        post_start=datetime(2024, 6, 1),
        post_end=datetime(2024, 9, 1),
        pre_months=3,
        post_months=3,
    )


@pytest.fixture
def seeded_governance_db(governance_db, window):
    """DB with governance documents for testing."""
    conn = governance_db

    # Create analysis run
    conn.execute(
        "INSERT INTO analysis_runs "
        "(id, run_at, bot_adoption_date, pre_window_months, post_window_months, "
        "pre_start, pre_end, post_start, post_end) "
        "VALUES (1, '2024-09-01', '2024-06-01', 3, 3, "
        "'2024-03-01', '2024-06-01', '2024-06-01', '2024-09-01')"
    )

    # Insert governance document versions for CONTRIBUTING.md
    versions = [
        ("CONTRIBUTING.md", "aaa111", "2024-04-01T10:00:00", "user1",
         "# Contributing\n\nPlease submit PRs.\n"),
        ("CONTRIBUTING.md", "bbb222", "2024-05-15T10:00:00", "user2",
         "# Contributing\n\nPlease submit PRs.\n\n## Review Policy\n\n"
         "All PRs require approval from a reviewer.\n"),
        ("CONTRIBUTING.md", "ccc333", "2024-07-01T10:00:00", "user1",
         "# Contributing\n\nPlease submit PRs.\n\n## Review Policy\n\n"
         "All PRs require approval from a reviewer.\n\n"
         "## Automated Review\n\nBot automated checks run on all PRs.\n"),
    ]
    for fp, sha, date, author, content in versions:
        conn.execute(
            "INSERT INTO governance_documents "
            "(file_path, commit_sha, commit_date, author_login, content, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, '2024-09-01')",
            (fp, sha, date, author, content),
        )

    conn.commit()
    return conn


class TestGovernanceAnalysis:
    def test_analyze_governance_basic(self, seeded_governance_db, window):
        """Should detect changes and classify periods."""
        analyze_governance(seeded_governance_db, run_id=1, window=window)

        changes = seeded_governance_db.execute(
            "SELECT * FROM governance_changes WHERE analysis_run_id = 1 "
            "ORDER BY change_date"
        ).fetchall()

        assert len(changes) == 2  # Two diffs (v1->v2, v2->v3)

        # First change is pre-period (2024-05-15)
        assert changes[0]["period"] == "pre"
        assert changes[0]["lines_added"] > 0

        # Second change is post-period (2024-07-01)
        assert changes[1]["period"] == "post"
        assert changes[1]["lines_added"] > 0

    def test_keyword_detection(self, seeded_governance_db, window):
        """Should detect governance keywords in diffs."""
        analyze_governance(seeded_governance_db, run_id=1, window=window)

        changes = seeded_governance_db.execute(
            "SELECT * FROM governance_changes WHERE analysis_run_id = 1 "
            "ORDER BY change_date"
        ).fetchall()

        # First change adds "approval" and "reviewer"
        kw_added_1 = changes[0]["keywords_added"] or ""
        assert "approval" in kw_added_1
        assert "reviewer" in kw_added_1

        # Second change adds "bot" and "automated"
        kw_added_2 = changes[1]["keywords_added"] or ""
        assert "bot" in kw_added_2
        assert "automated" in kw_added_2

    def test_bot_related_flag(self, seeded_governance_db, window):
        """Changes mentioning bot/automated should be flagged as bot-related."""
        analyze_governance(seeded_governance_db, run_id=1, window=window)

        changes = seeded_governance_db.execute(
            "SELECT * FROM governance_changes WHERE analysis_run_id = 1 "
            "ORDER BY change_date"
        ).fetchall()

        # First change (adds "approval", "reviewer") is not bot-related
        assert changes[0]["bot_related"] == 0
        # Second change (adds "Bot automated") is bot-related
        assert changes[1]["bot_related"] == 1

    def test_category_classification(self, seeded_governance_db, window):
        """Changes should be categorized based on content and file."""
        analyze_governance(seeded_governance_db, run_id=1, window=window)

        changes = seeded_governance_db.execute(
            "SELECT * FROM governance_changes WHERE analysis_run_id = 1 "
            "ORDER BY change_date"
        ).fetchall()

        # First change mentions "approval", "reviewer" -> review_policy
        assert changes[0]["category"] == "review_policy"
        # Second change mentions "Bot", "automated" -> bot_integration
        assert changes[1]["category"] == "bot_integration"

    def test_diff_excerpt(self, seeded_governance_db, window):
        """Changes should include a diff excerpt of added lines."""
        analyze_governance(seeded_governance_db, run_id=1, window=window)

        changes = seeded_governance_db.execute(
            "SELECT * FROM governance_changes WHERE analysis_run_id = 1 "
            "ORDER BY change_date"
        ).fetchall()

        for change in changes:
            assert change["diff_excerpt"] is not None
            assert len(change["diff_excerpt"]) > 0

    def test_graceful_skip_no_data(self, governance_db, window):
        """Should skip gracefully when no governance documents exist."""
        # Create analysis run
        governance_db.execute(
            "INSERT INTO analysis_runs "
            "(id, run_at, bot_adoption_date, pre_window_months, post_window_months, "
            "pre_start, pre_end, post_start, post_end) "
            "VALUES (1, '2024-09-01', '2024-06-01', 3, 3, "
            "'2024-03-01', '2024-06-01', '2024-06-01', '2024-09-01')"
        )
        governance_db.commit()

        # Should not raise
        analyze_governance(governance_db, run_id=1, window=window)

        changes = governance_db.execute(
            "SELECT COUNT(*) as cnt FROM governance_changes"
        ).fetchone()["cnt"]
        assert changes == 0

    def test_diff_lines_counted(self, seeded_governance_db, window):
        """Lines added/removed should be counted correctly."""
        analyze_governance(seeded_governance_db, run_id=1, window=window)

        changes = seeded_governance_db.execute(
            "SELECT * FROM governance_changes WHERE analysis_run_id = 1 "
            "ORDER BY change_date"
        ).fetchall()

        for change in changes:
            assert change["lines_added"] >= 0
            assert change["lines_removed"] >= 0
            assert change["change_type"] == "modified"


class TestGovernanceCollection:
    def test_collect_governance_docs_mocked(self, governance_db):
        """Collection should fetch commits and content from GitHub API."""
        from setu_rp.collection.governance import collect_governance_docs

        mock_client = MagicMock()

        # Mock paginated commits response
        mock_client.get_paginated.return_value = iter([
            {
                "sha": "abc123",
                "author": {"login": "testuser"},
                "commit": {"committer": {"date": "2024-05-01T10:00:00Z"}},
            }
        ])

        # Mock content response
        import base64
        content_b64 = base64.b64encode(b"# CONTRIBUTING\n\nSubmit PRs.\n").decode()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "encoding": "base64",
            "content": content_b64,
        }
        mock_client.get.return_value = mock_response

        count = collect_governance_docs(
            mock_client, governance_db, "openshift", "hypershift",
            tracked_files=["CONTRIBUTING.md"],
        )

        assert count == 1
        row = governance_db.execute(
            "SELECT * FROM governance_documents WHERE file_path = 'CONTRIBUTING.md'"
        ).fetchone()
        assert row is not None
        assert row["commit_sha"] == "abc123"
        assert "CONTRIBUTING" in row["content"]

    def test_collect_skips_missing_files(self, governance_db):
        """Collection should skip files that don't exist in the repo."""
        from setu_rp.collection.governance import collect_governance_docs

        mock_client = MagicMock()
        mock_client.get_paginated.side_effect = Exception("Not Found")

        count = collect_governance_docs(
            mock_client, governance_db, "openshift", "hypershift",
            tracked_files=["NONEXISTENT.md"],
        )

        assert count == 0
