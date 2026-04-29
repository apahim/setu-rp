"""Dataclasses representing database rows."""

from dataclasses import dataclass


@dataclass
class SyncMetadata:
    entity_type: str
    last_sync: str | None = None
    last_page: int = 0
    total_items: int = 0
    updated_at: str | None = None


@dataclass
class Repository:
    id: int
    owner: str
    name: str
    full_name: str
    bot_adoption_date: str | None = None
    fetched_at: str = ""


@dataclass
class User:
    id: int
    login: str
    type: str = "User"
    name: str | None = None
    fetched_at: str = ""


@dataclass
class PullRequest:
    id: int
    number: int
    title: str | None = None
    state: str = "open"
    author_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    merged_at: str | None = None
    merge_commit_sha: str | None = None
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    body: str | None = None
    fetched_at: str = ""


@dataclass
class Label:
    id: int
    name: str
    description: str | None = None
    fetched_at: str = ""


@dataclass
class Review:
    id: int
    pull_request_id: int
    reviewer_id: int | None = None
    state: str | None = None
    body: str | None = None
    submitted_at: str | None = None
    fetched_at: str = ""


@dataclass
class ReviewComment:
    id: int
    pull_request_id: int
    review_id: int | None = None
    author_id: int | None = None
    body: str | None = None
    path: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    fetched_at: str = ""


@dataclass
class IssueComment:
    id: int
    pull_request_id: int
    author_id: int | None = None
    body: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    fetched_at: str = ""
