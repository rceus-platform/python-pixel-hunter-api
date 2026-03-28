"""DuckDuckGo search engine scraper implementation."""

import asyncio
import logging
from typing import List, Optional, cast
from urllib.parse import quote_plus

import httpx
from fastapi import HTTPException

from app.core.constants import (
    DUCKDUCKGO_VQD_PATTERN,
    MAX_CANDIDATES_PER_ENGINE,
    MIN_ENGINE_CANDIDATES_BEFORE_BROWSER_FALLBACK,
    REQUEST_HEADERS,
)
from app.core.logging import log_event
from app.services.engines.base import fetch_html
from app.services.engines.selenium_ext import scrape_engine_urls_with_selenium
from app.utilities.url_utils import normalize_candidate_url


async def scrape_duckduckgo_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> List[str]:
    """Scrape DuckDuckGo image results via vqd token + i.js endpoint."""

    log_event(
        logging.INFO,
        f"Getting data from DuckDuckGo (page={page})",
        event="scrape.duckduckgo.started",
    )

    bootstrap_url = (
        f"https://duckduckgo.com/?q={quote_plus(query)}&iax=images&ia=images"
    )
    try:
        bootstrap_html = await fetch_html(client, bootstrap_url)
    except HTTPException:
        bootstrap_html = ""

    token_match = DUCKDUCKGO_VQD_PATTERN.search(bootstrap_html)
    vqd = token_match.group(1) if token_match else ""
    if not vqd:
        if page > 0:
            return []
        fallback_urls = await asyncio.to_thread(
            scrape_engine_urls_with_selenium, query, "duckduckgo"
        )
        log_event(
            logging.INFO,
            f"DuckDuckGo returned {len(fallback_urls)} candidate URLs (page={page})",
            event="scrape.duckduckgo.completed",
            candidate_count=len(fallback_urls),
        )
        return fallback_urls[:MAX_CANDIDATES_PER_ENGINE]

    params = {
        "q": query,
        "o": "json",
        "p": "1",
        "vqd": vqd,
        "f": ",,,",
        "l": "wt-wt",
        "s": str(page * 100),
    }
    headers = {**REQUEST_HEADERS, "Referer": "https://duckduckgo.com/"}
    try:
        response = await client.get(
            "https://duckduckgo.com/i.js", params=params, headers=headers
        )
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
    except (httpx.HTTPError, ValueError):
        log_event(
            logging.WARNING,
            "DuckDuckGo request failed",
            event="scrape.duckduckgo.failed",
        )
        return []

    urls: List[str] = []
    seen: set[str] = set()
    results_obj = payload.get("results")
    if isinstance(results_obj, list):
        for item in cast(List[object], results_obj):
            if not isinstance(item, dict):
                continue
            item_dict = cast(dict[str, object], item)
            for key in ("image", "thumbnail", "url"):
                val = cast(Optional[str], item_dict.get(key))
                candidate = normalize_candidate_url(val)
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    urls.append(candidate)
                if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                    break
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if page == 0 and len(urls) < MIN_ENGINE_CANDIDATES_BEFORE_BROWSER_FALLBACK:
        fallback_urls = await asyncio.to_thread(
            scrape_engine_urls_with_selenium, query, "duckduckgo"
        )
        for cand in fallback_urls:
            norm = normalize_candidate_url(cand)
            if norm and norm not in seen:
                seen.add(norm)
                urls.append(norm)
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    log_event(
        logging.INFO,
        f"DuckDuckGo returned {len(urls)} candidate URLs (page={page})",
        event="scrape.duckduckgo.completed",
        candidate_count=len(urls),
    )
    return urls
