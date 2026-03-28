"""Image processing, dimension probing, and perceptual hashing utilities."""

import hashlib
import logging
import re
from io import BytesIO
from typing import Optional, Tuple, cast
from urllib.parse import urlparse

import cloudscraper
import httpx
from PIL import Image, ImageFile, UnidentifiedImageError
from requests import RequestException

from app.core.constants import IMAGE_TIMEOUT_SECONDS, MAX_IMAGE_BYTES, REQUEST_HEADERS
from app.core.logging import log_event


def get_node_attr(node: object, key: str) -> Optional[str]:
    """Safely fetch a node attribute from selectolax nodes."""

    attributes = cast(Optional[dict[str, object]], getattr(node, "attributes", None))
    if isinstance(attributes, dict):
        value = attributes.get(key)
        if isinstance(value, str):
            return value
    return None


def derive_title_from_url(image_url: str) -> str:
    """Derive a lightweight title from the image filename."""

    path_tail = urlparse(image_url).path.rsplit("/", maxsplit=1)[-1]
    if not path_tail:
        return ""
    filename = path_tail.rsplit(".", maxsplit=1)[0]
    return re.sub(r"[_\-]+", " ", filename).strip()[:120]


def format_image_size(byte_count: int) -> str:
    """Format byte count into a compact KB/MB human-readable string."""

    if byte_count <= 0:
        return ""
    kb = byte_count / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    return f"{mb:.2f} MB"


def calculate_phash(image_bytes: bytes) -> Optional[str]:
    """Compute a lightweight perceptual hash (dHash) from image bytes."""

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            grayscale = image.convert("L")
            grayscale = grayscale.resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(grayscale.tobytes())
            bits: list[str] = []
            for row in range(8):
                row_start = row * 9
                for col in range(8):
                    left = pixels[row_start + col]
                    right = pixels[row_start + col + 1]
                    bits.append("1" if left > right else "0")
            return f"{int(''.join(bits), 2):016x}"
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def make_safe_filename(url: str) -> str:
    """Build a deterministic filename for download responses."""

    suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return f"image_{suffix}.jpg"


def is_orientation_allowed(width: int, height: int, orientation: str) -> bool:
    """Validate orientation filter against an image dimension tuple."""

    if orientation == "any":
        return True
    if orientation == "portrait":
        return height > width
    if orientation == "landscape":
        return width >= height
    return True


def meets_dimension_thresholds(
    width: int, height: int, min_width: int, min_height: int, orientation: str
) -> bool:
    """Check minimum dimensions with orientation-aware matching."""

    min_long = max(min_width, min_height)
    min_short = min(min_width, min_height)
    landscape_ok = width >= min_long and height >= min_short
    portrait_ok = width >= min_short and height >= min_long

    if orientation == "landscape":
        return landscape_ok
    if orientation == "portrait":
        return portrait_ok
    return landscape_ok or portrait_ok


def get_dimensions_with_cloudscraper(
    image_url: str,
) -> Optional[Tuple[int, int, bytes, int]]:
    """Fallback dimension probe using cloudscraper when direct async fetch is blocked."""

    parser = ImageFile.Parser()
    collected = bytearray()
    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(
            image_url, headers=REQUEST_HEADERS, timeout=IMAGE_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        content_length = int(response.headers.get("content-length", 0))
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue

            remaining = MAX_IMAGE_BYTES - len(collected)
            if remaining <= 0:
                break

            use_chunk = chunk[:remaining]
            collected.extend(use_chunk)
            parser.feed(use_chunk)

            if parser.image and len(collected) >= 64 * 1024:
                break

        parsed = parser.close()
        width, height = parsed.size
        return int(width), int(height), bytes(collected), content_length
    except (RequestException, UnidentifiedImageError, OSError, ValueError) as exc:
        log_event(
            logging.DEBUG,
            "cloudscraper dimension probe failed",
            event="image.dimension.cloudscraper_failed",
            url_host=urlparse(image_url).netloc.lower(),
            error=str(exc),
        )
        return None


async def get_dimensions_from_stream(
    client: httpx.AsyncClient,
    image_url: str,
) -> Optional[Tuple[int, int, bytes, int]]:
    """Download only a capped amount of image bytes and infer dimensions via PIL parser.

    Returns (width, height, partial_image_bytes, content_length) where
    content_length is the real file size from the Content-Length header (0 if
    the header is missing).
    """

    parser = ImageFile.Parser()
    collected = bytearray()

    try:
        async with client.stream(
            "GET", image_url, headers=REQUEST_HEADERS, timeout=IMAGE_TIMEOUT_SECONDS
        ) as response:
            _ = response.raise_for_status()
            content_length = int(response.headers.get("content-length", 0))
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if not chunk:
                    continue

                remaining = MAX_IMAGE_BYTES - len(collected)
                if remaining <= 0:
                    break

                use_chunk = chunk[:remaining]
                collected.extend(use_chunk)
                parser.feed(use_chunk)

                if parser.image:
                    width, height = parser.image.size
                    if width > 0 and height > 0 and len(collected) >= 8 * 1024:
                        return int(width), int(height), bytes(collected), content_length

                if parser.image and len(collected) >= 64 * 1024:
                    break

        if parser.image:
            width, height = parser.image.size
            if width > 0 and height > 0:
                return int(width), int(height), bytes(collected), content_length

        parsed = parser.close()
        width, height = parsed.size
        return int(width), int(height), bytes(collected), content_length
    except (httpx.HTTPError, UnidentifiedImageError, OSError, ValueError) as exc:
        log_event(
            logging.DEBUG,
            "stream dimension probe failed, trying cloudscraper",
            event="image.dimension.stream_failed",
            url_host=urlparse(image_url).netloc.lower(),
            error=str(exc),
        )
        import asyncio

        return await asyncio.to_thread(get_dimensions_with_cloudscraper, image_url)
