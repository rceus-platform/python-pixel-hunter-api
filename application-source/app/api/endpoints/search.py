"""API endpoints for searching images across multiple engines."""

import asyncio
import json
import logging
from typing import Annotated, AsyncIterator, List, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.constants import (
    CONCURRENT_IMAGE_TASKS,
    DEFAULT_MIN_HEIGHT,
    DEFAULT_MIN_WIDTH,
    FOUR_K_HEIGHT,
    FOUR_K_WIDTH,
    MAX_STREAM_PAGES,
    SEARCH_TIMEOUT_SECONDS,
    STREAM_RELAXED_MIN_DIMENSION_FLOOR,
    STREAM_SOFT_FALLBACK_THRESHOLD,
)
from app.core.logging import log_event
from app.schemas.search import ImageCandidate, ImageSearchResult
from app.services.image_service import build_unverified_result
from app.services.scraper_manager import (
    collect_engine_urls,
    evaluate_image_candidate,
    evaluate_image_candidate_with_reason,
    iterate_engine_candidates,
)
from app.utilities.url_utils import query_hash

router = APIRouter()

# Global event to signal streaming endpoints to stop early
stream_stop_event = asyncio.Event()


@router.post("/stream/stop")
def stop_search_stream() -> dict[str, str]:
    """Signal any active search stream endpoints to stop and exit early."""

    stream_stop_event.set()
    log_event(
        logging.INFO,
        "search stream stop requested via API",
        event="search.stream.stop_endpoint_called",
    )
    return {"status": "stopping active streams"}


@router.get("")
async def search_images(
    query: Annotated[str, Query(min_length=1, description="Search keyword")],
    engines: Annotated[
        str,
        Query(
            description="Comma-separated engines: bing,google,yandex,duckduckgo,baidu,all"
        ),
    ] = "all",
    min_width: Annotated[int, Query(ge=1)] = DEFAULT_MIN_WIDTH,
    min_height: Annotated[int, Query(ge=1)] = DEFAULT_MIN_HEIGHT,
    four_k_only: Annotated[
        bool, Query(description="If true, enforce minimum 3840x2160")
    ] = False,
    orientation: Annotated[str, Query(pattern="^(any|portrait|landscape)$")] = "any",
    remove_duplicates: Annotated[
        bool, Query(description="Remove near-duplicates via perceptual hash")
    ] = True,
    allow_unverified: Annotated[
        bool,
        Query(
            description=(
                "Include unverified candidates when strict filtering yields few results"
            )
        ),
    ] = False,
    pages: Annotated[
        int, Query(ge=0, le=5, description="How many pages to scrape per engine")
    ] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, List[ImageSearchResult]]:
    """Search across engines, filter HD images, sort by resolution, and return top results."""

    cleaned_query = query.strip()
    if not cleaned_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    selected_engines = [
        part.strip().lower() for part in engines.split(",") if part.strip()
    ]
    if not selected_engines:
        selected_engines = ["bing"]

    if four_k_only:
        min_width = max(min_width, FOUR_K_WIDTH)
        min_height = max(min_height, FOUR_K_HEIGHT)

    log_event(
        logging.INFO,
        "search request started",
        event="search.request.started",
        engines=selected_engines,
        query_hash=query_hash(cleaned_query),
    )

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=SEARCH_TIMEOUT_SECONDS
    ) as client:
        try:
            candidates = await collect_engine_urls(
                cleaned_query, selected_engines, client, pages=pages
            )
        except HTTPException as exc:
            if exc.status_code == 502:
                candidates = []
            else:
                raise

        semaphore = asyncio.Semaphore(CONCURRENT_IMAGE_TASKS)

        async def guarded(candidate: ImageCandidate) -> ImageSearchResult | None:
            try:
                async with semaphore:
                    return await evaluate_image_candidate(
                        client=client,
                        candidate=candidate,
                        min_width=min_width,
                        min_height=min_height,
                        orientation=orientation,
                        dedupe=remove_duplicates,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event(
                    logging.DEBUG,
                    "candidate evaluation failed",
                    event="search.candidate.eval_failed",
                    url=candidate.url,
                    error=str(exc),
                )
                return None

        evaluated = await asyncio.gather(
            *[guarded(candidate) for candidate in candidates], return_exceptions=False
        )

    verified_results = [item for item in evaluated if item]
    results: List[ImageSearchResult] = verified_results

    if allow_unverified:
        verified_urls = {item.url for item in verified_results}
        for candidate in candidates:
            if candidate.url not in verified_urls:
                results.append(build_unverified_result(candidate))
            if len(results) >= limit:
                break

    if not results and candidates:
        results = [build_unverified_result(c) for c in candidates[:limit]]

    if remove_duplicates:
        unique_results = []
        seen_hashes = set()
        for item in results:
            if item.phash:
                if item.phash in seen_hashes:
                    continue
                seen_hashes.add(item.phash)
            unique_results.append(item)
        results = unique_results

    results.sort(key=lambda item: item.width * item.height, reverse=True)

    # Hide phash from output
    final_results = results[:limit]
    for res in final_results:
        res.phash = None

    return {"results": final_results}


@router.get("/stream")
async def search_images_stream(
    query: Annotated[str, Query(min_length=1, description="Actress name or keyword")],
    engines: Annotated[
        str,
        Query(
            description="Comma-separated engines: bing,google,yandex,duckduckgo,baidu,all"
        ),
    ] = "all",
    min_width: Annotated[int, Query(ge=1)] = DEFAULT_MIN_WIDTH,
    min_height: Annotated[int, Query(ge=1)] = DEFAULT_MIN_HEIGHT,
    four_k_only: Annotated[
        bool, Query(description="If true, enforce minimum 3840x2160")
    ] = False,
    orientation: Annotated[str, Query(pattern="^(any|portrait|landscape)$")] = "any",
    remove_duplicates: Annotated[
        bool, Query(description="Remove near-duplicates via perceptual hash")
    ] = True,
    allow_unverified: Annotated[
        bool,
        Query(
            description=(
                "Include unverified candidates when strict filtering yields few results"
            )
        ),
    ] = False,
    pages: Annotated[int, Query(ge=0, le=MAX_STREAM_PAGES)] = 0,
    limit: Annotated[int, Query(ge=0, le=20000)] = 20,
) -> StreamingResponse:
    """Stream search results as NDJSON for real-time UI updates."""

    cleaned_query = query.strip()
    selected_engines = [
        part.strip().lower() for part in engines.split(",") if part.strip()
    ]
    if not selected_engines:
        selected_engines = ["all"]
    if four_k_only:
        min_width = max(min_width, FOUR_K_WIDTH)
        min_height = max(min_height, FOUR_K_HEIGHT)

    async def stream_results() -> AsyncIterator[bytes]:
        emitted = 0
        seen_hashes = set()
        emitted_urls = set()
        stream_stop_event.clear()
        strict_failed_candidates = []

        async with httpx.AsyncClient(
            follow_redirects=True, timeout=SEARCH_TIMEOUT_SECONDS
        ) as client:
            semaphore = asyncio.Semaphore(CONCURRENT_IMAGE_TASKS)

            async def guarded(
                candidate: ImageCandidate, relaxed: bool = False
            ) -> Tuple[Optional[ImageSearchResult], Optional[str]]:
                try:
                    async with semaphore:
                        eff_w = (
                            min(min_width, STREAM_RELAXED_MIN_DIMENSION_FLOOR)
                            if relaxed
                            else min_width
                        )
                        eff_h = (
                            min(min_height, STREAM_RELAXED_MIN_DIMENSION_FLOOR)
                            if relaxed
                            else min_height
                        )
                        return await evaluate_image_candidate_with_reason(
                            client, candidate, eff_w, eff_h, orientation, remove_duplicates
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log_event(
                        logging.DEBUG,
                        "candidate evaluation failed",
                        event="search.candidate.eval_failed",
                        url=candidate.url,
                        error=str(exc),
                    )
                    return None, "eval_error"

            pending = {}
            async for batch in iterate_engine_candidates(
                cleaned_query, selected_engines, client, pages
            ):
                if stream_stop_event.is_set():
                    break
                for candidate in batch:
                    task = asyncio.create_task(guarded(candidate))
                    pending[task] = candidate

                while pending:
                    if stream_stop_event.is_set():
                        for t in pending:
                            t.cancel()
                        return
                    done, _ = await asyncio.wait(
                        set(pending.keys()), return_when=asyncio.FIRST_COMPLETED
                    )
                    for finished in done:
                        candidate = pending.pop(finished)
                        result, _ = finished.result()
                        emit_item = result
                        if result is None and allow_unverified:
                            emit_item = build_unverified_result(candidate)
                        elif result is None:
                            strict_failed_candidates.append(candidate)

                        if emit_item and emit_item.url not in emitted_urls:
                            if remove_duplicates and emit_item.phash:
                                if emit_item.phash in seen_hashes:
                                    continue
                                seen_hashes.add(emit_item.phash)

                            emit_item.phash = None
                            yield (
                                json.dumps(emit_item.model_dump(), ensure_ascii=True)
                                + "\n"
                            ).encode("utf-8")
                            emitted += 1
                            emitted_urls.add(emit_item.url)
                            if limit > 0 and emitted >= limit:
                                for t in pending:
                                    t.cancel()
                                return

            if (
                not allow_unverified
                and emitted < STREAM_SOFT_FALLBACK_THRESHOLD
                and strict_failed_candidates
            ):
                relaxed_pending = {}
                for c in strict_failed_candidates:
                    relaxed_pending[asyncio.create_task(guarded(c, relaxed=True))] = c

                while relaxed_pending:
                    if stream_stop_event.is_set():
                        for t in relaxed_pending:
                            t.cancel()
                        return
                    done, _ = await asyncio.wait(
                        set(relaxed_pending.keys()), return_when=asyncio.FIRST_COMPLETED
                    )
                    for finished in done:
                        candidate = relaxed_pending.pop(finished)
                        result, _ = finished.result()
                        if result and result.url not in emitted_urls:
                            if remove_duplicates and result.phash:
                                if result.phash in seen_hashes:
                                    continue
                                seen_hashes.add(result.phash)
                            result.phash = None
                            yield (
                                json.dumps(result.model_dump(), ensure_ascii=True)
                                + "\n"
                            ).encode("utf-8")
                            emitted += 1
                            emitted_urls.add(result.url)
                            if limit > 0 and emitted >= limit:
                                for t in relaxed_pending:
                                    t.cancel()
                                return

            if (
                not allow_unverified
                and emitted < STREAM_SOFT_FALLBACK_THRESHOLD
                and strict_failed_candidates
            ):
                for c in strict_failed_candidates:
                    if emitted >= limit:
                        break
                    fallback = build_unverified_result(c)
                    if fallback.url not in emitted_urls:
                        yield (
                            json.dumps(fallback.model_dump(), ensure_ascii=True) + "\n"
                        ).encode("utf-8")
                        emitted += 1
                        emitted_urls.add(fallback.url)

    return StreamingResponse(stream_results(), media_type="application/x-ndjson")
