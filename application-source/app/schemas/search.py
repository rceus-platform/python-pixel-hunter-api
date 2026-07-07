"""Pydantic schemas for search engine data models."""

from typing import Optional

from pydantic import BaseModel, Field


class ImageCandidate(BaseModel):
    """Candidate payload shape including provenance metadata."""

    url: str
    source: str
    search_engine: str


class ImageSearchResult(BaseModel):
    """Result payload shape for a validated image candidate."""

    url: str
    width: int
    height: int
    size: str
    cover: str
    title: str
    source: str
    search_engine: str
    note: str
    description: str
    highlights: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    phash: Optional[str] = None
    unverified: Optional[bool] = False


class SearchResponse(BaseModel):
    """Envelope for search results."""

    results: list[ImageSearchResult]
    query_hash: str
    engine_counts: dict[str, int]
