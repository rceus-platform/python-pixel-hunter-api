"""Main entry point for the HD Image Finder API."""

import logging
import time
import uuid
from typing import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.api import api_router
from app.core.logging import (
    configure_logging,
    log_event,
    request_id_context,
    request_log_counters_context,
)

# Initialize FastAPI application
app = FastAPI(
    title="HD Image Finder API",
    version="2.0.0",
    description="FastAPI service for HD image search and reverse-image lookup.",
)

# Configure CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log incoming requests with request id, status code, and latency."""

    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_context.set(request_id)
    log_token = request_log_counters_context.set({})
    start = time.perf_counter()
    path = request.url.path
    method = request.method

    log_event(
        logging.INFO,
        "HTTP request started",
        event="http.request.started",
        method=method,
        path=path,
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(
            logging.ERROR,
            "HTTP request failed",
            event="http.request.failed",
            method=method,
            path=path,
            duration_ms=duration_ms,
            error=str(exc),
        )
        raise
    finally:
        request_log_counters_context.reset(log_token)
        request_id_context.reset(token)

    response.headers["X-Request-ID"] = request_id
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    level = (
        logging.INFO
        if response.status_code < 400
        else logging.WARNING
        if response.status_code < 500
        else logging.ERROR
    )
    log_event(
        level,
        "HTTP request completed",
        event="http.request.completed",
        method=method,
        path=path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


@app.get("/health")
def health() -> dict[str, str]:
    """Provide a simple health-check endpoint for local verification."""

    log_event(logging.DEBUG, "health check", event="health.check")
    return {"status": "ok"}


# Include all routers
app.include_router(api_router)

# Initialize logging on startup
configure_logging()
logger = logging.getLogger("pixel_hunter_api")
logger.info("Application initialized", extra={"event": "app.startup"})
