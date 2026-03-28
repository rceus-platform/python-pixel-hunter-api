"""API endpoints for reverse-image search and direct image downloads."""

import logging
from typing import Annotated, cast
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.constants import (
    IMAGE_TIMEOUT_SECONDS,
    REQUEST_HEADERS,
    SEARCH_TIMEOUT_SECONDS,
)
from app.core.logging import log_event
from app.services.engines.yandex import scrape_yandex_reverse_image_urls
from app.utilities.image_utils import make_safe_filename

router = APIRouter()


@router.get("/reverse-search/yandex")
async def yandex_reverse_search(
    image_url: Annotated[
        str, Query(min_length=5, description="Public image URL to reverse-search")
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, list[str]]:
    """Run Yandex reverse-image search for a given image URL and return similar URLs."""

    cleaned = image_url.strip()
    if not cleaned.startswith("http"):
        log_event(
            logging.WARNING,
            "reverse search rejected non-http image_url",
            event="reverse_search.invalid_url",
        )
        raise HTTPException(
            status_code=400, detail="image_url must be a public HTTP(S) URL."
        )

    log_event(
        logging.INFO,
        "reverse search started",
        event="reverse_search.started",
        limit=limit,
        source_host=urlparse(cleaned).netloc.lower(),
    )
    timeout = httpx.Timeout(SEARCH_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        urls = await scrape_yandex_reverse_image_urls(cleaned, client)

    final_urls = urls[:limit]
    log_event(
        logging.INFO,
        "reverse search completed",
        event="reverse_search.completed",
        result_count=len(final_urls),
        source_host=urlparse(cleaned).netloc.lower(),
    )
    return {"results": final_urls}


@router.get("/download")
async def download_image(url: Annotated[str, Query(min_length=5)]) -> StreamingResponse:
    """Proxy-download a remote image so frontend can offer a direct download button."""

    if not url.startswith("http"):
        log_event(
            logging.WARNING,
            "download rejected non-http url",
            event="download.invalid_url",
        )
        raise HTTPException(status_code=400, detail="url must be a public HTTP(S) URL.")

    log_event(
        logging.INFO,
        "download started",
        event="download.started",
        source_host=urlparse(url).netloc.lower(),
    )
    timeout = httpx.Timeout(IMAGE_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(url, headers=REQUEST_HEADERS)
            response.raise_for_status()
            content_type = cast(
                str, response.headers.get("content-type", "application/octet-stream")
            )
            filename = make_safe_filename(url)
            log_event(
                logging.INFO,
                "download completed",
                event="download.completed",
                source_host=urlparse(url).netloc.lower(),
                content_type=content_type,
                content_length=len(response.content),
            )

            return StreamingResponse(
                content=iter([response.content]),
                media_type=content_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
    except httpx.HTTPError as exc:
        log_event(
            logging.ERROR,
            "download failed",
            event="download.failed",
            source_host=urlparse(url).netloc.lower(),
            error=str(exc),
        )
        raise HTTPException(
            status_code=502, detail=f"Failed to download remote image: {exc}"
        ) from exc
