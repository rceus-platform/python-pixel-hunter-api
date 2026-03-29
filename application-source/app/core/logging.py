"""Structured JSON logging and request correlation utilities."""

import contextvars
import json
import logging
import os

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
request_log_counters_context: contextvars.ContextVar[dict[str, int] | None] = (
    contextvars.ContextVar("request_log_counters", default=None)
)


class JsonLogFormatter(logging.Formatter):
    """Format logs as compact JSON records."""

    def format(self, record: logging.LogRecord) -> str:
        """Compose a log record into a single-line JSON string."""

        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_context.get()),
        }
        optional_fields = (
            "event",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "engine",
            "engines",
            "pages",
            "candidate_count",
            "verified_count",
            "result_count",
            "limit",
            "allow_unverified",
            "remove_duplicates",
            "source_host",
            "query_hash",
            "url_host",
            "content_type",
            "content_length",
            "error",
        )
        for field in optional_fields:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class RequestIDFilter(logging.Filter):
    """Inject the active request_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach the current request_id to the log record."""

        if not hasattr(record, "request_id"):
            record.request_id = request_id_context.get()
        return True


def configure_logging() -> None:
    """Initialize process-wide logging configuration."""

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = os.getenv("LOG_FORMAT", "text").lower()
    root_logger = logging.getLogger()

    if getattr(configure_logging, "_configured", False):
        return

    handler = logging.StreamHandler()
    handler.addFilter(RequestIDFilter())
    if log_format == "text":
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s | request_id=%(request_id)s",
                datefmt="%Y-%m-%d %H:%M:%S%z",
            )
        )
    else:
        handler.setFormatter(JsonLogFormatter())

    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
    logging.captureWarnings(True)
    # Silence noisy per-request transport logs; keep only our app-level logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    setattr(configure_logging, "_configured", True)


def log_event(level: int, message: str, **fields: object) -> None:
    """Emit a structured log entry with the active request id."""

    logger.log(level, message, extra={"request_id": request_id_context.get(), **fields})


def log_event_throttled(
    counter_key: str,
    message: str,
    *,
    first_level: int = logging.WARNING,
    repeat_level: int = logging.DEBUG,
    repeat_every: int = 10,
    **fields: object,
) -> None:
    """Emit warning-like events once, then downgrade repeats to reduce log noise."""

    counters = request_log_counters_context.get()
    if counters is None:
        log_event(first_level, message, **fields)
        return

    count = counters.get(counter_key, 0) + 1
    counters[counter_key] = count

    if count == 1:
        log_event(first_level, message, **fields)
        return

    if repeat_every > 0 and count % repeat_every == 0:
        log_event(
            repeat_level,
            f"{message} (repeat={count})",
            **fields,
        )


# Initialize logging immediately on module import if not already configured
configure_logging()
logger = logging.getLogger("pixel_hunter_api")
