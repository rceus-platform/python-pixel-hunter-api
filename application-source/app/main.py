"""FastAPI service for HD image search, reverse-image lookup, and image download proxy."""
# pylint: disable=too-many-lines

import asyncio
import hashlib
import html
import importlib
import json
import re
import time
from collections.abc import Coroutine
from io import BytesIO
from json import JSONDecodeError
from typing import Annotated, NotRequired, Protocol, TypedDict, cast
from urllib.parse import parse_qs, quote_plus, unquote, unquote_plus, urlparse

import cloudscraper  # pyright: ignore[reportMissingTypeStubs]
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from PIL import Image, ImageFile, UnidentifiedImageError
from requests import RequestException, Response
from selectolax.lexbor import LexborHTMLParser

# Create the FastAPI application instance.
app = FastAPI(title="HD Image Finder API", version="2.0.0")

# Configure CORS for local React development servers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared HTTP headers to reduce request blocking from target sites.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Defaults for HD filtering and performance controls.
DEFAULT_MIN_WIDTH = 1920
DEFAULT_MIN_HEIGHT = 1080
FOUR_K_WIDTH = 3840
FOUR_K_HEIGHT = 2160
MAX_RESULTS = 20
MAX_CANDIDATES_PER_ENGINE = 200
WIKIMEDIA_PAGE_SIZE = 50
MAX_IMAGE_BYTES = 8_000_000
SEARCH_TIMEOUT_SECONDS = 12
IMAGE_TIMEOUT_SECONDS = 10
CONCURRENT_IMAGE_TASKS = 12
SUPPORTED_SEARCH_ENGINES = ("bing", "google", "yandex", "wikimedia")

# Regex helper for Google Images JSON payload snippets.
GOOGLE_OU_PATTERN = re.compile(r'"ou":"(.*?)"')
YANDEX_IMG_HREF_PATTERN = re.compile(r'"img_href":"(http[s]?://[^"\\]+)"')
YANDEX_ORIGIN_URL_PATTERN = re.compile(r'"origin":\{"url":"(http[s]?://[^"\\]+)"')
GENERIC_IMAGE_URL_PATTERN = re.compile(
    r"https?://[^\"'\\\s>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\"'\\\s>]*)?",
    re.IGNORECASE,
)
GOOGLE_ABOUT_IMAGE_URL_PATTERN = re.compile(
    r"https://www\.google\.com/search/about-this-image\?[^\"'\\\s>]+",
    re.IGNORECASE,
)


class ImageSearchResult(TypedDict):
    """Result payload shape for a validated image candidate."""

    url: str
    width: int
    height: int
    phash: NotRequired[str]
    unverified: NotRequired[bool]


class ScraperClient(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol for cloudscraper client methods used in this module."""

    def get(  # pylint: disable=missing-function-docstring
        self, url: str, headers: dict[str, str], timeout: int
    ) -> Response: ...


class SeleniumOptionsClient(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol for Chrome option objects used in Selenium fallback."""

    def add_argument(  # pylint: disable=missing-function-docstring
        self, argument: str
    ) -> None: ...


class SeleniumDriverClient(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol for WebDriver methods used in Selenium fallback."""

    page_source: str

    def set_page_load_timeout(  # pylint: disable=missing-function-docstring
        self, timeout: int
    ) -> None: ...
    def get(self, url: str) -> None: ...  # pylint: disable=missing-function-docstring
    def quit(self) -> None: ...  # pylint: disable=missing-function-docstring


class SeleniumWebDriverModule(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol for Selenium webdriver module."""

    def Chrome(  # pylint: disable=missing-function-docstring
        self, options: SeleniumOptionsClient
    ) -> SeleniumDriverClient: ...


class SeleniumChromeOptionsModule(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol for Selenium chrome options module."""

    def Options(self) -> SeleniumOptionsClient: ...  # pylint: disable=missing-function-docstring


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


def get_node_attr(node: object, key: str) -> str | None:
    """Safely fetch a node attribute from selectolax nodes."""
    attributes = cast(dict[str, object] | None, getattr(node, "attributes", None))
    if isinstance(attributes, dict):
        value = attributes.get(key)
        if isinstance(value, str):
            return value
    return None


def fetch_html_with_cloudscraper(url: str) -> str | None:
    """Fetch HTML with cloudscraper for anti-bot-protected pages."""
    try:
        scraper_obj = cast(
            object,
            cloudscraper.create_scraper(),  # pyright: ignore[reportUnknownMemberType]
        )
        scraper = cast(ScraperClient, scraper_obj)
        response = scraper.get(
            url, headers=REQUEST_HEADERS, timeout=SEARCH_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.text
    except RequestException:
        return None


def get_dimensions_with_cloudscraper(image_url: str) -> tuple[int, int, bytes] | None:
    """Fallback dimension probe using cloudscraper when direct async fetch is blocked."""
    parser = ImageFile.Parser()
    collected = bytearray()
    scraper_obj = cast(
        object,
        cloudscraper.create_scraper(),  # pyright: ignore[reportUnknownMemberType]
    )
    scraper = cast(ScraperClient, scraper_obj)

    try:
        response = scraper.get(
            image_url, headers=REQUEST_HEADERS, timeout=IMAGE_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=8192):  # pyright: ignore[reportAny]
            chunk_bytes = cast(bytes, chunk)
            if not chunk_bytes:
                continue

            remaining = MAX_IMAGE_BYTES - len(collected)
            if remaining <= 0:
                break

            use_chunk = chunk_bytes[:remaining]
            collected.extend(use_chunk)
            parser.feed(use_chunk)

            if parser.image and len(collected) >= 64 * 1024:
                break

        parsed = parser.close()
        width, height = parsed.size
        return int(width), int(height), bytes(collected)
    except (RequestException, UnidentifiedImageError, OSError, ValueError):
        return None


async def fetch_html(client: httpx.AsyncClient, url: str) -> str:
    """Fetch an HTML page and return text, raising HTTPException on failures."""
    try:
        response = await client.get(url, headers=REQUEST_HEADERS)
        _ = response.raise_for_status()
        return response.text
    except httpx.HTTPStatusError as exc:
        # Some engines challenge default clients. Try cloudscraper fallback on blocked statuses.
        if exc.response.status_code in {403, 429, 503}:
            fallback_html = await asyncio.to_thread(fetch_html_with_cloudscraper, url)
            if fallback_html:
                return fallback_html
        raise HTTPException(
            status_code=502, detail=f"Search engine fetch failed: {exc}"
        ) from exc
    except httpx.HTTPError as exc:
        fallback_html = await asyncio.to_thread(fetch_html_with_cloudscraper, url)
        if fallback_html:
            return fallback_html
        raise HTTPException(
            status_code=502, detail=f"Search engine fetch failed: {exc}"
        ) from exc


# Browser fallback needs defensive branches to handle runtime driver availability.
# pylint: disable=too-many-locals,too-many-branches,broad-exception-caught,too-many-statements
def scrape_engine_urls_with_selenium(query: str, engine: str) -> list[str]:
    """Use Selenium as a browser fallback to collect image URLs."""
    try:
        webdriver_module = cast(
            SeleniumWebDriverModule,
            cast(object, importlib.import_module("selenium.webdriver")),
        )
        chrome_options_module = cast(
            SeleniumChromeOptionsModule,
            cast(object, importlib.import_module("selenium.webdriver.chrome.options")),
        )
    except Exception:
        return []

    search_url: str | None = None
    if engine == "google":
        search_url = f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}"
    elif engine == "bing":
        search_url = f"https://www.bing.com/images/search?q={quote_plus(query)}"
    else:
        return []

    options = chrome_options_module.Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-agent={REQUEST_HEADERS['User-Agent']}")

    driver: SeleniumDriverClient | None = None
    page_html = ""
    try:
        driver = webdriver_module.Chrome(options=options)
        driver.set_page_load_timeout(SEARCH_TIMEOUT_SECONDS)
        driver.get(search_url)
        time.sleep(2)
        page_html = driver.page_source
    except Exception:
        return []
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    tree = LexborHTMLParser(page_html)
    urls: list[str] = []
    seen: set[str] = set()

    def maybe_add_url(candidate: str | None) -> None:
        normalized = normalize_candidate_url(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    for img in tree.css("img"):
        for attr in ("src", "data-src", "data-iurl", "data-src-hq", "data-src-large"):
            maybe_add_url(get_node_attr(img, attr))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break
        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for anchor in tree.css("a[href]"):
            href = get_node_attr(anchor, "href")
            if isinstance(href, str):
                maybe_add_url(extract_image_url_from_href(href))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for match in cast(list[str], GENERIC_IMAGE_URL_PATTERN.findall(page_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    return urls


# Fallback parsing needs multiple HTML branches and temporary variables.
# pylint: disable=too-many-locals,too-many-branches
async def scrape_bing_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> list[str]:
    """Scrape Bing Images page and extract image source URLs from tile metadata."""
    first = page * 35 + 1
    search_url = (
        f"https://www.bing.com/images/search?q={quote_plus(query)}"
        f"&first={first}&count=35"
    )
    page_html = await fetch_html(client, search_url)

    tree = LexborHTMLParser(page_html)
    urls: list[str] = []
    seen: set[str] = set()

    def maybe_add_url(candidate: str | None) -> None:
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
        except JSONDecodeError:
            continue

        if not isinstance(payload_obj, dict):
            continue

        payload_dict = cast(dict[str, object], payload_obj)
        candidate = payload_dict.get("murl") or payload_dict.get("turl")
        maybe_add_url(cast(str | None, candidate))

        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    # Fallback for Bing HTML variants that do not expose `a.iusc` metadata.
    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for img in tree.css("img"):
            for attr in ("src", "data-src", "data-src-hq", "data-src-large"):
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
        for match in cast(list[str], GENERIC_IMAGE_URL_PATTERN.findall(page_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    return urls


async def scrape_google_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> list[str]:
    """Scrape Google Images HTML and extract original image URLs from embedded JSON snippets."""
    start = page * 20
    search_url = (
        f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}&start={start}"
    )
    page_html = await fetch_html(client, search_url)
    decoded_html = (
        page_html.replace("\\u003d", "=").replace("\\u0026", "&").replace("\\/", "/")
    )
    tree = LexborHTMLParser(decoded_html)

    urls: list[str] = []
    seen: set[str] = set()

    def maybe_add_url(candidate: str | None) -> None:
        normalized = normalize_candidate_url(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    for match in cast(list[str], GOOGLE_OU_PATTERN.findall(decoded_html)):
        candidate = bytes(match, "utf-8").decode("unicode_escape")
        candidate = candidate.replace("\\/", "/")
        maybe_add_url(candidate)

        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    # Fallback: image tags and data attributes if JSON extraction is blocked or empty.
    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for img in tree.css("img"):
            for attr in ("src", "data-src", "data-iurl", "data-ou"):
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

    # New Google Images layouts often include origin URLs in about-this-image links.
    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for about_url in cast(
            list[str], GOOGLE_ABOUT_IMAGE_URL_PATTERN.findall(decoded_html)
        ):
            maybe_add_url(extract_image_url_from_href(about_url))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for match in cast(list[str], GENERIC_IMAGE_URL_PATTERN.findall(decoded_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    return urls


async def scrape_yandex_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> list[str]:
    """Scrape Yandex image search result page for image URLs."""
    search_url = f"https://yandex.com/images/search?text={quote_plus(query)}&p={page}"
    page_html = await fetch_html(client, search_url)
    tree = LexborHTMLParser(page_html)

    urls: list[str] = []
    seen: set[str] = set()

    def maybe_add_url(candidate: str | None) -> None:
        normalized = normalize_candidate_url(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    for match in cast(list[str], YANDEX_IMG_HREF_PATTERN.findall(page_html)):
        maybe_add_url(match.replace("\\/", "/"))
        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for match in cast(list[str], YANDEX_ORIGIN_URL_PATTERN.findall(page_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for img in tree.css("img"):
            for attr in ("src", "data-src", "data-image", "data-src-large"):
                maybe_add_url(get_node_attr(img, attr))
                if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                    break
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    if len(urls) < MAX_CANDIDATES_PER_ENGINE:
        for match in cast(list[str], GENERIC_IMAGE_URL_PATTERN.findall(page_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    return urls


async def scrape_yandex_reverse_image_urls(
    image_url: str, client: httpx.AsyncClient
) -> list[str]:
    """Scrape Yandex reverse image search result page and extract similar-image URLs."""
    url = f"https://yandex.com/images/search?rpt=imageview&url={quote_plus(image_url)}"
    page_html = await fetch_html(client, url)

    urls: list[str] = []
    seen: set[str] = set()

    # Yandex often places source links in JSON payload values under "img_href".
    for match in cast(list[str], YANDEX_IMG_HREF_PATTERN.findall(page_html)):
        candidate = normalize_candidate_url(match.replace("\\/", "/"))
        if candidate and candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    return urls


async def scrape_wikimedia_image_urls(
    query: str, client: httpx.AsyncClient, page: int = 0
) -> list[str]:
    """Fetch candidate image URLs from Wikimedia Commons public API."""
    api_url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",  # File namespace.
        "gsrlimit": str(WIKIMEDIA_PAGE_SIZE),
        "gsroffset": str(page * WIKIMEDIA_PAGE_SIZE),
        "prop": "imageinfo",
        "iiprop": "url|mime",
        "format": "json",
        "formatversion": "2",
    }

    try:
        response = await client.get(api_url, params=params, headers=REQUEST_HEADERS)
        _ = response.raise_for_status()
        payload = cast(dict[str, object], response.json())
    except (httpx.HTTPError, ValueError):
        return []

    query_obj = payload.get("query")
    if not isinstance(query_obj, dict):
        return []
    query_dict = cast(dict[str, object], query_obj)

    pages_obj = query_dict.get("pages")
    if not isinstance(pages_obj, list):
        return []
    pages = cast(list[object], pages_obj)

    urls: list[str] = []
    seen: set[str] = set()
    for page_item in pages:
        if not isinstance(page_item, dict):
            continue
        page_dict = cast(dict[str, object], page_item)
        imageinfo_obj = page_dict.get("imageinfo")
        if not isinstance(imageinfo_obj, list) or not imageinfo_obj:
            continue
        imageinfo = cast(list[object], imageinfo_obj)
        first_info = imageinfo[0]
        if not isinstance(first_info, dict):
            continue
        first_info_dict = cast(dict[str, object], first_info)
        candidate = first_info_dict.get("url")
        mime = first_info_dict.get("mime")
        if not isinstance(candidate, str) or not candidate.startswith("http"):
            continue
        if isinstance(mime, str) and not mime.startswith("image/"):
            continue
        if candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
        if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
            break

    return urls


def is_orientation_allowed(width: int, height: int, orientation: str) -> bool:
    """Validate orientation filter against an image dimension tuple."""
    if orientation == "any":
        return True
    if orientation == "portrait":
        return height > width
    if orientation == "landscape":
        return width >= height
    return True


async def get_dimensions_from_stream(
    client: httpx.AsyncClient,
    image_url: str,
) -> tuple[int, int, bytes] | None:
    """Download only a capped amount of image bytes and infer dimensions via PIL parser."""
    parser = ImageFile.Parser()
    collected = bytearray()

    try:
        async with client.stream(
            "GET", image_url, headers=REQUEST_HEADERS, timeout=IMAGE_TIMEOUT_SECONDS
        ) as response:
            _ = response.raise_for_status()
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
                        return int(width), int(height), bytes(collected)

                if parser.image and len(collected) >= 64 * 1024:
                    break

        if parser.image:
            width, height = parser.image.size
            if width > 0 and height > 0:
                return int(width), int(height), bytes(collected)

        parsed = parser.close()
        width, height = parsed.size
        return int(width), int(height), bytes(collected)
    except (httpx.HTTPError, UnidentifiedImageError, OSError, ValueError):
        return await asyncio.to_thread(get_dimensions_with_cloudscraper, image_url)


async def maybe_get_content_type_via_head(
    client: httpx.AsyncClient, image_url: str
) -> str | None:
    """Use HEAD request to cheaply validate image content type before GET where possible."""
    try:
        response = await client.head(
            image_url, headers=REQUEST_HEADERS, timeout=IMAGE_TIMEOUT_SECONDS
        )
        if response.status_code >= 400:
            return None
        return cast(str, response.headers.get("content-type", ""))
    except httpx.HTTPError:
        return None


def calculate_phash(image_bytes: bytes) -> str | None:
    """Compute a lightweight perceptual hash (dHash) from image bytes."""
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            grayscale = image.convert("L")
            grayscale = grayscale.resize(  # pyright: ignore[reportUnknownMemberType]
                (9, 8), Image.Resampling.LANCZOS
            )
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


async def collect_engine_urls(
    query: str, engines: list[str], client: httpx.AsyncClient, pages: int = 1
) -> list[str]:
    """Execute selected search engine scrapers and return deduplicated URL candidates."""
    selected_engines = engines
    if "all" in selected_engines:
        selected_engines = list(SUPPORTED_SEARCH_ENGINES)

    unsupported = [
        engine for engine in selected_engines if engine not in SUPPORTED_SEARCH_ENGINES
    ]
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported engines: {', '.join(sorted(set(unsupported)))}. "
                + f"Use one of: {', '.join(SUPPORTED_SEARCH_ENGINES)} or all."
            ),
        )

    page_count = max(1, pages)
    task_specs: list[tuple[str, int, Coroutine[object, object, list[str]]]] = []
    for page in range(page_count):
        if "bing" in selected_engines:
            task_specs.append(
                ("bing", page, scrape_bing_image_urls(query, client, page))
            )
        if "google" in selected_engines:
            task_specs.append(
                ("google", page, scrape_google_image_urls(query, client, page))
            )
        if "yandex" in selected_engines:
            task_specs.append(
                ("yandex", page, scrape_yandex_image_urls(query, client, page))
            )
        if "wikimedia" in selected_engines:
            task_specs.append(
                ("wikimedia", page, scrape_wikimedia_image_urls(query, client, page))
            )

    if not task_specs:
        raise HTTPException(
            status_code=400,
            detail=(
                "No supported engine selected. "
                + f"Use {', '.join(SUPPORTED_SEARCH_ENGINES)}, or all."
            ),
        )

    outputs = await asyncio.gather(
        *[coroutine for _, _, coroutine in task_specs], return_exceptions=True
    )

    deduped: list[str] = []
    seen: set[str] = set()
    successful_responses = 0
    failed_engines: list[str] = []
    engine_has_urls = {engine: False for engine in selected_engines}

    fallback_engines: list[str] = []

    for (engine_name, _page, _), output in zip(task_specs, outputs):
        if isinstance(output, BaseException):
            failed_engines.append(engine_name)
            continue
        successful_responses += 1
        if output:
            engine_has_urls[engine_name] = True
        for url in output:
            if url not in seen:
                seen.add(url)
                deduped.append(url)

    for engine_name, has_urls in engine_has_urls.items():
        if not has_urls:
            fallback_engines.append(engine_name)

    selenium_engines = [
        engine
        for engine in fallback_engines
        if engine in {"google", "bing"} and engine in selected_engines
    ]
    for engine_name in selenium_engines:
        selenium_urls = await asyncio.to_thread(
            scrape_engine_urls_with_selenium, query, engine_name
        )
        for url in selenium_urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)

    if successful_responses == 0:
        if deduped:
            return deduped
        raise HTTPException(
            status_code=502,
            detail=(
                "All selected engines failed to respond. "
                + f"Failed engines: {', '.join(failed_engines)}."
            ),
        )

    return deduped


# pylint: disable=too-many-arguments,too-many-positional-arguments
async def evaluate_image_candidate(
    client: httpx.AsyncClient,
    image_url: str,
    min_width: int,
    min_height: int,
    orientation: str,
    dedupe: bool,
) -> ImageSearchResult | None:
    """Evaluate one candidate image: HEAD check, dimension extraction, filters, and hash."""
    # We don't hard-reject via HEAD because many hosts block/alter HEAD responses.
    dimensions = await get_dimensions_from_stream(client, image_url)
    if not dimensions:
        return None

    width, height, image_bytes = dimensions
    if width < min_width or height < min_height:
        return None

    if not is_orientation_allowed(width, height, orientation):
        return None

    result: ImageSearchResult = {
        "url": image_url,
        "width": width,
        "height": height,
    }

    if dedupe:
        phash = calculate_phash(image_bytes)
        if phash:
            result["phash"] = phash

    return result


@app.get("/health")
def health() -> dict[str, str]:
    """Provide a simple health-check endpoint for local verification."""
    return {"status": "ok"}


@app.get("/search")
# FastAPI endpoint signature requires explicit query params.
# pylint: disable=too-many-arguments,too-many-locals,too-many-positional-arguments
async def search_images(
    query: Annotated[str, Query(min_length=1, description="Actress name or keyword")],
    engines: Annotated[
        str,
        Query(description="Comma-separated engines: bing,google,yandex,wikimedia,all"),
    ] = "bing",
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
        int, Query(ge=1, le=5, description="How many pages to scrape per engine")
    ] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = MAX_RESULTS,
) -> dict[str, list[ImageSearchResult]]:
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

    timeout = httpx.Timeout(SEARCH_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        try:
            candidates = await collect_engine_urls(
                cleaned_query, selected_engines, client, pages=pages
            )
        except HTTPException as exc:
            if exc.status_code == 502:
                candidates = []
            else:
                raise
        if not candidates:
            # Fallback source when search-engine scraping returns zero candidates.
            candidates = await scrape_wikimedia_image_urls(cleaned_query, client)

        semaphore = asyncio.Semaphore(CONCURRENT_IMAGE_TASKS)

        async def guarded(url: str) -> ImageSearchResult | None:
            async with semaphore:
                return await evaluate_image_candidate(
                    client=client,
                    image_url=url,
                    min_width=min_width,
                    min_height=min_height,
                    orientation=orientation,
                    dedupe=remove_duplicates,
                )

        evaluated = await asyncio.gather(
            *[guarded(url) for url in candidates], return_exceptions=False
        )

    verified_results = [item for item in evaluated if item]
    results: list[ImageSearchResult] = verified_results

    if allow_unverified:
        verified_urls = {item["url"] for item in verified_results}
        for url in candidates:
            if url in verified_urls:
                continue
            results.append(
                {
                    "url": url,
                    "width": 0,
                    "height": 0,
                    "unverified": True,
                }
            )
            if len(results) >= limit:
                break

    if not results and candidates:
        # As a last resort, return discovered candidate URLs when verification downloads fail.
        fallback_results: list[ImageSearchResult] = []
        for url in candidates[:limit]:
            fallback_results.append(
                {
                    "url": url,
                    "width": 0,
                    "height": 0,
                    "unverified": True,
                }
            )
        return {"results": fallback_results}

    if remove_duplicates:
        unique_results: list[ImageSearchResult] = []
        seen_hashes: set[str] = set()
        for item in results:
            phash = item.get("phash")
            if isinstance(phash, str):
                if phash in seen_hashes:
                    continue
                seen_hashes.add(phash)
            unique_results.append(item)
        results = unique_results

    results.sort(
        key=lambda item: int(item["width"]) * int(item["height"]), reverse=True
    )

    # Hide internal dedupe fields from API output.
    for item in results:
        if "phash" in item:
            del item["phash"]

    return {"results": results[:limit]}


@app.get("/reverse-search/yandex")
async def yandex_reverse_search(
    image_url: Annotated[
        str, Query(min_length=5, description="Public image URL to reverse-search")
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, list[str]]:
    """Run Yandex reverse-image search for a given image URL and return similar URLs."""
    cleaned = image_url.strip()
    if not cleaned.startswith("http"):
        raise HTTPException(
            status_code=400, detail="image_url must be a public HTTP(S) URL."
        )

    timeout = httpx.Timeout(SEARCH_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        urls = await scrape_yandex_reverse_image_urls(cleaned, client)

    return {"results": urls[:limit]}


@app.get("/download")
async def download_image(url: Annotated[str, Query(min_length=5)]) -> StreamingResponse:
    """Proxy-download a remote image so frontend can offer a direct download button."""
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="url must be a public HTTP(S) URL.")

    timeout = httpx.Timeout(IMAGE_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(url, headers=REQUEST_HEADERS)
            _ = response.raise_for_status()
            content_type = cast(
                str, response.headers.get("content-type", "application/octet-stream")
            )
            filename = make_safe_filename(url)

            return StreamingResponse(
                content=iter([response.content]),
                media_type=content_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to download remote image: {exc}"
        ) from exc
