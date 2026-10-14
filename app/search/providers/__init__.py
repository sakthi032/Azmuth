"""Search provider registry factory."""

from __future__ import annotations

from app.core.registry import Registry
from app.search.base import SearchProvider
from app.search.providers.duckduckgo import DuckDuckGoProvider
from app.search.providers.null import NullSearchProvider
from app.search.providers.searxng import SearXNGProvider


def build_search_registry() -> Registry[SearchProvider]:
    """Return the registry of available search providers.

    Registration order is not selection order — the SearchManager
    reads ``Settings.search_provider_order`` to decide which to try
    first and how to fall through.
    """
    registry: Registry[SearchProvider] = Registry("search")
    registry.register(NullSearchProvider.name, NullSearchProvider)
    registry.register(DuckDuckGoProvider.name, DuckDuckGoProvider)
    registry.register(SearXNGProvider.name, SearXNGProvider)
    return registry


__all__ = [
    "DuckDuckGoProvider",
    "NullSearchProvider",
    "SearXNGProvider",
    "build_search_registry",
]