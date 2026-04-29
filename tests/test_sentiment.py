"""Tests for sentiment analysis module."""

import pytest

from setu_rp.analysis.schema import init_analysis_tables
from setu_rp.analysis.sentiment import (
    aggregate_pr_sentiment,
    compute_comment_sentiments,
    preprocess_comment,
    score_sentiment,
)


# --- Preprocessing ---

class TestPreprocess:
    def test_removes_code_blocks(self):
        text = "Good change\n```python\ndef foo():\n    pass\n```\nLooks great"
        result = preprocess_comment(text)
        assert "def foo" not in result
        assert "Good change" in result
        assert "Looks great" in result

    def test_removes_inline_code(self):
        text = "The `compute_metrics()` function is well written"
        result = preprocess_comment(text)
        assert "`" not in result
        assert "compute_metrics" not in result
        assert "well written" in result

    def test_removes_urls(self):
        text = "See https://github.com/org/repo/pull/123 for details"
        result = preprocess_comment(text)
        assert "https://" not in result
        assert "details" in result

    def test_removes_mentions(self):
        text = "@alice please review this @bob-smith"
        result = preprocess_comment(text)
        assert "@alice" not in result
        assert "@bob-smith" not in result
        assert "please review this" in result

    def test_removes_html_tags(self):
        text = "<details><summary>Click</summary>Hidden</details>"
        result = preprocess_comment(text)
        assert "<" not in result
        assert "Click" in result
        assert "Hidden" in result

    def test_removes_markdown_headers(self):
        text = "## Summary\nThis is good"
        result = preprocess_comment(text)
        assert "##" not in result
        assert "Summary" in result

    def test_handles_none(self):
        assert preprocess_comment(None) == ""

    def test_handles_empty(self):
        assert preprocess_comment("") == ""

    def test_collapses_whitespace(self):
        text = "word1   word2\n\n\nword3"
        result = preprocess_comment(text)
        assert result == "word1 word2 word3"


# --- Scoring ---

class TestScoring:
    def test_positive_text(self):
        scores = score_sentiment("This is excellent work, great job!")
        assert scores["compound"] > 0.3
        assert scores["word_count"] > 0

    def test_negative_text(self):
        scores = score_sentiment("This is terrible and broken")
        assert scores["compound"] < -0.3

    def test_neutral_text(self):
        scores = score_sentiment("Changed variable name from x to y")
        assert -0.3 < scores["compound"] < 0.3

    def test_lgtm_positive(self):
        scores = score_sentiment("LGTM")
        assert scores["compound"] > 0

    def test_nit_slightly_negative(self):
        scores = score_sentiment("nit: trailing whitespace")
        assert scores["compound"] <= 0

    def test_empty_text(self):
        scores = score_sentiment("")
        assert scores["compound"] == 0.0
        assert scores["word_count"] == 0


# --- Integration ---

class TestSentimentIntegration:
    @pytest.fixture
    def sentiment_db(self, db_conn):
        """DB with schema and sample data for sentiment testing."""
        init_analysis_tables(db_conn)

        db_conn.execute(
            "INSERT INTO repositories (id, owner, name, full_name, bot_adoption_date, fetched_at) "
            "VALUES (1, 'org', 'repo', 'org/repo', '2024-06-01T00:00:00', '2024-01-01')"
        )
        db_conn.execute(
            "INSERT INTO users (id, login, type, name, fetched_at) VALUES "
            "(1, 'human1', 'User', 'Human', '2024-01-01')"
        )
        db_conn.execute(
            "INSERT INTO users (id, login, type, name, fetched_at) VALUES "
            "(2, 'bot1', 'Bot', 'Bot', '2024-01-01')"
        )
        db_conn.execute(
            "INSERT INTO pull_requests (id, number, state, author_id, created_at, updated_at, "
            "closed_at, merged_at, additions, deletions, changed_files, fetched_at) "
            "VALUES (101, 1, 'closed', 1, '2024-04-01T10:00:00', '2024-04-02T10:00:00', "
            "'2024-04-02T10:00:00', '2024-04-02T10:00:00', 10, 5, 2, '2024-01-01')"
        )
        # Human review comment (positive)
        db_conn.execute(
            "INSERT INTO review_comments (id, pull_request_id, review_id, author_id, body, path, "
            "created_at, updated_at, fetched_at) "
            "VALUES (2001, 101, NULL, 1, 'Excellent work, LGTM!', 'main.py', "
            "'2024-04-01T12:00:00', '2024-04-01T12:00:00', '2024-01-01')"
        )
        # Bot review comment
        db_conn.execute(
            "INSERT INTO review_comments (id, pull_request_id, review_id, author_id, body, path, "
            "created_at, updated_at, fetched_at) "
            "VALUES (2002, 101, NULL, 2, 'Automated review summary', 'main.py', "
            "'2024-04-01T12:01:00', '2024-04-01T12:01:00', '2024-01-01')"
        )
        # Human issue comment (negative)
        db_conn.execute(
            "INSERT INTO issue_comments (id, pull_request_id, author_id, body, "
            "created_at, updated_at, fetched_at) "
            "VALUES (3001, 101, 1, 'This approach is wrong and broken', "
            "'2024-04-01T13:00:00', '2024-04-01T13:00:00', '2024-01-01')"
        )

        # Create analysis run
        db_conn.execute(
            "INSERT INTO analysis_runs (id, run_at, bot_adoption_date, pre_window_months, "
            "post_window_months, pre_start, pre_end, post_start, post_end) "
            "VALUES (1, '2024-01-01', '2024-06-01', 3, 3, "
            "'2024-03-01', '2024-06-01', '2024-06-01', '2024-09-01')"
        )
        # Create pr_metrics row
        db_conn.execute(
            "INSERT INTO pr_metrics (pull_request_id, analysis_run_id, period, author_id, "
            "contributor_type, time_to_merge_hours) "
            "VALUES (101, 1, 'pre', 1, 'new', 24.0)"
        )

        db_conn.commit()
        return db_conn

    def test_compute_comment_sentiments(self, sentiment_db):
        count = compute_comment_sentiments(
            sentiment_db, 1,
            "2024-03-01", "2024-06-01", "2024-06-01", "2024-09-01"
        )
        assert count == 3  # 2 review comments + 1 issue comment

        rows = sentiment_db.execute(
            "SELECT * FROM comment_sentiments WHERE analysis_run_id = 1"
        ).fetchall()
        assert len(rows) == 3

        # Check human vs bot
        human_rows = [r for r in rows if r["is_bot"] == 0]
        bot_rows = [r for r in rows if r["is_bot"] == 1]
        assert len(human_rows) == 2
        assert len(bot_rows) == 1

        # Check positive comment has positive score
        positive = sentiment_db.execute(
            "SELECT compound_score FROM comment_sentiments "
            "WHERE comment_id = 2001 AND comment_type = 'review_comment'"
        ).fetchone()
        assert positive["compound_score"] > 0

    def test_aggregate_pr_sentiment(self, sentiment_db):
        compute_comment_sentiments(
            sentiment_db, 1,
            "2024-03-01", "2024-06-01", "2024-06-01", "2024-09-01"
        )
        aggregate_pr_sentiment(sentiment_db, 1)

        row = sentiment_db.execute(
            "SELECT avg_human_sentiment, avg_bot_sentiment FROM pr_metrics "
            "WHERE pull_request_id = 101 AND analysis_run_id = 1"
        ).fetchone()
        # Human avg should be somewhere between positive and negative
        assert row["avg_human_sentiment"] is not None
        # Bot avg should exist (one bot comment)
        assert row["avg_bot_sentiment"] is not None

    def test_sentiment_in_full_analysis(self, db_conn):
        """Sentiment should be computed as part of run_analysis."""
        init_analysis_tables(db_conn)

        db_conn.execute(
            "INSERT INTO repositories (id, owner, name, full_name, bot_adoption_date, fetched_at) "
            "VALUES (1, 'org', 'repo', 'org/repo', '2024-06-01T00:00:00', '2024-01-01')"
        )
        db_conn.execute(
            "INSERT INTO users (id, login, type, name, fetched_at) VALUES "
            "(1, 'human1', 'User', 'Human', '2024-01-01')"
        )
        db_conn.execute(
            "INSERT INTO pull_requests (id, number, state, author_id, created_at, updated_at, "
            "closed_at, merged_at, additions, deletions, changed_files, fetched_at) "
            "VALUES (101, 1, 'closed', 1, '2024-04-01T10:00:00', '2024-04-02T10:00:00', "
            "'2024-04-02T10:00:00', '2024-04-02T10:00:00', 10, 5, 2, '2024-01-01')"
        )
        db_conn.execute(
            "INSERT INTO review_comments (id, pull_request_id, review_id, author_id, body, path, "
            "created_at, updated_at, fetched_at) "
            "VALUES (2001, 101, NULL, 1, 'Great work!', 'main.py', "
            "'2024-04-01T12:00:00', '2024-04-01T12:00:00', '2024-01-01')"
        )
        db_conn.commit()

        from setu_rp.analysis.analyzer import run_analysis
        run_id = run_analysis(db_conn, pre_months=3, post_months=3)

        # Check comment_sentiments populated
        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM comment_sentiments WHERE analysis_run_id = ?",
            (run_id,),
        ).fetchone()["cnt"]
        assert count > 0

        # Check avg_human_sentiment populated
        row = db_conn.execute(
            "SELECT avg_human_sentiment FROM pr_metrics "
            "WHERE analysis_run_id = ? AND avg_human_sentiment IS NOT NULL",
            (run_id,),
        ).fetchone()
        assert row is not None

        # Check sentiment appears in period_statistics
        stat = db_conn.execute(
            "SELECT * FROM period_statistics "
            "WHERE analysis_run_id = ? AND metric_name = 'avg_human_sentiment'",
            (run_id,),
        ).fetchone()
        assert stat is not None

        # Check sentiment appears in statistical_tests
        test = db_conn.execute(
            "SELECT * FROM statistical_tests "
            "WHERE analysis_run_id = ? AND metric_name = 'avg_human_sentiment'",
            (run_id,),
        ).fetchone()
        assert test is not None
