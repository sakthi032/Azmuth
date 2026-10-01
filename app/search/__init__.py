
"""Web-search subsystem public API."""

from app.search.base import SearchProvider
from app.search.manager import SearchManager, build_search_manager
from app.search.models import SearchQuery, SearchResponse, SearchResult
from app.search.needs import keyword_hits, needs_search
from app.search.providers import NullSearchProvider, build_search_registry
from app.search.sanitizer import (
    CLOSE_DELIMITER,
    OPEN_DELIMITER,
    build_reference_block,
    sanitize_snippet,
)

__all__ = [
    "CLOSE_DELIMITER",
    "OPEN_DELIMITER",
    "NullSearchProvider",
    "SearchManager",
    "SearchProvider",
    "SearchQuery",
    "SearchResponse",
    "SearchResult",
    "build_reference_block",
    "build_search_manager",
    "build_search_registry",
    "keyword_hits",
    "needs_search",
    "sanitize_snippet",
]
