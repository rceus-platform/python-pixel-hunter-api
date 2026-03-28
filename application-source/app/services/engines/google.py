"""Google search engine scraper implementation using Selenium fallback."""

import asyncio
import logging
from typing import List

import httpx

from app.core.logging import log_event
from app.services.engines.selenium_ext import scrape_engine_urls_with_selenium


async def scrape_google_image_urls(
    query: str, _client: httpx.AsyncClient, page: int = 0
) -> List[str]:
    """Scrape Google Images using Selenium for reliable rendering."""

    log_event(
        logging.INFO,
        f"Getting data from Google using Selenium (page={page})",
        event="scrape.google.selenium_started",
    )
    try:
        urls = await asyncio.to_thread(
            scrape_engine_urls_with_selenium, query, "google"
        )
        log_event(
            logging.INFO,
            f"Google returned {len(urls)} candidate URLs (page={page})",
            event="scrape.google.completed",
            candidate_count=len(urls),
        )
        return urls
    except Exception as exc:
        log_event(
            logging.WARNING,
            "Google selenium scrape failed",
            event="scrape.google.failed",
            error=str(exc),
        )
        return []
