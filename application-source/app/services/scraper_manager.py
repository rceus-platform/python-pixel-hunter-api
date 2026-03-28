"""Module for orchestrating search engine scrapers and evaluating image candidates."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Coroutine, List, Optional, Tuple, cast
from urllib.parse import quote_plus

import httpx
from fastapi import HTTPException

from app.core.constants import (
    ENGINE_PAGE_TIMEOUT_SECONDS,
    ENGINE_TOTAL_TIMEOUT_BUDGET_SECONDS,
    GENERIC_IMAGE_URL_PATTERN,
    GOOGLE_BARE_IMAGE_URL_PATTERN,
    MAX_CANDIDATES_PER_ENGINE,
    MAX_STREAM_PAGES,
    MIN_ENGINE_CANDIDATES_BEFORE_BROWSER_FALLBACK,
    REQUEST_HEADERS,
    STREAM_ENGINE_EMPTY_PAGE_STREAK_LIMIT,
    SUPPORTED_SEARCH_ENGINES,
)
from app.core.logging import log_event
from app.schemas.search import ImageCandidate, ImageSearchResult
from app.services.engines.baidu import scrape_baidu_image_urls
from app.services.engines.bing import scrape_bing_image_urls
from app.services.engines.duckduckgo import scrape_duckduckgo_image_urls
from app.services.engines.google import scrape_google_image_urls
from app.services.engines.selenium_ext import scrape_engine_urls_with_selenium
from app.services.engines.yandex import scrape_yandex_image_urls
from app.utilities.image_utils import (
    calculate_phash,
    derive_title_from_url,
    format_image_size,
    get_dimensions_from_stream,
    is_orientation_allowed,
    meets_dimension_thresholds,
)
from app.utilities.url_utils import (
    is_google_internal_url,
    normalize_candidate_url,
    query_hash,
    source_from_url,
)


# Re-including this here for now as it's a manager-level fallback
async def scrape_google_proxy_fallback_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> List[str]:
    """Use a text mirror as a last-resort fallback for blocked Google responses."""

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
    except httpx.HTTPError:
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

    for match in cast(List[str], GENERIC_IMAGE_URL_PATTERN.findall(body)):
        maybe_add_url(match)
        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for match in cast(List[str], GOOGLE_BARE_IMAGE_URL_PATTERN.findall(body)):
            maybe_add_url(match)
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    return urls


async def run_engine_task_with_timeout(
    engine_name: str,
    task_coro: Coroutine[object, object, List[str]],
    timeout_budget_seconds: Optional[float],
) -> Tuple[Optional[List[str]], Optional[BaseException], float]:
    """Run one engine scrape task with optional timeout ceiling and elapsed timing."""

    start = time.perf_counter()
    try:
        if timeout_budget_seconds is None:
            result = await task_coro
        else:
            timeout_seconds = max(
                1.0, min(timeout_budget_seconds, ENGINE_PAGE_TIMEOUT_SECONDS)
            )
            result = await asyncio.wait_for(task_coro, timeout=timeout_seconds)
        elapsed = time.perf_counter() - start
        return result, None, elapsed
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        return None, TimeoutError(f"{engine_name} scrape timed out"), elapsed
    except BaseException as exc:
        elapsed = time.perf_counter() - start
        return None, exc, elapsed


def _get_engine_coro(
    engine_name: str, query: str, client: httpx.AsyncClient, page: int
) -> Coroutine[object, object, List[str]]:
    """Helper to get the correct scraper coroutine for an engine."""

    if engine_name == "bing":
        return scrape_bing_image_urls(query, client, page)
    if engine_name == "google":
        return scrape_google_image_urls(query, client, page)
    if engine_name == "yandex":
        return scrape_yandex_image_urls(query, client, page)
    if engine_name == "duckduckgo":
        return scrape_duckduckgo_image_urls(query, client, page)
    if engine_name == "baidu":
        return scrape_baidu_image_urls(query, client, page)
    raise ValueError(f"Unknown engine: {engine_name}")


async def collect_engine_urls(
    query: str, engines: List[str], client: httpx.AsyncClient, pages: int = 1
) -> List[ImageCandidate]:
    """Execute selected search engine scrapers and return deduplicated URL candidates."""

    selected_engines = engines
    if "all" in selected_engines:
        selected_engines = list(SUPPORTED_SEARCH_ENGINES)

    log_event(
        logging.INFO,
        "collecting engine candidates",
        event="search.collect.started",
        engines=selected_engines,
        pages=pages,
        query_hash=query_hash(query),
    )

    unsupported = [e for e in selected_engines if e not in SUPPORTED_SEARCH_ENGINES]
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported engines: {', '.join(sorted(set(unsupported)))}. "
                f"Use one of: {', '.join(SUPPORTED_SEARCH_ENGINES)} or all."
            ),
        )

    page_count = max(1, pages)
    engine_timeout_budget_remaining = {
        e: ENGINE_TOTAL_TIMEOUT_BUDGET_SECONDS.get(e) for e in selected_engines
    }
    task_specs = []

    for page_idx in range(page_count):
        for engine_name in selected_engines:
            budget = engine_timeout_budget_remaining.get(engine_name)
            if isinstance(budget, float) and budget <= 0:
                log_event(
                    logging.INFO,
                    "skipping engine due to timeout budget exhaustion",
                    event="search.collect.engine_timeout_budget_exhausted",
                    engine=engine_name,
                    pages=page_idx,
                )
                continue

            coro = _get_engine_coro(engine_name, query, client, page_idx)
            task_specs.append(
                (
                    engine_name,
                    page_idx,
                    run_engine_task_with_timeout(engine_name, coro, budget),
                )
            )

    if not task_specs:
        raise HTTPException(status_code=400, detail="No supported engine selected.")

    outputs = await asyncio.gather(*[spec[2] for spec in task_specs])

    deduped: List[ImageCandidate] = []
    seen: set[str] = set()
    for (engine_name, _, _), (engine_urls, output_error, elapsed_seconds) in zip(
        task_specs, outputs
    ):
        budget = engine_timeout_budget_remaining.get(engine_name)
        if isinstance(budget, float):
            engine_timeout_budget_remaining[engine_name] = max(
                0.0, budget - elapsed_seconds
            )

        if output_error:
            log_event(
                logging.WARNING,
                "engine scrape failed",
                event="search.collect.engine_failed",
                engine=engine_name,
                error=str(output_error),
            )
            continue

        for url in engine_urls or []:
            if url not in seen:
                seen.add(url)
                deduped.append(
                    ImageCandidate(
                        url=url,
                        source=source_from_url(url),
                        search_engine=engine_name,
                    )
                )

    return deduped


async def iterate_engine_candidates(
    query: str,
    engines: List[str],
    client: httpx.AsyncClient,
    pages: int,
) -> AsyncIterator[List[ImageCandidate]]:
    """Collect candidates across pages; pages=0 means best-effort full crawl."""

    selected_engines = engines
    if "all" in selected_engines:
        selected_engines = list(SUPPORTED_SEARCH_ENGINES)

    unsupported = [e for e in selected_engines if e not in SUPPORTED_SEARCH_ENGINES]
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported engines: {', '.join(sorted(set(unsupported)))}. "
                f"Use one of: {', '.join(SUPPORTED_SEARCH_ENGINES)} or all."
            ),
        )

    page_limit = MAX_STREAM_PAGES if pages == 0 else max(1, pages)
    seen: set[str] = set()
    engine_totals = {engine: 0 for engine in selected_engines}
    engine_timeout_budget_remaining = {
        engine: ENGINE_TOTAL_TIMEOUT_BUDGET_SECONDS.get(engine)
        for engine in selected_engines
    }
    active_engines = set(selected_engines)
    engine_empty_streaks = {engine: 0 for engine in selected_engines}

    for page_idx in range(page_limit):
        page_candidates: List[ImageCandidate] = []
        tasks = []

        for engine_name in list(active_engines):
            budget = engine_timeout_budget_remaining.get(engine_name)
            if isinstance(budget, float) and budget <= 0:
                active_engines.remove(engine_name)
                log_event(
                    logging.INFO,
                    "disabling engine due to timeout budget exhaustion",
                    event="search.stream.engine_timeout_budget_exhausted",
                    engine=engine_name,
                    pages=page_idx,
                )
                continue

            coro = _get_engine_coro(engine_name, query, client, page_idx)
            tasks.append(
                (engine_name, run_engine_task_with_timeout(engine_name, coro, budget))
            )

        if not tasks:
            break

        outputs = await asyncio.gather(*[coroutine for _, coroutine in tasks])
        page_added = 0
        page_added_by_engine = {engine_name: 0 for engine_name, _ in tasks}

        for (engine_name, _), (engine_urls, output_error, elapsed_seconds) in zip(
            tasks, outputs
        ):
            budget = engine_timeout_budget_remaining.get(engine_name)
            if isinstance(budget, float):
                engine_timeout_budget_remaining[engine_name] = max(
                    0.0, budget - elapsed_seconds
                )

            if output_error is not None:
                continue

            output = engine_urls or []
            engine_totals[engine_name] += len(output)
            for url in output:
                if url in seen:
                    continue
                seen.add(url)
                page_candidates.append(
                    ImageCandidate(
                        url=url,
                        source=source_from_url(url),
                        search_engine=engine_name,
                    )
                )
                page_added += 1
                page_added_by_engine[engine_name] = (
                    page_added_by_engine.get(engine_name, 0) + 1
                )

        for engine_name in page_added_by_engine:
            if page_added_by_engine[engine_name] > 0:
                engine_empty_streaks[engine_name] = 0
                continue
            engine_empty_streaks[engine_name] = (
                engine_empty_streaks.get(engine_name, 0) + 1
            )
            if (
                engine_empty_streaks[engine_name]
                >= STREAM_ENGINE_EMPTY_PAGE_STREAK_LIMIT
                and engine_name in active_engines
            ):
                active_engines.remove(engine_name)
                log_event(
                    logging.INFO,
                    "disabling low-yield engine for remaining stream pages",
                    event="search.stream.engine_disabled",
                    engine=engine_name,
                    pages=page_idx,
                )

        if page_candidates:
            yield page_candidates
        if page_added == 0:
            break

    for engine_name, total_candidates in engine_totals.items():
        is_fallback_engine = engine_name in {"google", "bing", "duckduckgo", "baidu"}
        below_threshold = (
            total_candidates < MIN_ENGINE_CANDIDATES_BEFORE_BROWSER_FALLBACK
        )
        if is_fallback_engine and below_threshold:
            selenium_urls = await asyncio.to_thread(
                scrape_engine_urls_with_selenium, query, engine_name
            )
            fallback_candidates: List[ImageCandidate] = []
            for url in selenium_urls:
                if url in seen:
                    continue
                seen.add(url)
                fallback_candidates.append(
                    ImageCandidate(
                        url=url,
                        source=source_from_url(url),
                        search_engine=engine_name,
                    )
                )
            if fallback_candidates:
                yield fallback_candidates


async def evaluate_image_candidate_with_reason(
    client: httpx.AsyncClient,
    candidate: ImageCandidate,
    min_width: int,
    min_height: int,
    orientation: str,
    dedupe: bool,
) -> Tuple[Optional[ImageSearchResult], Optional[str]]:
    """Evaluate one candidate image: HEAD check, dimension extraction, filters, and hash."""

    image_url = candidate.url
    dimensions = await get_dimensions_from_stream(client, image_url)
    if not dimensions:
        return None, "dimension_probe_failed"

    width, height, image_bytes, content_length = dimensions
    if not meets_dimension_thresholds(
        width, height, min_width, min_height, orientation
    ):
        return None, "below_minimum_dimensions"

    if not is_orientation_allowed(width, height, orientation):
        return None, "orientation_filtered"

    real_size = content_length if content_length > 0 else len(image_bytes)
    result = ImageSearchResult(
        url=image_url,
        width=width,
        height=height,
        size=format_image_size(real_size),
        cover=image_url,
        title=derive_title_from_url(image_url),
        source=candidate.source,
        search_engine=candidate.search_engine,
        phash=calculate_phash(image_bytes) if dedupe else None,
        note="",
        description="",
        highlights=[],
        tags=[],
    )

    return result, None


async def evaluate_image_candidate(
    client: httpx.AsyncClient,
    candidate: ImageCandidate,
    min_width: int,
    min_height: int,
    orientation: str,
    dedupe: bool,
) -> Optional[ImageSearchResult]:
    """Backward-compatible wrapper for candidate evaluation."""

    result, _ = await evaluate_image_candidate_with_reason(
        client=client,
        candidate=candidate,
        min_width=min_width,
        min_height=min_height,
        orientation=orientation,
        dedupe=dedupe,
    )
    return result
