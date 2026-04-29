"""Tests for the GitHub REST client."""

from unittest.mock import MagicMock, patch

import pytest

from setu_rp.collection.client import GitHubClient


@pytest.fixture
def client():
    return GitHubClient(token="test-token", per_page=2, max_retries=1, rate_limit_buffer=10)


def _mock_response(data, status_code=200, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.headers = headers or {
        "X-RateLimit-Remaining": "4999",
        "X-RateLimit-Reset": "9999999999",
    }
    resp.raise_for_status = MagicMock()
    return resp


def test_get_simple(client):
    with patch.object(client.session, "request") as mock_req:
        mock_req.return_value = _mock_response({"id": 1})
        resp = client.get("/repos/owner/repo")
        assert resp.json() == {"id": 1}
        mock_req.assert_called_once()


def test_get_paginated_single_page(client):
    with patch.object(client.session, "request") as mock_req:
        mock_req.return_value = _mock_response(
            [{"id": 1}, {"id": 2}],
            headers={"X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": "9999999999"},
        )
        items = list(client.get_paginated("/repos/owner/repo/pulls"))
        assert len(items) == 2
        assert items[0]["id"] == 1


def test_get_paginated_multiple_pages(client):
    page1_resp = _mock_response(
        [{"id": 1}, {"id": 2}],
        headers={
            "X-RateLimit-Remaining": "4999",
            "X-RateLimit-Reset": "9999999999",
            "Link": '<https://api.github.com/repos/owner/repo/pulls?page=2>; rel="next"',
        },
    )
    page2_resp = _mock_response(
        [{"id": 3}],
        headers={"X-RateLimit-Remaining": "4998", "X-RateLimit-Reset": "9999999999"},
    )

    with patch.object(client.session, "request", side_effect=[page1_resp, page2_resp]):
        items = list(client.get_paginated("/repos/owner/repo/pulls"))
        assert len(items) == 3


def test_retry_on_server_error(client):
    error_resp = _mock_response([], status_code=500)
    error_resp.raise_for_status.side_effect = Exception("Server Error")
    ok_resp = _mock_response([{"id": 1}])

    with patch.object(client.session, "request", side_effect=[error_resp, ok_resp]):
        with patch("setu_rp.collection.client.time.sleep"):
            resp = client.get("/repos/owner/repo")
            assert resp.json() == [{"id": 1}]


def test_get_next_url_parsing():
    link = '<https://api.github.com/repos/o/r/pulls?page=3>; rel="next", <https://api.github.com/repos/o/r/pulls?page=1>; rel="prev"'
    resp = MagicMock()
    resp.headers = {"Link": link}
    assert GitHubClient._get_next_url(resp) == "https://api.github.com/repos/o/r/pulls?page=3"


def test_get_next_url_no_next():
    resp = MagicMock()
    resp.headers = {"Link": '<https://api.github.com/repos/o/r/pulls?page=1>; rel="prev"'}
    assert GitHubClient._get_next_url(resp) is None


def test_get_next_url_no_header():
    resp = MagicMock()
    resp.headers = {}
    assert GitHubClient._get_next_url(resp) is None
