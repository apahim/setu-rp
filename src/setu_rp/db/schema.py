"""Database schema definitions and initialization."""

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    entity_type TEXT PRIMARY KEY,
    last_sync TEXT,
    last_page INTEGER DEFAULT 0,
    total_items INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    bot_adoption_date TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    login TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL DEFAULT 'User',
    name TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_login ON users(login);
CREATE INDEX IF NOT EXISTS idx_users_type ON users(type);

CREATE TABLE IF NOT EXISTS pull_requests (
    id INTEGER PRIMARY KEY,
    number INTEGER UNIQUE NOT NULL,
    title TEXT,
    state TEXT NOT NULL,
    author_id INTEGER REFERENCES users(id),
    created_at TEXT,
    updated_at TEXT,
    closed_at TEXT,
    merged_at TEXT,
    merge_commit_sha TEXT,
    additions INTEGER,
    deletions INTEGER,
    changed_files INTEGER,
    body TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pr_created_at ON pull_requests(created_at);
CREATE INDEX IF NOT EXISTS idx_pr_merged_at ON pull_requests(merged_at);
CREATE INDEX IF NOT EXISTS idx_pr_author_id ON pull_requests(author_id);
CREATE INDEX IF NOT EXISTS idx_pr_updated_at ON pull_requests(updated_at);
CREATE INDEX IF NOT EXISTS idx_pr_state ON pull_requests(state);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_labels_name ON labels(name);

CREATE TABLE IF NOT EXISTS pull_request_labels (
    pull_request_id INTEGER REFERENCES pull_requests(id),
    label_id INTEGER REFERENCES labels(id),
    PRIMARY KEY (pull_request_id, label_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    pull_request_id INTEGER NOT NULL REFERENCES pull_requests(id),
    reviewer_id INTEGER REFERENCES users(id),
    state TEXT,
    body TEXT,
    submitted_at TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_pr_id ON reviews(pull_request_id);
CREATE INDEX IF NOT EXISTS idx_reviews_reviewer_id ON reviews(reviewer_id);
CREATE INDEX IF NOT EXISTS idx_reviews_submitted_at ON reviews(submitted_at);
CREATE INDEX IF NOT EXISTS idx_reviews_state ON reviews(state);

CREATE TABLE IF NOT EXISTS review_comments (
    id INTEGER PRIMARY KEY,
    pull_request_id INTEGER NOT NULL REFERENCES pull_requests(id),
    review_id INTEGER,
    author_id INTEGER REFERENCES users(id),
    body TEXT,
    path TEXT,
    created_at TEXT,
    updated_at TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rc_pr_id ON review_comments(pull_request_id);
CREATE INDEX IF NOT EXISTS idx_rc_author_id ON review_comments(author_id);
CREATE INDEX IF NOT EXISTS idx_rc_created_at ON review_comments(created_at);

CREATE TABLE IF NOT EXISTS issue_comments (
    id INTEGER PRIMARY KEY,
    pull_request_id INTEGER NOT NULL REFERENCES pull_requests(id),
    author_id INTEGER REFERENCES users(id),
    body TEXT,
    created_at TEXT,
    updated_at TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ic_pr_id ON issue_comments(pull_request_id);
CREATE INDEX IF NOT EXISTS idx_ic_author_id ON issue_comments(author_id);
CREATE INDEX IF NOT EXISTS idx_ic_created_at ON issue_comments(created_at);


CREATE TABLE IF NOT EXISTS governance_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    commit_date TEXT NOT NULL,
    author_login TEXT,
    content TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(file_path, commit_sha)
);
CREATE INDEX IF NOT EXISTS idx_gd_file_path ON governance_documents(file_path);
CREATE INDEX IF NOT EXISTS idx_gd_commit_date ON governance_documents(commit_date);
"""


def init_db(conn: sqlite3.Connection):
    """Initialize the database schema."""
    conn.executescript(SCHEMA_SQL)
