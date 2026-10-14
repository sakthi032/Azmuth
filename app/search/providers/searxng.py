"""SearXNG search provider — self-hosted metasearch aggregating 70+ engines."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from app.core.errors import ProviderError
from app.search.base import SearchProvider
from app.search.models import SearchQuery, SearchResponse, SearchResult

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8888"
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_LANGUAGE = "en"


def _build_reference_block(results: tuple[SearchResult, ...]) -> str:
    """Build the reference block for downstream prompt injection.

    Prefers ``app.search.sanitizer.build_reference_block`` when available;
    otherwise renders a simple numbered list the model can cite.
    """
    try:
        from app.search.sanitizer import build_reference_block  # type: ignore
        return build_reference_block(results)
    except (ImportError, AttributeError, TypeError):
        pass

    if not results:
        return ""
    lines = ["References:"]
    for i, r in enumerate(results, start=1):
        lines.append(f"[{i}] {r.title} — {r.url}")
    return "\n".join(lines)


class SearXNGProvider(SearchProvider):
    """Query a self-hosted SearXNG instance's JSON API."""

    name = "searxng"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        language: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("SEARXNG_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = float(
            timeout
            if timeout is not None
            else os.getenv("SEARXNG_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT))
        )
        self.language = (
            language or os.getenv("SEARXNG_LANGUAGE") or _DEFAULT_LANGUAGE
        ).lower()
        self.api_key = api_key or os.getenv("SEARXNG_API_KEY") or None

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 3.0)) as client:
                resp = await client.get(f"{self.base_url}/")
            return resp.status_code == 200
        except (httpx.RequestError, httpx.TimeoutException):
            return False

    async def search(self, query: SearchQuery) -> SearchResponse:
        started = time.perf_counter()

        params: dict[str, Any] = {
            "q": query.text,
            "format": "json",
            "language": query.language or self.language,
            "safesearch": 1 if query.safe_search else 0,
        }

        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/search",
                    params=params,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"SearXNG timed out after {self.timeout}s at {self.base_url}"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"SearXNG unreachable at {self.base_url}: {exc}"
            ) from exc

        if resp.status_code == 403:
            raise ProviderError(
                "SearXNG returned 403 — enable 'json' in search.formats "
                "of settings.yml and restart."
            )
        if resp.status_code >= 400:
            raise ProviderError(f"SearXNG returned HTTP {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                "SearXNG returned non-JSON — 'json' likely missing from "
                "search.formats."
            ) from exc

        results = self._parse(data, max_results=query.max_results)
        reference_block = _build_reference_block(results)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        return SearchResponse(
            query=query,
            results=results,
            provider=self.name,
            reference_block=reference_block,
            has_reference=bool(reference_block),
            elapsed_ms=elapsed_ms,
            error=None,
        )

    @staticmethod
    def _parse(
        data: dict[str, Any], max_results: int | None = None
    ) -> tuple[SearchResult, ...]:
        raw = data.get("results") or []
        out: list[SearchResult] = []
        for idx, item in enumerate(raw):
            if max_results is not None and len(out) >= max_results:
                break
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue
            snippet = " ".join((item.get("content") or "").split())
            out.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source=item.get("engine") or "searxng",
                    rank=idx,
                )
            )
        return tuple(out)