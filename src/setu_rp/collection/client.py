"""GitHub REST API client with rate limiting, pagination, and retry."""

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str, per_page: int = 100, max_retries: int = 3,
                 rate_limit_buffer: int = 100):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        })
        self.per_page = per_page
        self.max_retries = max_retries
        self.rate_limit_buffer = rate_limit_buffer
        self._request_count = 0

    def _check_rate_limit(self, response: requests.Response):
        """Check rate limit headers and sleep if approaching limit."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset_time = response.headers.get("X-RateLimit-Reset")
        if remaining is not None and reset_time is not None:
            remaining = int(remaining)
            reset_time = int(reset_time)
            if remaining < self.rate_limit_buffer:
                sleep_seconds = max(reset_time - int(time.time()), 1) + 1
                logger.warning(
                    "Rate limit low (%d remaining). Sleeping %d seconds until reset.",
                    remaining, sleep_seconds,
                )
                time.sleep(sleep_seconds)

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make an HTTP request with exponential backoff retry."""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(method, url, **kwargs)
                self._request_count += 1

                if self._request_count % 50 == 0:
                    remaining = response.headers.get("X-RateLimit-Remaining", "?")
                    logger.info(
                        "Request #%d — Rate limit remaining: %s",
                        self._request_count, remaining,
                    )

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.max_retries:
                        wait = 2 ** attempt
                        logger.warning(
                            "HTTP %d on %s. Retry %d/%d in %ds.",
                            response.status_code, url, attempt + 1,
                            self.max_retries, wait,
                        )
                        time.sleep(wait)
                        continue
                    response.raise_for_status()

                self._check_rate_limit(response)
                response.raise_for_status()
                return response

            except requests.exceptions.ConnectionError:
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "Connection error on %s. Retry %d/%d in %ds.",
                        url, attempt + 1, self.max_retries, wait,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError(f"Max retries exceeded for {url}")

    def get(self, path: str, params: dict | None = None) -> requests.Response:
        """Make a GET request to the GitHub API."""
        url = f"{self.BASE_URL}{path}" if path.startswith("/") else path
        return self._request_with_retry("GET", url, params=params)

    def get_paginated(self, path: str, params: dict | None = None,
                      start_page: int = 1):
        """Generator that yields items from all pages of a paginated endpoint."""
        params = dict(params or {})
        params.setdefault("per_page", self.per_page)
        params["page"] = start_page

        url = f"{self.BASE_URL}{path}" if path.startswith("/") else path

        while url:
            response = self._request_with_retry("GET", url, params=params)
            items = response.json()
            if not items:
                break

            yield from items

            # Follow Link header for next page
            url = self._get_next_url(response)
            params = {}  # params are embedded in the Link URL

    def get_paginated_with_pages(self, path: str, params: dict | None = None,
                                  start_page: int = 1):
        """Generator yielding (page_number, items) tuples for page tracking."""
        params = dict(params or {})
        params.setdefault("per_page", self.per_page)
        params["page"] = start_page
        current_page = start_page

        url = f"{self.BASE_URL}{path}" if path.startswith("/") else path

        while url:
            response = self._request_with_retry("GET", url, params=params)
            items = response.json()
            if not items:
                break

            yield current_page, items
            current_page += 1

            url = self._get_next_url(response)
            params = {}

    @staticmethod
    def _get_next_url(response: requests.Response) -> str | None:
        """Extract the next page URL from the Link header."""
        link_header = response.headers.get("Link", "")
        if not link_header:
            return None
        match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
        return match.group(1) if match else None
