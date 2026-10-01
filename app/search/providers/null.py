
"""Null search provider - the Phase 1 default."""

from __future__ import annotations

from app.search.base import SearchProvider
from app.search.models import SearchQuery, SearchResult


class NullSearchProvider(SearchProvider):
    """Always available, always returns no results."""

    name = "null"

    def is_available(self) -> bool:
        return True

    async def search(self, query: SearchQuery) -> list[SearchResult]:
        del query
        return []
