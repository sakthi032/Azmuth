
"""The search provider interface."""

from __future__ import annotations

from abc import abstractmethod

from app.core.interfaces import Provider
from app.search.models import SearchQuery, SearchResult


class SearchProvider(Provider):
    """Base class for every search backend.

    Providers are async because real ones do network I/O. Implementations
    must not raise for an empty result set - return an empty list. Genuine
    transport failures should raise ProviderError or
    ProviderUnavailableError; the manager wraps both.

    Contract for security: providers return raw data. Sanitisation is
    the manager's job. Providers must not try to render templates, prefix
    instructions, or format anything for the LLM.
    """

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[SearchResult]:
        """Run the query and return results in rank order."""
