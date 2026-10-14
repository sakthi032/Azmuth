"""Search facade.

Wraps one or more providers behind a tiered fallback chain and enforces
the security convention that no external content reaches the brain in a
form that could impersonate instructions. Responsibilities:

* resolve providers from the registry by name,
* try them in configured order, treating empty results as failure,
* return a SearchResponse that already carries a sanitised reference block.

Callers (the brain) use SearchManager.search() and never see raw snippets
in isolation - the response bundles structured results *and* the single
safe block.
"""

from __future__ import annotations

import time

from app.config.logging_setup import get_logger
from app.core.errors import (
    ProviderError,
    ProviderNotFoundError,
)
from app.core.registry import Registry
from app.search.base import SearchProvider
from app.search.models import SearchQuery, SearchResponse

logger = get_logger("search")


def _default_fallback_order(primary: str) -> tuple[str, ...]:
    """Build the fallback chain: primary first, then settings order minus primary."""
    try:
        from app.config.settings import load_settings

        settings = load_settings(create_directories=False)
        order = tuple(settings.search_provider_order or ())
    except Exception:  # noqa: BLE001
        order = ()

    seen: list[str] = []
    for name in (primary, *order):
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


class SearchManager:
    """Provider selection with tiered fallback and safe result handling."""

    def __init__(
        self,
        provider_name: str,
        registry: Registry[SearchProvider],
        *,
        fallback_order: tuple[str, ...] | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._registry = registry
        self._provider: SearchProvider | None = None
        self._fallback_order = (
            fallback_order
            if fallback_order is not None
            else _default_fallback_order(provider_name)
        )

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

    @property
    def fallback_order(self) -> tuple[str, ...]:
        return self._fallback_order

    def reset(self) -> None:
        self._provider = None

    # -- health ------------------------------------------------------------
    def is_available(self) -> bool:
        """Best-effort sync check of the *primary* provider only.

        Fallback providers are probed lazily during :meth:`search`.
        """
        try:
            result = self.provider.is_available()
        except ProviderNotFoundError:
            return False
        except Exception:  # noqa: BLE001
            logger.exception(
                "availability check failed",
                extra={"event": "SEARCH_AVAILABILITY_FAILED"},
            )
            return False

        # Some providers may implement is_available as async by mistake;
        # a coroutine object is truthy but means nothing here.
        if hasattr(result, "__await__"):
            return True  # assume reachable; search() will confirm
        return bool(result)

    # -- search ------------------------------------------------------------
    async def search(self, query: SearchQuery) -> SearchResponse:
        """Try providers in fallback order until one returns results."""
        started = time.perf_counter()
        last_error: Exception | None = None
        last_provider_used: str = "none"

        for name in self._fallback_order:
            try:
                provider = self._registry.create(name)
            except ProviderNotFoundError:
                logger.debug(
                    "provider %r not registered — skipping", name,
                    extra={"event": "SEARCH_PROVIDER_MISSING", "provider": name},
                )
                continue

            try:
                response = await provider.search(query)
            except ProviderError as exc:
                last_error = exc
                logger.warning(
                    "search via %s failed: %s", name, exc,
                    extra={
                        "event": "SEARCH_PROVIDER_FAILED",
                        "provider": name,
                        "error": str(exc),
                    },
                )
                continue

            if response.results:
                logger.info(
                    "search via %s returned %d results",
                    name, len(response.results),
                    extra={
                        "event": "SEARCH_SUCCESS",
                        "provider": name,
                        "count": len(response.results),
                    },
                )
                return response

            last_provider_used = name
            logger.warning(
                "search via %s returned 0 results — falling through",
                name,
                extra={"event": "SEARCH_EMPTY", "provider": name},
            )

        # Every provider failed or returned empty.
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        error_message = (
            str(last_error)
            if last_error is not None
            else "all search providers returned no results"
        )
        logger.warning(
            "search exhausted all providers",
            extra={"event": "SEARCH_EXHAUSTED", "error": error_message},
        )

        return SearchResponse(
            query=query,
            results=(),
            provider=last_provider_used,
            reference_block="",
            has_reference=False,
            elapsed_ms=elapsed_ms,
            error=error_message,
        )


def build_search_manager(
    provider_name: str,
    *,
    registry: Registry[SearchProvider] | None = None,
    fallback_order: tuple[str, ...] | None = None,
) -> SearchManager:
    """Construct a :class:`SearchManager` with the default registry."""
    from app.search.providers import build_search_registry

    return SearchManager(
        provider_name,
        registry if registry is not None else build_search_registry(),
        fallback_order=fallback_order,
    )