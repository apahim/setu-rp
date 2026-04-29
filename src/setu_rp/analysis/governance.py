"""Governance document analysis: diff computation and keyword detection."""

import difflib
import logging
import sqlite3

from setu_rp.analysis.time_windows import TimeWindow, classify_pr_period

logger = logging.getLogger(__name__)

DEFAULT_KEYWORDS = [
    "required", "approval", "reviewer", "bot", "automated",
    "policy", "checklist", "merge", "CI", "test",
]

BOT_KEYWORDS = {"bot", "automated", "coderabbit", "auto-review", "ai"}

CATEGORY_RULES = [
    ("bot_integration", {"bot", "automated", "coderabbit", "auto-review", "ai"}),
    ("review_policy", {"approval", "reviewer", "review", "lgtm", "approver"}),
    ("ci_testing", {"ci", "test", "pipeline", "check", "lint", "verify"}),
    ("contribution_guide", {"contributing", "pr", "commit", "style", "guideline"}),
]


def _classify_category(file_path: str, added_text: str) -> str:
    """Classify a governance change into a category based on file and content."""
    basename = file_path.rsplit("/", 1)[-1].upper()
    if basename in ("OWNERS", "REVIEWERS"):
        return "ownership"

    text_lower = added_text.lower()
    for category, terms in CATEGORY_RULES:
        if any(t in text_lower for t in terms):
            return category

    return "other"


def _is_bot_related(added_text: str) -> bool:
    """Check if change content is related to bot/automation adoption."""
    text_lower = added_text.lower()
    return any(kw in text_lower for kw in BOT_KEYWORDS)


def _make_diff_excerpt(diff_lines: list[str], max_len: int = 500) -> str:
    """Extract added lines from a diff as a readable excerpt."""
    added = [
        line[1:].strip() for line in diff_lines
        if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
    ]
    text = "\n".join(added)
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def analyze_governance(
    conn: sqlite3.Connection,
    run_id: int,
    window: TimeWindow,
    keywords: list[str] | None = None,
):
    """Analyze governance document changes across pre/post periods.

    For each tracked file, computes diffs between consecutive versions,
    counts lines added/removed, detects governance keywords, classifies
    changes by category, and flags bot-related changes.

    Skips gracefully if governance_documents table is empty.
    """
    kw_list = keywords or DEFAULT_KEYWORDS

    # Check if governance_documents table exists and has data
    try:
        count = conn.execute("SELECT COUNT(*) as cnt FROM governance_documents").fetchone()["cnt"]
    except sqlite3.OperationalError:
        logger.info("governance_documents table not found, skipping governance analysis")
        return
    if count == 0:
        logger.info("No governance documents collected, skipping governance analysis")
        return

    # Get distinct tracked files
    files = conn.execute(
        "SELECT DISTINCT file_path FROM governance_documents ORDER BY file_path"
    ).fetchall()

    total_changes = 0
    for file_row in files:
        file_path = file_row["file_path"]
        versions = conn.execute(
            "SELECT commit_sha, commit_date, author_login, content "
            "FROM governance_documents "
            "WHERE file_path = ? ORDER BY commit_date ASC",
            (file_path,),
        ).fetchall()

        if len(versions) < 2:
            continue

        for i in range(1, len(versions)):
            prev = versions[i - 1]
            curr = versions[i]

            prev_content = prev["content"] or ""
            curr_content = curr["content"] or ""
            prev_lines = prev_content.splitlines(keepends=True)
            curr_lines = curr_content.splitlines(keepends=True)

            diff = list(difflib.unified_diff(prev_lines, curr_lines, n=0))

            lines_added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
            lines_removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

            if lines_added == 0 and lines_removed == 0:
                continue

            # Keyword detection
            added_text = " ".join(
                line[1:] for line in diff
                if line.startswith("+") and not line.startswith("+++")
            )
            removed_text = " ".join(
                line[1:] for line in diff
                if line.startswith("-") and not line.startswith("---")
            )

            kw_added = [kw for kw in kw_list if kw.lower() in added_text.lower()]
            kw_removed = [kw for kw in kw_list if kw.lower() in removed_text.lower()]

            # Classify period
            period = classify_pr_period(curr["commit_date"], window)

            # Determine change type
            if not prev_content.strip():
                change_type = "created"
            elif not curr_content.strip():
                change_type = "deleted"
            else:
                change_type = "modified"

            # New fields
            bot_related = 1 if _is_bot_related(added_text) else 0
            diff_excerpt = _make_diff_excerpt(diff)
            category = _classify_category(file_path, added_text)

            conn.execute(
                "INSERT OR REPLACE INTO governance_changes "
                "(analysis_run_id, file_path, change_date, period, change_type, "
                "lines_added, lines_removed, keywords_added, keywords_removed, "
                "summary, bot_related, diff_excerpt, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    file_path,
                    curr["commit_date"],
                    period,
                    change_type,
                    lines_added,
                    lines_removed,
                    ",".join(kw_added) if kw_added else None,
                    ",".join(kw_removed) if kw_removed else None,
                    f"{lines_added}+ {lines_removed}- by {curr['author_login'] or 'unknown'}",
                    bot_related,
                    diff_excerpt,
                    category,
                ),
            )
            total_changes += 1

    logger.info("Governance analysis complete: %d changes recorded", total_changes)
