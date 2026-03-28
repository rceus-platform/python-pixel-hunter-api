"""Bing search engine scraper implementation."""

import json
import logging
from typing import List, Optional, cast
from urllib.parse import quote_plus

import httpx
from selectolax.lexbor import LexborHTMLParser

from app.core.constants import GENERIC_IMAGE_URL_PATTERN, MAX_CANDIDATES_PER_ENGINE
from app.core.logging import log_event
from app.services.engines.base import fetch_html
from app.utilities.image_utils import get_node_attr
from app.utilities.url_utils import (
    extract_image_url_from_href,
    normalize_candidate_url,
)


async def scrape_bing_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> List[str]:
    """Scrape Bing Images page and extract image source URLs from tile metadata."""

    log_event(
        logging.INFO,
        f"Getting data from Bing (page={page})",
        event="scrape.bing.started",
    )
    first = page * 35 + 1
    search_url = (
        f"https://www.bing.com/images/search?q={quote_plus(query)}"
        f"&first={first}&count=35"
    )
    page_html = await fetch_html(client, search_url)

    tree = LexborHTMLParser(page_html)
    urls: List[str] = []
    seen: set[str] = set()

    def maybe_add_url(candidate: Optional[str]) -> None:
        normalized = normalize_candidate_url(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    for node in tree.css("a.iusc"):
        metadata = get_node_attr(node, "m")
        if not isinstance(metadata, str):
            continue

        try:
            payload_obj = cast(object, json.loads(metadata))
        except json.JSONDecodeError:
            continue

        if not isinstance(payload_obj, dict):
            continue

        payload_dict = cast(dict[str, object], payload_obj)
        val = payload_dict.get("murl") or payload_dict.get("turl")
        maybe_add_url(cast(Optional[str], val))

        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    # Fallback for Bing HTML variants that do not expose `a.iusc` metadata.
    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for img in tree.css("img"):
            attrs = ("src", "data-src", "data-src-hq", "data-src-large")
            for attr in attrs:
                maybe_add_url(get_node_attr(img, attr))
                if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                    break
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for anchor in tree.css("a[href]"):
            href = get_node_attr(anchor, "href")
            if not isinstance(href, str):
                continue
            maybe_add_url(extract_image_url_from_href(href))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        pattern = GENERIC_IMAGE_URL_PATTERN
        for match in cast(List[str], pattern.findall(page_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    log_event(
        logging.INFO,
        f"Bing returned {len(urls)} candidate URLs (page={page})",
        event="scrape.bing.completed",
        candidate_count=len(urls),
    )
    return urls
