"""Yandex search and reverse image search engine scraper implementation."""

import logging
from typing import List, Optional, cast
from urllib.parse import quote_plus

import httpx
from selectolax.lexbor import LexborHTMLParser

from app.core.constants import (
    GENERIC_IMAGE_URL_PATTERN,
    MAX_CANDIDATES_PER_ENGINE,
    YANDEX_IMG_HREF_PATTERN,
    YANDEX_ORIGIN_URL_PATTERN,
)
from app.core.logging import log_event
from app.services.engines.base import fetch_html
from app.utilities.image_utils import get_node_attr
from app.utilities.url_utils import normalize_candidate_url


async def scrape_yandex_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> List[str]:
    """Scrape Yandex image search result page for image URLs."""

    log_event(
        logging.INFO,
        f"Getting data from Yandex (page={page})",
        event="scrape.yandex.started",
    )
    search_url = f"https://yandex.com/images/search?text={quote_plus(query)}&p={page}"
    page_html = await fetch_html(client, search_url)
    tree = LexborHTMLParser(page_html)

    urls: List[str] = []
    seen: set[str] = set()

    def maybe_add_url(candidate: Optional[str]) -> None:
        normalized = normalize_candidate_url(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    for match in cast(List[str], YANDEX_IMG_HREF_PATTERN.findall(page_html)):
        maybe_add_url(match.replace("\\/", "/"))
        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for match in cast(List[str], YANDEX_ORIGIN_URL_PATTERN.findall(page_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for img in tree.css("img"):
            attrs = ("src", "data-src", "data-image", "data-src-large")
            for attr in attrs:
                maybe_add_url(get_node_attr(img, attr))
                if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                    break
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for match in cast(List[str], GENERIC_IMAGE_URL_PATTERN.findall(page_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    log_event(
        logging.INFO,
        f"Yandex returned {len(urls)} candidate URLs (page={page})",
        event="scrape.yandex.completed",
        candidate_count=len(urls),
    )
    return urls


async def scrape_yandex_reverse_image_urls(
    image_url: str, client: httpx.AsyncClient
) -> List[str]:
    """Scrape Yandex reverse image search result page and extract similar-image URLs."""

    url = f"https://yandex.com/images/search?rpt=imageview&url={quote_plus(image_url)}"
    page_html = await fetch_html(client, url)

    urls: List[str] = []
    seen: set[str] = set()

    # Yandex often places source links in JSON payload values under "img_href".
    for match in cast(List[str], YANDEX_IMG_HREF_PATTERN.findall(page_html)):
        candidate = normalize_candidate_url(match.replace("\\/", "/"))
        if candidate and candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    return urls
