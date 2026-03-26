"""API behavior smoke tests for health, validation, and download endpoints."""

from typing import cast

import httpx
from _pytest.monkeypatch import MonkeyPatch
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    """Health endpoint returns status payload."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_search_rejects_empty_query():
    """Search endpoint rejects blank query values."""
    response = client.get("/search", params={"query": "   "})
    assert response.status_code == 400


def test_search_rejects_unsupported_engine():
    """Search endpoint rejects unknown engine names."""
    response = client.get(
        "/search", params={"query": "emma watson", "engines": "duckduckgo"}
    )
    assert response.status_code == 400


def test_download_rejects_non_http_url():
    """Download endpoint rejects non-HTTP URL schemes."""
    response = client.get("/download", params={"url": "file:///etc/passwd"})
    assert response.status_code == 400


def test_search_returns_limited_sorted_results(monkeypatch: MonkeyPatch):
    """Search returns ranked results and honors the requested result limit."""

    async def fake_collect_engine_urls(  # pylint: disable=redefined-outer-name
        query: str, engines: list[str], client: httpx.AsyncClient, pages: int = 1
    ) -> list[str]:
        _ = (query, engines, client, pages)
        return [
            "https://img.example/1.jpg",
            "https://img.example/2.jpg",
            "https://img.example/3.jpg",
        ]

    # Test double mirrors production signature intentionally.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    async def fake_evaluate_image_candidate(  # pylint: disable=redefined-outer-name
        client: httpx.AsyncClient,
        image_url: str,
        min_width: int,
        min_height: int,
        orientation: str,
        dedupe: bool,
    ) -> dict[str, str | int]:
        _ = (client, min_width, min_height, orientation, dedupe)
        mapping = {
            "https://img.example/1.jpg": {
                "url": image_url,
                "width": 1200,
                "height": 900,
            },
            "https://img.example/2.jpg": {
                "url": image_url,
                "width": 2000,
                "height": 1200,
            },
            "https://img.example/3.jpg": {"url": image_url, "width": 800, "height": 800},
        }
        return mapping[image_url]

    monkeypatch.setattr(main_module, "collect_engine_urls", fake_collect_engine_urls)
    monkeypatch.setattr(
        main_module, "evaluate_image_candidate", fake_evaluate_image_candidate
    )

    response = client.get(
        "/search",
        params={
            "query": "emma stone",
            "engines": "bing,google",
            "limit": 2,
            "remove_duplicates": "false",
        },
    )

    assert response.status_code == 200
    payload = cast(dict[str, list[dict[str, str | int]]], response.json())
    assert "results" in payload
    assert len(payload["results"]) == 2
    assert payload["results"][0]["url"] == "https://img.example/2.jpg"
    assert payload["results"][1]["url"] == "https://img.example/1.jpg"


def test_search_deduplicates_results_by_phash(monkeypatch: MonkeyPatch):
    """Search removes perceptual duplicates when remove_duplicates is enabled."""

    async def fake_collect_engine_urls(  # pylint: disable=redefined-outer-name
        query: str, engines: list[str], client: httpx.AsyncClient, pages: int = 1
    ) -> list[str]:
        _ = (query, engines, client, pages)
        return [
            "https://img.example/a.jpg",
            "https://img.example/b.jpg",
            "https://img.example/c.jpg",
        ]

    # Test double mirrors production signature intentionally.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    async def fake_evaluate_image_candidate(  # pylint: disable=redefined-outer-name
        client: httpx.AsyncClient,
        image_url: str,
        min_width: int,
        min_height: int,
        orientation: str,
        dedupe: bool,
    ) -> dict[str, str | int]:
        _ = (client, min_width, min_height, orientation, dedupe)
        mapping = {
            "https://img.example/a.jpg": {
                "url": image_url,
                "width": 1800,
                "height": 1000,
                "phash": "abc123",
            },
            "https://img.example/b.jpg": {
                "url": image_url,
                "width": 1700,
                "height": 1000,
                "phash": "abc123",
            },
            "https://img.example/c.jpg": {
                "url": image_url,
                "width": 1900,
                "height": 1000,
                "phash": "def456",
            },
        }
        return mapping[image_url]

    monkeypatch.setattr(main_module, "collect_engine_urls", fake_collect_engine_urls)
    monkeypatch.setattr(
        main_module, "evaluate_image_candidate", fake_evaluate_image_candidate
    )

    response = client.get(
        "/search",
        params={"query": "emma stone", "engines": "all", "remove_duplicates": "true"},
    )

    assert response.status_code == 200
    payload = cast(dict[str, list[dict[str, str | int]]], response.json())
    assert len(payload["results"]) == 2
    assert all("phash" not in item for item in payload["results"])


def test_search_allow_unverified_includes_failed_candidates(monkeypatch: MonkeyPatch):
    """Search appends unverified candidates when allow_unverified is enabled."""

    async def fake_collect_engine_urls(  # pylint: disable=redefined-outer-name
        query: str, engines: list[str], client: httpx.AsyncClient, pages: int = 1
    ) -> list[str]:
        _ = (query, engines, client, pages)
        return [
            "https://img.example/v.jpg",
            "https://img.example/u1.jpg",
            "https://img.example/u2.jpg",
        ]

    # Test double mirrors production signature intentionally.
    # pylint: disable=too-many-arguments,too-many-positional-arguments,redefined-outer-name
    async def fake_evaluate_image_candidate(
        client: httpx.AsyncClient,
        image_url: str,
        min_width: int,
        min_height: int,
        orientation: str,
        dedupe: bool,
    ) -> dict[str, str | int] | None:
        _ = (client, min_width, min_height, orientation, dedupe)
        if image_url == "https://img.example/v.jpg":
            return {"url": image_url, "width": 2000, "height": 1200}
        return None

    monkeypatch.setattr(main_module, "collect_engine_urls", fake_collect_engine_urls)
    monkeypatch.setattr(
        main_module, "evaluate_image_candidate", fake_evaluate_image_candidate
    )

    response = client.get(
        "/search",
        params={
            "query": "emma stone",
            "engines": "bing,google",
            "allow_unverified": "true",
            "limit": 3,
        },
    )

    assert response.status_code == 200
    payload = cast(dict[str, list[dict[str, str | int | bool]]], response.json())
    assert len(payload["results"]) == 3
    assert payload["results"][0]["url"] == "https://img.example/v.jpg"
    assert payload["results"][1]["url"] == "https://img.example/u1.jpg"
    assert payload["results"][2]["url"] == "https://img.example/u2.jpg"
    assert payload["results"][1]["unverified"] is True
    assert payload["results"][2]["unverified"] is True


def test_normalize_candidate_url_trims_serialized_payload_tail():
    """URL normalization should drop serialized JSON/HTML fragments after image URL."""
    polluted = (
        "https://i0.wp.com/redheaddates.com/wp-content/uploads/2020/09/"
        "EmmaStone-1-scaled.jpg?ssl=1&quot;,&quot;fileSizeInBytes&quot;:671061"
    )
    cleaned = main_module.normalize_candidate_url(polluted)
    assert cleaned == (
        "https://i0.wp.com/redheaddates.com/wp-content/uploads/2020/09/"
        "EmmaStone-1-scaled.jpg?ssl=1"
    )
