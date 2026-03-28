"""Baidu search engine scraper implementation."""

import asyncio
import json
import logging
from typing import List, Optional, cast

import httpx

from app.core.constants import (
    MAX_CANDIDATES_PER_ENGINE,
    MIN_ENGINE_CANDIDATES_BEFORE_BROWSER_FALLBACK,
    REQUEST_HEADERS,
)
from app.core.logging import log_event
from app.services.engines.selenium_ext import scrape_engine_urls_with_selenium
from app.utilities.url_utils import normalize_candidate_url


async def scrape_baidu_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> List[str]:
    """Scrape Baidu image JSON endpoint for candidate image URLs."""

    log_event(
        logging.INFO,
        f"Getting data from Baidu (page={page})",
        event="scrape.baidu.started",
    )
    params = {
        "tn": "resultjson_com",
        "ipn": "rj",
        "ct": "201326592",
        "lm": "-1",
        "word": query,
        "queryWord": query,
        "pn": str(page * 30),
        "rn": "30",
        "ie": "utf-8",
        "oe": "utf-8",
    }
    headers = {
        **REQUEST_HEADERS,
        "Referer": "https://image.baidu.com/",
        "Accept": "application/json,text/javascript,*/*;q=0.1",
    }

    try:
        response = await client.get(
            "https://image.baidu.com/search/acjson", params=params, headers=headers
        )
        response.raise_for_status()
        body = response.text.strip()
        start = body.find("{")
        end = body.rfind("}")
        if start == -1 or end == -1:
            return []
        payload = cast(dict[str, object], json.loads(body[start : end + 1]))
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        log_event(logging.WARNING, "Baidu request failed", event="scrape.baidu.failed")
        return []

    urls: List[str] = []
    seen: set[str] = set()
    data_obj = payload.get("data")
    if isinstance(data_obj, list):
        for item in cast(List[object], data_obj):
            if not isinstance(item, dict):
                continue
            item_dict = cast(dict[str, object], item)
            for key in ("objURL", "hoverURL", "middleURL", "thumbURL"):
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
            scrape_engine_urls_with_selenium, query, "baidu"
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
        f"Baidu returned {len(urls)} candidate URLs (page={page})",
        event="scrape.baidu.completed",
        candidate_count=len(urls),
    )
    return urls
