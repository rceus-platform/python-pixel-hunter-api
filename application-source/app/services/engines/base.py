"""Base utilities and network fetchers for search engine scrapers."""

import asyncio
import logging
from typing import Dict, Optional, Protocol
from urllib.parse import urlparse

import cloudscraper
import httpx
from fastapi import HTTPException
from requests import RequestException, Response

from app.core.constants import (
    BLOCKED_FETCH_LOG_REPEAT_EVERY,
    REQUEST_HEADERS,
    SEARCH_TIMEOUT_SECONDS,
)
from app.core.logging import log_event, log_event_throttled


class ScraperClient(Protocol):
    """Protocol defining the interface for a search engine HTTP client."""

    def get(self, url: str, headers: Dict[str, str], timeout: int) -> Response:
        """Execute a synchronous GET request."""


def fetch_html_with_cloudscraper(
    url: str, headers: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """Fetch HTML with cloudscraper for anti-bot-protected pages."""

    try:
        scraper = cloudscraper.create_scraper()
        merged_headers = headers or REQUEST_HEADERS
        response = scraper.get(
            url, headers=merged_headers, timeout=SEARCH_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.text
    except RequestException as exc:
        host = urlparse(url).netloc.lower()
        log_event_throttled(
            f"cloudscraper_failed:{host}",
            "cloudscraper HTML fetch failed",
            event="scrape.cloudscraper.fetch_failed",
            url_host=host,
            error=str(exc),
            repeat_every=BLOCKED_FETCH_LOG_REPEAT_EVERY,
        )
        return None


async def fetch_html(
    client: httpx.AsyncClient, url: str, headers: Optional[Dict[str, str]] = None
) -> str:
    """Fetch an HTML page and return text, raising HTTPException on failures."""

    merged_headers = headers or REQUEST_HEADERS
    try:
        response = await client.get(url, headers=merged_headers)
        response.raise_for_status()
        return response.text
    except httpx.HTTPStatusError as exc:
        # Some engines challenge default clients. Try cloudscraper fallback on blocked statuses.
        if exc.response.status_code in {403, 429, 503}:
            host = urlparse(url).netloc.lower()
            log_event_throttled(
                f"primary_blocked:{host}",
                "primary HTML fetch blocked, trying cloudscraper fallback",
                event="scrape.fetch.blocked",
                status_code=exc.response.status_code,
                url_host=host,
                repeat_every=BLOCKED_FETCH_LOG_REPEAT_EVERY,
            )
            fallback_html = await asyncio.to_thread(
                fetch_html_with_cloudscraper, url, merged_headers
            )
            if fallback_html:
                log_event(
                    logging.INFO,
                    "cloudscraper HTML fallback succeeded",
                    event="scrape.fetch.cloudscraper_succeeded",
                    url_host=urlparse(url).netloc.lower(),
                )
                return fallback_html
        raise HTTPException(
            status_code=502, detail=f"Search engine fetch failed: {exc}"
        ) from exc
    except httpx.HTTPError as exc:
        host = urlparse(url).netloc.lower()
        log_event_throttled(
            f"primary_failed:{host}",
            "primary HTML fetch failed, trying cloudscraper fallback",
            event="scrape.fetch.failed",
            url_host=host,
            error=str(exc),
            repeat_every=BLOCKED_FETCH_LOG_REPEAT_EVERY,
        )
        fallback_html = await asyncio.to_thread(
            fetch_html_with_cloudscraper, url, merged_headers
        )
        if fallback_html:
            log_event(
                logging.INFO,
                "cloudscraper HTML fallback succeeded",
                event="scrape.fetch.cloudscraper_succeeded",
                url_host=urlparse(url).netloc.lower(),
            )
            return fallback_html
        raise HTTPException(
            status_code=502, detail=f"Search engine fetch failed: {exc}"
        ) from exc
