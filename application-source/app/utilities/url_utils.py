"""URL parsing and normalization utilities for search engine results."""

import hashlib
import html
import re
from urllib.parse import parse_qs, unquote, unquote_plus, urlparse

from app.core.constants import GOOGLE_INTERNAL_HOSTS


def query_hash(query: str) -> str:
    """Return a stable, non-reversible fingerprint for query observability."""

    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]


def extract_image_url_from_href(href: str) -> str | None:
    """Extract embedded image URL query params from search-engine result links."""

    if not href:
        return None

    parsed = urlparse(href)
    query = parse_qs(parsed.query)

    for key in ("imgurl", "mediaurl", "objurl", "mediaUrl"):
        values = query.get(key)
        if values:
            candidate = normalize_candidate_url(unquote(values[0]))
            if candidate:
                return candidate

    if "/search/about-this-image" in href:
        values = query.get("q")
        if values:
            candidate = normalize_candidate_url(unquote_plus(values[0]))
            if candidate:
                return candidate

    return None


def normalize_candidate_url(candidate: str | None) -> str | None:
    """Normalize extracted URL fragments and drop malformed payloads."""

    if not candidate:
        return None

    decoded = (
        candidate.strip()
        .replace("\\/", "/")
        .replace("\\u003d", "=")
        .replace("\\u0026", "&")
    )
    decoded = html.unescape(decoded)
    # Some engines embed image URLs in serialized JSON; trim obvious payload tails.
    for marker in ('","', '"}', "},", "&quot;", "&quot"):
        if marker in decoded:
            decoded = decoded.split(marker, 1)[0]
    match = re.search(r"https?://[^\s\"'<>]+", decoded)
    if not match:
        return None

    return match.group(0).rstrip(".,);")


def source_from_url(image_url: str) -> str:
    """Extract source host from image URL."""

    return urlparse(image_url).netloc.lower()


def is_google_internal_url(candidate_url: str) -> bool:
    """Return True when URL points to Google-owned hosts, not source images."""

    host = source_from_url(candidate_url)
    return host.endswith("google.com") or host in GOOGLE_INTERNAL_HOSTS
