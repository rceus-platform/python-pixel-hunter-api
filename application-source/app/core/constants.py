"""Global constants and search engine configurations for the Pixel Hunter API."""

import re

# Shared HTTP headers to reduce request blocking from target sites.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
GOOGLE_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Cookie": "CONSENT=YES+cb.20210328-17-p0.en+FX+667; SOCS=CAESHAgBEhIaAB",
}

# Defaults for HD filtering and performance controls.
DEFAULT_MIN_WIDTH = 1080
DEFAULT_MIN_HEIGHT = 1920
FOUR_K_WIDTH = 3840
FOUR_K_HEIGHT = 2160
MAX_RESULTS = 20
MAX_CANDIDATES_PER_ENGINE = 200
MAX_IMAGE_BYTES = 8_000_000
SEARCH_TIMEOUT_SECONDS = 12
IMAGE_TIMEOUT_SECONDS = 10
CONCURRENT_IMAGE_TASKS = 12
MAX_STREAM_PAGES = 50
MIN_ENGINE_CANDIDATES_BEFORE_BROWSER_FALLBACK = 12
STREAM_SOFT_FALLBACK_THRESHOLD = 24
STREAM_SOFT_UNVERIFIED_CAP = 64
STREAM_RELAXED_MIN_DIMENSION_FLOOR = 1080
STREAM_ENGINE_EMPTY_PAGE_STREAK_LIMIT = 3
BLOCKED_FETCH_LOG_REPEAT_EVERY = 12
ENGINE_PAGE_TIMEOUT_SECONDS = 8.0
ENGINE_TOTAL_TIMEOUT_BUDGET_SECONDS: dict[str, float] = {
    "google": 24.0,
    "duckduckgo": 18.0,
    "baidu": 18.0,
}
SUPPORTED_SEARCH_ENGINES = ("bing", "google", "yandex", "duckduckgo", "baidu")
GOOGLE_INTERNAL_HOSTS = (
    "google.com",
    "www.google.com",
    "encrypted-tbn0.gstatic.com",
    "gstatic.com",
    "www.gstatic.com",
)

# Regex helper for Google Images JSON payload snippets.
GOOGLE_OU_PATTERN = re.compile(r'"ou":"(.*?)"')
YANDEX_IMG_HREF_PATTERN = re.compile(r'"img_href":"(http[s]?://[^"\\\\]+)"')
YANDEX_ORIGIN_URL_PATTERN = re.compile(r'"origin":\{"url":"(http[s]?://[^"\\\\]+)"')
GOOGLE_VQD_PATTERN = re.compile(r"vqd=(?:'|\")([^'\"]+)")
GOOGLE_IMAGE_URL_PATTERN = re.compile(
    r'"(?:ou|iurl|imgurl|image_url)":"(https?://[^"\\\\]+)"', re.IGNORECASE
)
GOOGLE_BARE_IMAGE_URL_PATTERN = re.compile(
    r"https?://[^\"'\\\\s>]+?\.(?:jpg|jpeg|png|webp|avif)(?:\?[^\"'\\\\s>]*)?",
    re.IGNORECASE,
)
DUCKDUCKGO_VQD_PATTERN = re.compile(r"vqd=(?:'|\")([^'\"]+)")
GENERIC_IMAGE_URL_PATTERN = re.compile(
    r"https?://[^\"'\\\\s>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\"'\\\\s>]*)?",
    re.IGNORECASE,
)
GOOGLE_ABOUT_IMAGE_URL_PATTERN = re.compile(
    r"https://www\.google\.com/search/about-this-image\?[^\"'\\\\s>]+",
    re.IGNORECASE,
)
