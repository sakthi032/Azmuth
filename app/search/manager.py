
"""Search facade.

Wraps a provider and enforces the security convention that no external
content reaches the brain in a form that could impersonate instructions.
Its two responsibilities:

* resolve a provider from the registry by name,
* run a query and return a :class:`SearchResponse` that already contains a
  sanitised reference block ready for the prompt.

Callers (the brain) use :meth:`SearchManager.search` and never see raw
snippets in isolation - the response bundles structured results *and* the
single safe block.
"""

from __future__ import annotations

import time

from app.config.logging_setup import get_logger
from app.core.errors import (
    ProviderError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)
from app.core.registry import Registry
from app.search.base import SearchProvider
from app.search.models import SearchQuery, SearchResponse, SearchResult
from app.search.sanitizer import build_reference_block

logger = get_logger("search")


class SearchManager:
    """Provider selection and safe result handling."""

    def __init__(
        self,
        provider_name: str,
        registry: Registry[SearchProvider],
    ) -> None:
        self._provider_name = provider_name
        self._registry = registry
        self._provider: SearchProvider | None = None

    # -- introspection -----------------------------------------------------
    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def provider(self) -> SearchProvider:
        if self._provider is None:
            self._provider = self._registry.create(self._provider_name)
            logger.info(
                "search provider resolved",
                extra={
                    "event": "SEARCH_PROVIDER_READY",
                    "provider": self._provider.name,
                },
            )
        return self._provider

    def is_available(self) -> bool:
        try:
            return self.provider.is_available()
        except ProviderNotFoundError:
            return False
        except Exception:  # noqa: BLE001
            logger.exception(
                "availability check failed",
                extra={"event": "SEARCH_AVAILABILITY_FAILED"},
            )
            return False

    def reset(self) -> None:
        self._provider = None

    # -- search ------------------------------------------------------------
    async def search(self, query: SearchQuery) -> SearchResponse:
        """Run ``query`` and return a sanitised response.

        Never raises for an empty result set. Raises only for a genuinely
        unusable provider (unknown name, transport failure).
        """
        started = time.perf_counter()

        try:
            provider = self.provider
        except ProviderNotFoundError:
            raise
        if not provider.is_available():
            raise ProviderUnavailableError(
                f"search provider {provider.name!r} is not available"
            )

        try:
            raw = await provider.search(query)
        except (ProviderUnavailableError, ProviderNotFoundError):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "search failed",
                extra={"event": "SEARCH_FAILED", "provider": provider.name},
            )
            raise ProviderError(f"search failed: {exc}") from exc

        # Defensive: providers might return a list containing non-SearchResult
        # items (e.g. a plugin bug). Drop anything that is not the right type
        # rather than crashing the conversation.
        results: list[SearchResult] = []
        for item in raw:
            if isinstance(item, SearchResult):
                results.append(item)
            else:
                logger.warning(
                    "search provider returned unexpected item type",
                    extra={
                        "event": "SEARCH_BAD_ITEM",
                        "provider": provider.name,
                        "type": type(item).__name__,
                    },
                )

        # Rank by provider order but cap at the requested limit.
        results = results[: query.max_results]

        reference_block = build_reference_block(tuple(results))
        elapsed_ms = (time.perf_counter() - started) * 1000

        response = SearchResponse(
            query=query,
            results=tuple(results),
            provider=provider.name,
            reference_block=reference_block,
            has_reference=bool(reference_block),
            elapsed_ms=elapsed_ms,
        )
        logger.info(
            "search completed",
            extra={
                "event": "SEARCH_COMPLETED",
                "provider": provider.name,
                "result_count": len(results),
                "elapsed_ms": round(elapsed_ms, 3),
            },
        )
        return response


def build_search_manager(
    provider_name: str,
    *,
    registry: Registry[SearchProvider] | None = None,
) -> SearchManager:
    """Construct a :class:`SearchManager` with the default registry."""
    from app.search.providers import build_search_registry

    return SearchManager(
        provider_name,
        registry if registry is not None else build_search_registry(),
    )
