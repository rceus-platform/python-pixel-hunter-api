"""Selenium-based browser automation for scraping search engines that block simple HTTP requests."""

import importlib
import logging
import os
import tempfile
import time
import zipfile
from typing import List, Optional, Protocol, cast
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser

from app.core.constants import (
    GENERIC_IMAGE_URL_PATTERN,
    MAX_CANDIDATES_PER_ENGINE,
    REQUEST_HEADERS,
    SEARCH_TIMEOUT_SECONDS,
)
from app.core.logging import log_event
from app.utilities.image_utils import get_node_attr
from app.utilities.url_utils import (
    extract_image_url_from_href,
    normalize_candidate_url,
)


class SeleniumOptionsClient(Protocol):
    """Protocol for Selenium Chrome options."""

    def add_argument(self, argument: str) -> None:
        """Add a command-line argument to the browser."""

    def add_extension(self, extension_path: str) -> None:
        """Add an extension zip to the browser."""


class SeleniumDriverClient(Protocol):
    """Protocol for Selenium Chrome driver."""

    page_source: str

    def set_page_load_timeout(self, timeout: int) -> None:
        """Set the maximum time to wait for a page load."""

    def get(self, url: str) -> None:
        """Navigate to a URL."""

    def quit(self) -> None:
        """Close the browser and clean up resources."""

    def execute_script(self, script: str) -> object:
        """Execute JavaScript in the context of the current page."""


class SeleniumWebDriverModule(Protocol):
    """Protocol for the selenium.webdriver module."""

    def Chrome(self, options: SeleniumOptionsClient) -> SeleniumDriverClient:
        """Create a new Chrome driver instance."""


class SeleniumChromeOptionsModule(Protocol):
    """Protocol for the selenium.webdriver.chrome.options module."""

    def Options(self) -> SeleniumOptionsClient:
        """Create a new Chrome options instance."""


def create_proxy_auth_extension(
    proxy_host: str, proxy_port: str, proxy_user: str, proxy_pass: str
) -> str:
    """Create a temporary Chrome extension zip to handle proxy authentication."""

    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy Auth",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": {
            "scripts": ["background.js"]
        },
        "minimum_chrome_version":"22.0.0"
    }
    """
    background_js = """
    var config = {
        mode: "fixed_servers",
        rules: {
            singleProxy: {
                scheme: "http",
                host: "%s",
                port: parseInt(%s)
            },
            bypassList: ["localhost"]
        }
    };
    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
    chrome.webRequest.onAuthRequired.addListener(
        function(details) {
            return {
                authCredentials: {
                    username: "%s",
                    password: "%s"
                }
            };
        },
        {urls: ["<all_urls>"]},
        ["blocking"]
    );
    """ % (
        proxy_host,
        proxy_port,
        proxy_user,
        proxy_pass,
    )

    plugin_path = tempfile.NamedTemporaryFile(suffix=".zip", delete=False).name
    with zipfile.ZipFile(plugin_path, "w") as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    return plugin_path


def scrape_engine_urls_with_selenium(query: str, engine: str) -> List[str]:
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
    except Exception as exc:
        log_event(
            logging.WARNING,
            "selenium modules unavailable",
            event="scrape.selenium.module_unavailable",
            engine=engine,
            error=str(exc),
        )
        return []

    search_url: Optional[str] = None
    if engine == "google":
        search_url = f"https://www.google.com/search?q={quote_plus(query)}&udm=2&hl=en"
    elif engine == "bing":
        search_url = f"https://www.bing.com/images/search?q={quote_plus(query)}"
    elif engine == "duckduckgo":
        search_url = (
            f"https://duckduckgo.com/?q={quote_plus(query)}&iax=images&ia=images"
        )
    elif engine == "baidu":
        search_url = (
            f"https://image.baidu.com/search/index"
            f"?tn=baiduimage&word={quote_plus(query)}"
        )
    else:
        return []

    options = chrome_options_module.Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-agent={REQUEST_HEADERS['User-Agent']}")

    proxy_host = os.getenv("PROXY_HOST")
    proxy_port = os.getenv("PROXY_PORT")
    proxy_user = os.getenv("PROXY_USER")
    proxy_pass = os.getenv("PROXY_PASS")

    proxy_plugin_path: Optional[str] = None
    if proxy_host and proxy_port:
        if proxy_user and proxy_pass:
            proxy_plugin_path = create_proxy_auth_extension(
                proxy_host, proxy_port, proxy_user, proxy_pass
            )
            options.add_extension(proxy_plugin_path)
            log_event(
                logging.INFO,
                "Using authenticated residential proxy for Selenium",
                event="scrape.selenium.proxy_auth_enabled",
                engine=engine,
                proxy_host=proxy_host,
            )
        else:
            options.add_argument(f"--proxy-server=http://{proxy_host}:{proxy_port}")
            log_event(
                logging.INFO,
                "Using unauthenticated proxy for Selenium",
                event="scrape.selenium.proxy_enabled",
                engine=engine,
                proxy_host=proxy_host,
            )

    driver: Optional[SeleniumDriverClient] = None
    page_html = ""
    try:
        driver = webdriver_module.Chrome(options=options)
        driver.set_page_load_timeout(SEARCH_TIMEOUT_SECONDS)
        driver.get(search_url)
        time.sleep(2)
        if engine == "google":
            try:
                driver.execute_script("""
                    var ss = ['button', 'a', '[role="button"]', 'div', 'span'];
                    var ls = ['reject all', 'accept all', 'i agree', 'consent', 'agree'];
                    for (var s of ss) {
                        var elements = document.querySelectorAll(s);
                        for (var i = 0; i < elements.length; i++) {
                            var t = (elements[i].innerText || elements[i].value || "").toLowerCase().trim();
                            for (var l of ls) {
                                if (t === l || t.includes(l)) {
                                    try { elements[i].click(); } catch(e) {}
                                    return;
                                }
                            }
                        }
                    }
                """)
                time.sleep(1)
                for _ in range(5):
                    driver.execute_script(
                        "window.scrollTo(0, document.body.scrollHeight);"
                    )
                    time.sleep(1.5)

                js_urls = driver.execute_script("""
                    var res = [];
                    var ans = document.querySelectorAll('a[href*="/imgres"]');
                    for (var i = 0; i < ans.length; i++) {
                        var href = ans[i].getAttribute('href');
                        if (href && href.includes('imgurl=')) {
                            try {
                                var ps = href.split('imgurl=');
                                if (ps.length > 1) {
                                    var u = ps[1].split('&')[0];
                                    if (u) { res.push(decodeURIComponent(u)); }
                                }
                            } catch(e) {}
                        }
                    }
                    var imgs = document.querySelectorAll('img');
                    for (var j = 0; j < imgs.length; j++) {
                        var s = imgs[j].src || imgs[j].getAttribute('data-src');
                        if (s && s.startsWith('http')) { res.push(s); }
                    }
                    return [...new Set(res)].filter(u => u.length < 2000);
                """)
                page_html = driver.page_source
                if isinstance(js_urls, list):
                    injected = " ".join([f"<img src='{u}'>" for u in js_urls])
                    page_html += injected
            except Exception as js_exc:
                log_event(
                    logging.DEBUG,
                    "selenium js extraction skipped",
                    event="scrape.selenium.js_error",
                    engine=engine,
                    error=str(js_exc),
                )
                page_html = driver.page_source
        else:
            page_html = driver.page_source
    except Exception as exc:
        log_event(
            logging.WARNING,
            "selenium scrape failed",
            event="scrape.selenium.failed",
            engine=engine,
            error=str(exc),
        )
        return []
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if proxy_plugin_path and os.path.exists(proxy_plugin_path):
            try:
                os.remove(proxy_plugin_path)
            except Exception:
                pass

    tree = LexborHTMLParser(page_html)
    urls: List[str] = []
    seen: set[str] = set()

    def maybe_add_url(candidate: Optional[str]) -> None:
        normalized = normalize_candidate_url(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    for img in tree.css("img"):
        attrs = ("src", "data-src", "data-iurl", "data-src-hq", "data-src-large")
        for attr in attrs:
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
        for match in cast(List[str], GENERIC_IMAGE_URL_PATTERN.findall(page_html)):
            maybe_add_url(match.replace("\\/", "/"))
            if len(urls) >= MAX_CANDIDATES_PER_ENGINE:
                break

    log_event(
        logging.INFO,
        "selenium scrape completed",
        event="scrape.selenium.completed",
        engine=engine,
        candidate_count=len(urls),
    )
    return urls
