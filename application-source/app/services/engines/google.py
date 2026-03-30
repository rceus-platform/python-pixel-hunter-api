"""Google search engine scraper implementation using Jina Reader proxy to avoid Selenium."""

import logging
from typing import List, Optional, cast
from urllib.parse import quote_plus

import httpx

from app.core.constants import (
    GENERIC_IMAGE_URL_PATTERN,
    GOOGLE_BARE_IMAGE_URL_PATTERN,
    MAX_CANDIDATES_PER_ENGINE,
    REQUEST_HEADERS,
)
from app.core.logging import log_event
from app.utilities.url_utils import is_google_internal_url, normalize_candidate_url


async def scrape_google_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> List[str]:
    """Scrape Google Images using a text-based proxy mirror to avoid memory-heavy Selenium."""

    log_event(
        logging.INFO,
        f"Getting data from Google via Jina proxy (page={page})",
        event="scrape.google.started",
    )

    start = page * 20
    proxy_target = (
        "https://www.google.com/search?tbm=isch&gbv=1"
        f"&hl=en&gl=us&pws=0&q={quote_plus(query)}&start={start}"
    )
    proxy_url = f"https://r.jina.ai/http://{proxy_target.removeprefix('https://')}"

    try:
        response = await client.get(proxy_url, headers=REQUEST_HEADERS)
        response.raise_for_status()
        body = response.text
    except httpx.HTTPError as exc:
        log_event(
            logging.WARNING,
            "Google proxy scrape failed",
            event="scrape.google.failed",
            error=str(exc),
        )
        return []

    urls: List[str] = []
    seen: set[str] = set()

    def maybe_add_url(candidate: Optional[str]) -> None:
        normalized = normalize_candidate_url(candidate)
        if not normalized:
            return
        if is_google_internal_url(normalized):
            return
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    # First pass: try generic patterns
    for match in cast(List[str], GENERIC_IMAGE_URL_PATTERN.findall(body)):
        maybe_add_url(match)
        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    # Second pass: try bare patterns if needed
    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for match in cast(List[str], GOOGLE_BARE_IMAGE_URL_PATTERN.findall(body)):
            maybe_add_url(match)
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    log_event(
        logging.INFO,
        f"Google (proxy) returned {len(urls)} candidate URLs (page={page})",
        event="scrape.google.completed",
        candidate_count=len(urls),
    )
    return urls
