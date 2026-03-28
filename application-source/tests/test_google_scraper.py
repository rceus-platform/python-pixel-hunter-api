"""Test module for Google Selenium scraper."""

import asyncio
import os

from app.core.logging import configure_logging
from app.services.engines.selenium_ext import scrape_engine_urls_with_selenium

# Configure logging to stdout for this test
configure_logging()


def test_google_selenium() -> None:
    """Test the Google Selenium scraper with mock proxy environment variables."""

    asyncio.run(_test_google_selenium())


async def _test_google_selenium() -> None:
    """Actual async logic for Google Selenium test."""

    print("Starting Google Selenium test with Mock Proxy...")
    # Set dummy proxy env vars to trigger the new logic
    os.environ["PROXY_HOST"] = "1.2.3.4"
    os.environ["PROXY_PORT"] = "8888"
    os.environ["PROXY_USER"] = "user"
    os.environ["PROXY_PASS"] = "pass"

    # Test query
    query = "cyberpunk city 4k wallpaper"

    # Run the scraper in a thread because it's blocking
    try:
        urls = await asyncio.to_thread(
            scrape_engine_urls_with_selenium, query, "google"
        )
        print(f"\nFinal candidate count: {len(urls)}")
    except Exception as exc:
        print(f"Test failed as expected with dummy proxy: {exc}")


if __name__ == "__main__":
    asyncio.run(test_google_selenium())
