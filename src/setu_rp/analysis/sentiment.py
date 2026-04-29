"""Sentiment analysis for code review comments using VADER.

Preprocesses comment text (strips markdown, code blocks, mentions) and scores
sentiment using the VADER lexicon, which is well-validated for short social-media-
style text. Code review comments share similar characteristics (short, informal,
mixed technical/social language).
"""

import logging
import re
import sqlite3

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from setu_rp.analysis.bot_detection import is_bot_user

logger = logging.getLogger(__name__)

_analyzer = None


def _get_analyzer() -> SentimentIntensityAnalyzer:
    """Lazily initialise the VADER analyzer (singleton)."""
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentIntensityAnalyzer()
        # Add code-review-specific terms
        _analyzer.lexicon.update({
            "lgtm": 2.0,
            "LGTM": 2.0,
            "nit": -0.5,
            "nit:": -0.5,
            "ptal": 0.5,
            "PTAL": 0.5,
            "wip": -0.3,
            "WIP": -0.3,
            "shipit": 2.0,
            "+1": 1.5,
            "-1": -1.5,
            "ack": 1.0,
            "nack": -1.5,
        })
    return _analyzer


def preprocess_comment(text: str | None) -> str:
    """Clean a comment body for sentiment analysis.

    Removes:
    - Fenced code blocks (``` ... ```)
    - Inline code (`...`)
    - HTML tags
    - GitHub @mentions
    - URLs
    - Markdown image/link syntax
    - Excessive whitespace
    """
    if not text:
        return ""

    # Remove fenced code blocks (multiline)
    text = re.sub(r"```[\s\S]*?```", "", text)

    # Remove inline code
    text = re.sub(r"`[^`]+`", "", text)

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)

    # Remove GitHub @mentions
    text = re.sub(r"@[\w-]+", "", text)

    # Remove markdown image/link syntax but keep alt text
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)

    # Remove markdown headers
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)

    # Remove markdown bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def score_sentiment(text: str) -> dict:
    """Score preprocessed text using VADER.

    Returns dict with keys: compound, positive, negative, neutral, word_count.
    """
    analyzer = _get_analyzer()
    if not text:
        return {
            "compound": 0.0,
            "positive": 0.0,
            "negative": 0.0,
            "neutral": 0.0,
            "word_count": 0,
        }

    scores = analyzer.polarity_scores(text)
    word_count = len(text.split())

    return {
        "compound": scores["compound"],
        "positive": scores["pos"],
        "negative": scores["neg"],
        "neutral": scores["neu"],
        "word_count": word_count,
    }


def compute_comment_sentiments(
    conn: sqlite3.Connection,
    run_id: int,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
):
    """Compute and store sentiment scores for all comments in the analysis window.

    Processes review_comments and issue_comments, joining with users to flag
    bot vs human, and with pull_requests to assign period.
    """
    count = 0

    # Review comments
    rows = conn.execute(
        "SELECT rc.id, rc.pull_request_id, rc.author_id, rc.body, "
        "u.type as user_type, u.login as user_login, "
        "p.created_at as pr_created_at "
        "FROM review_comments rc "
        "JOIN users u ON rc.author_id = u.id "
        "JOIN pull_requests p ON rc.pull_request_id = p.id "
        "WHERE p.created_at >= ? AND p.created_at < ?",
        (pre_start, post_end),
    ).fetchall()

    for row in rows:
        period = "pre" if row["pr_created_at"] < pre_end else "post"
        is_bot = 1 if is_bot_user(row["user_type"], row["user_login"]) else 0
        cleaned = preprocess_comment(row["body"])
        scores = score_sentiment(cleaned)

        conn.execute(
            "INSERT OR REPLACE INTO comment_sentiments "
            "(comment_type, comment_id, pull_request_id, author_id, "
            "period, is_bot, compound_score, positive, negative, neutral, "
            "word_count, analysis_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "review_comment",
                row["id"],
                row["pull_request_id"],
                row["author_id"],
                period,
                is_bot,
                scores["compound"],
                scores["positive"],
                scores["negative"],
                scores["neutral"],
                scores["word_count"],
                run_id,
            ),
        )
        count += 1

    # Issue comments
    rows = conn.execute(
        "SELECT ic.id, ic.pull_request_id, ic.author_id, ic.body, "
        "u.type as user_type, u.login as user_login, "
        "p.created_at as pr_created_at "
        "FROM issue_comments ic "
        "JOIN users u ON ic.author_id = u.id "
        "JOIN pull_requests p ON ic.pull_request_id = p.id "
        "WHERE p.created_at >= ? AND p.created_at < ?",
        (pre_start, post_end),
    ).fetchall()

    for row in rows:
        period = "pre" if row["pr_created_at"] < pre_end else "post"
        is_bot = 1 if is_bot_user(row["user_type"], row["user_login"]) else 0
        cleaned = preprocess_comment(row["body"])
        scores = score_sentiment(cleaned)

        conn.execute(
            "INSERT OR REPLACE INTO comment_sentiments "
            "(comment_type, comment_id, pull_request_id, author_id, "
            "period, is_bot, compound_score, positive, negative, neutral, "
            "word_count, analysis_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "issue_comment",
                row["id"],
                row["pull_request_id"],
                row["author_id"],
                period,
                is_bot,
                scores["compound"],
                scores["positive"],
                scores["negative"],
                scores["neutral"],
                scores["word_count"],
                run_id,
            ),
        )
        count += 1

    logger.info("Computed sentiment for %d comments", count)
    return count


def aggregate_pr_sentiment(conn: sqlite3.Connection, run_id: int):
    """Aggregate per-PR average sentiment from comment_sentiments into pr_metrics."""
    # Human sentiment per PR
    rows = conn.execute(
        "SELECT pull_request_id, AVG(compound_score) as avg_sentiment "
        "FROM comment_sentiments "
        "WHERE analysis_run_id = ? AND is_bot = 0 "
        "GROUP BY pull_request_id",
        (run_id,),
    ).fetchall()

    for row in rows:
        conn.execute(
            "UPDATE pr_metrics SET avg_human_sentiment = ? "
            "WHERE pull_request_id = ? AND analysis_run_id = ?",
            (row["avg_sentiment"], row["pull_request_id"], run_id),
        )

    # Bot sentiment per PR
    rows = conn.execute(
        "SELECT pull_request_id, AVG(compound_score) as avg_sentiment "
        "FROM comment_sentiments "
        "WHERE analysis_run_id = ? AND is_bot = 1 "
        "GROUP BY pull_request_id",
        (run_id,),
    ).fetchall()

    for row in rows:
        conn.execute(
            "UPDATE pr_metrics SET avg_bot_sentiment = ? "
            "WHERE pull_request_id = ? AND analysis_run_id = ?",
            (row["avg_sentiment"], row["pull_request_id"], run_id),
        )

    logger.info("Aggregated sentiment scores to pr_metrics")
