
"""Search provider registry factory."""

from __future__ import annotations

from app.core.registry import Registry
from app.search.base import SearchProvider
from app.search.providers.duckduckgo import DuckDuckGoProvider
from app.search.providers.null import NullSearchProvider


def build_search_registry() -> Registry[SearchProvider]:
    registry: Registry[SearchProvider] = Registry("search")
    registry.register(NullSearchProvider.name, NullSearchProvider)
    registry.register(DuckDuckGoProvider.name, DuckDuckGoProvider)
    return registry


__all__ = [
    "DuckDuckGoProvider",
    "NullSearchProvider",
    "build_search_registry",
]
