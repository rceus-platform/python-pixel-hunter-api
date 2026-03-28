"""Service for constructing and managing image result objects."""

from app.schemas.search import ImageCandidate, ImageSearchResult
from app.utilities.image_utils import derive_title_from_url


def build_unverified_result(candidate: ImageCandidate) -> ImageSearchResult:
    """Build an unverified result payload when strict validation fails."""

    image_url = candidate.url
    return ImageSearchResult(
        url=image_url,
        width=0,
        height=0,
        size="",
        cover=image_url,
        title=derive_title_from_url(image_url),
        source=candidate.source,
        search_engine=candidate.search_engine,
        unverified=True,
        note="",
        description="",
        highlights=[],
        tags=[],
    )
