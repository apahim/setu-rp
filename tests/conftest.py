"""Shared test fixtures."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from setu_rp.db.connection import get_connection
from setu_rp.db.schema import init_db


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def db_path(tmp_path):
    """Provide a temporary database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def db_conn(db_path):
    """Provide an initialized database connection."""
    with get_connection(db_path) as conn:
        init_db(conn)
        yield conn


@pytest.fixture
def sample_pr():
    """Sample PR data as returned by GitHub API."""
    return json.loads((FIXTURES_DIR / "pull_request.json").read_text())


@pytest.fixture
def sample_review():
    """Sample review data as returned by GitHub API."""
    return json.loads((FIXTURES_DIR / "review.json").read_text())


@pytest.fixture
def sample_review_comment():
    """Sample review comment data as returned by GitHub API."""
    return json.loads((FIXTURES_DIR / "review_comment.json").read_text())


@pytest.fixture
def sample_issue_comment():
    """Sample issue comment data as returned by GitHub API."""
    return json.loads((FIXTURES_DIR / "issue_comment.json").read_text())


@pytest.fixture
def mock_client():
    """Provide a mock GitHub client."""
    return MagicMock()
