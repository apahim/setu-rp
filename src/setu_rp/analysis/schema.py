"""Analysis tables DDL — added to the same SQLite database as collection tables."""

import sqlite3

ANALYSIS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    bot_adoption_date TEXT NOT NULL,
    pre_window_months INTEGER NOT NULL,
    post_window_months INTEGER NOT NULL,
    pre_start TEXT NOT NULL,
    pre_end TEXT NOT NULL,
    post_start TEXT NOT NULL,
    post_end TEXT NOT NULL,
    total_prs_pre INTEGER,
    total_prs_post INTEGER,
    total_contributors_pre INTEGER,
    total_contributors_post INTEGER
);

CREATE TABLE IF NOT EXISTS pr_metrics (
    pull_request_id INTEGER PRIMARY KEY REFERENCES pull_requests(id),
    analysis_run_id INTEGER REFERENCES analysis_runs(id),
    period TEXT NOT NULL,
    author_id INTEGER REFERENCES users(id),
    contributor_type TEXT,
    time_to_merge_hours REAL,
    time_to_first_feedback_hours REAL,
    time_to_first_human_feedback_hours REAL,
    time_to_first_human_review_hours REAL,
    review_iterations INTEGER,
    was_rejected INTEGER,
    human_review_comment_count INTEGER,
    bot_review_comment_count INTEGER,
    human_issue_comment_count INTEGER,
    bot_issue_comment_count INTEGER,
    total_human_comments INTEGER,
    total_bot_comments INTEGER,
    additions INTEGER,
    deletions INTEGER,
    changed_files INTEGER,
    avg_human_sentiment REAL,
    avg_bot_sentiment REAL
);
CREATE INDEX IF NOT EXISTS idx_pm_period ON pr_metrics(period);
CREATE INDEX IF NOT EXISTS idx_pm_author_id ON pr_metrics(author_id);
CREATE INDEX IF NOT EXISTS idx_pm_contributor_type ON pr_metrics(contributor_type);
CREATE INDEX IF NOT EXISTS idx_pm_analysis_run_id ON pr_metrics(analysis_run_id);

CREATE TABLE IF NOT EXISTS contributor_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_run_id INTEGER REFERENCES analysis_runs(id),
    user_id INTEGER REFERENCES users(id),
    period TEXT NOT NULL,
    contributor_type TEXT,
    pr_count INTEGER,
    merged_pr_count INTEGER,
    avg_time_to_merge_hours REAL,
    avg_review_iterations REAL,
    returned_in_period INTEGER,
    UNIQUE(analysis_run_id, user_id, period)
);

CREATE TABLE IF NOT EXISTS period_statistics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_run_id INTEGER REFERENCES analysis_runs(id),
    metric_name TEXT NOT NULL,
    period TEXT NOT NULL,
    contributor_type TEXT,
    n INTEGER,
    mean REAL,
    median REAL,
    std_dev REAL,
    min_val REAL,
    max_val REAL,
    q1 REAL,
    q3 REAL,
    UNIQUE(analysis_run_id, metric_name, period, contributor_type)
);

CREATE TABLE IF NOT EXISTS statistical_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_run_id INTEGER REFERENCES analysis_runs(id),
    metric_name TEXT NOT NULL,
    contributor_type TEXT,
    test_name TEXT NOT NULL,
    statistic REAL,
    p_value REAL,
    effect_size REAL,
    effect_size_type TEXT,
    ci_lower REAL,
    ci_upper REAL,
    pre_n INTEGER,
    post_n INTEGER,
    significant INTEGER,
    p_value_adjusted REAL,
    adjusted_significant INTEGER,
    notes TEXT,
    UNIQUE(analysis_run_id, metric_name, contributor_type, test_name)
);

CREATE TABLE IF NOT EXISTS comment_sentiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_type TEXT NOT NULL,
    comment_id INTEGER NOT NULL,
    pull_request_id INTEGER REFERENCES pull_requests(id),
    author_id INTEGER REFERENCES users(id),
    period TEXT NOT NULL,
    is_bot INTEGER NOT NULL,
    compound_score REAL,
    positive REAL,
    negative REAL,
    neutral REAL,
    word_count INTEGER,
    analysis_run_id INTEGER REFERENCES analysis_runs(id),
    UNIQUE(analysis_run_id, comment_type, comment_id)
);
CREATE INDEX IF NOT EXISTS idx_cs_run_period ON comment_sentiments(analysis_run_id, period);
CREATE INDEX IF NOT EXISTS idx_cs_pr ON comment_sentiments(pull_request_id);

CREATE TABLE IF NOT EXISTS sensitivity_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_run_id INTEGER REFERENCES analysis_runs(id),
    window_months INTEGER NOT NULL,
    metric_name TEXT NOT NULL,
    contributor_type TEXT,
    pre_mean REAL,
    post_mean REAL,
    effect_size REAL,
    p_value REAL,
    pre_n INTEGER,
    post_n INTEGER,
    UNIQUE(analysis_run_id, window_months, metric_name, contributor_type)
);

CREATE TABLE IF NOT EXISTS governance_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_run_id INTEGER REFERENCES analysis_runs(id),
    file_path TEXT NOT NULL,
    change_date TEXT NOT NULL,
    period TEXT,
    change_type TEXT,
    lines_added INTEGER,
    lines_removed INTEGER,
    keywords_added TEXT,
    keywords_removed TEXT,
    summary TEXT,
    bot_related INTEGER DEFAULT 0,
    diff_excerpt TEXT,
    category TEXT,
    UNIQUE(analysis_run_id, file_path, change_date)
);
CREATE INDEX IF NOT EXISTS idx_gc_run ON governance_changes(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_gc_period ON governance_changes(period);
"""


_MIGRATIONS = [
    ("pr_metrics", "avg_human_sentiment", "ALTER TABLE pr_metrics ADD COLUMN avg_human_sentiment REAL"),
    ("pr_metrics", "avg_bot_sentiment", "ALTER TABLE pr_metrics ADD COLUMN avg_bot_sentiment REAL"),
    ("statistical_tests", "p_value_adjusted", "ALTER TABLE statistical_tests ADD COLUMN p_value_adjusted REAL"),
    ("statistical_tests", "adjusted_significant", "ALTER TABLE statistical_tests ADD COLUMN adjusted_significant INTEGER"),
    ("governance_changes", "bot_related", "ALTER TABLE governance_changes ADD COLUMN bot_related INTEGER DEFAULT 0"),
    ("governance_changes", "diff_excerpt", "ALTER TABLE governance_changes ADD COLUMN diff_excerpt TEXT"),
    ("governance_changes", "category", "ALTER TABLE governance_changes ADD COLUMN category TEXT"),
]

_RENAME_MIGRATIONS = [
    ("contributor_metrics", "returned_in_post", "returned_in_period",
     "ALTER TABLE contributor_metrics RENAME COLUMN returned_in_post TO returned_in_period"),
]


def init_analysis_tables(conn: sqlite3.Connection):
    """Create analysis tables if they don't exist, then apply column migrations."""
    conn.executescript(ANALYSIS_SCHEMA_SQL)
    _apply_migrations(conn)
    _apply_rename_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection):
    """Add columns that may be missing from existing tables."""
    for table, column, ddl in _MIGRATIONS:
        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(ddl)


def _apply_rename_migrations(conn: sqlite3.Connection):
    """Rename columns from older schemas."""
    for table, old_col, new_col, ddl in _RENAME_MIGRATIONS:
        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if old_col in existing and new_col not in existing:
            conn.execute(ddl)
