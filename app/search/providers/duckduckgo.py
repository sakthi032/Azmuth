
"""DuckDuckGo HTML search provider.

Scrapes ``https://html.duckduckgo.com/html/`` — the no-JavaScript
version of DuckDuckGo search. No API key, no signup, no rate limit
beyond DDG's own anti-bot behaviour.

Fragility: DDG changes their HTML occasionally, and aggressive clients
get temporarily blocked. When that happens, ``search()`` returns an
empty list and logs a warning — the pipeline treats it as "no results"
and the model answers from its own knowledge. That is the correct
degraded behaviour.

This provider is intentionally modest. It exists so the search path
actually returns something without requiring the user to sign up for a
paid API. A production deployment would use Brave, SerpAPI, or an
equivalent with a real key.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request

from app.config.logging_setup import get_logger
from app.core.errors import ProviderError, ProviderUnavailableError
from app.search.base import SearchProvider
from app.search.models import SearchQuery, SearchResult

logger = get_logger("search.duckduckgo")

_ENDPOINT = "https://html.duckduckgo.com/html/"

#: DDG HTML result blocks look like:
#:   <a rel="nofollow" class="result__a" href="...">Title</a>
#:   <a class="result__snippet" href="...">Snippet text</a>
_RESULT_ANCHOR = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_SNIPPET_ANCHOR = re.compile(
    r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


class DuckDuckGoProvider(SearchProvider):
    """Free DDG HTML scraper. No API key."""

    name = "duckduckgo"

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def is_available(self) -> bool:
        # Config-only: this provider has no credentials to check.
        return True

    async def search(self, query: SearchQuery) -> list[SearchResult]:
        import asyncio

        return await asyncio.to_thread(self._search_sync, query)

    # -- internals ---------------------------------------------------------
    def _search_sync(self, query: SearchQuery) -> list[SearchResult]:
        params = {"q": query.text}
        if query.region:
            params["kl"] = query.region.lower()
        if query.language:
            # DDG uses kl for region and lang for locale hint.
            params.setdefault("kl", query.language.lower())
        url = f"{_ENDPOINT}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(
            url,
            headers={
                # DDG blocks the default urllib UA. Use a real browser UA.
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9,ta;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # 403/429 = rate-limited or blocked. Return nothing rather
            # than fail the whole turn.
            logger.warning(
                "duckduckgo returned HTTP %d", exc.code,
                extra={"event": "DDG_HTTP_ERROR", "status": exc.code},
            )
            return []
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(
                f"duckduckgo unreachable: {exc.reason}"
            ) from exc

        titles = _extract_matches(_RESULT_ANCHOR, body)
        snippets = _extract_matches(_SNIPPET_ANCHOR, body)

        results: list[SearchResult] = []
        for index, (href, title_html) in enumerate(titles):
            title = _clean_html(title_html)
            if not title or not href:
                continue
            snippet = (
                _clean_html(snippets[index][0]) if index < len(snippets) else ""
            )
            real_url = _unwrap_ddg_redirect(href)
            results.append(
                SearchResult(
                    title=title,
                    url=real_url,
                    snippet=snippet or "(no snippet)",
                    source=self.name,
                    rank=index,
                )
            )
            if len(results) >= query.max_results:
                break

        logger.info(
            "duckduckgo search completed",
            extra={
                "event": "DDG_SEARCH_COMPLETED",
                "query": query.text,
                "result_count": len(results),
            },
        )
        return results


def _extract_matches(pattern: re.Pattern[str], text: str) -> list[tuple[str, str]]:
    """Return ``[(group1, group2), ...]`` for every regex match."""
    out: list[tuple[str, str]] = []
    for match in pattern.finditer(text):
        if match.lastindex and match.lastindex >= 2:
            out.append((match.group(1), match.group(2)))
        elif match.lastindex == 1:
            out.append((match.group(1), ""))
    return out


def _clean_html(text: str) -> str:
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return _WHITESPACE.sub(" ", text).strip()


def _unwrap_ddg_redirect(href: str) -> str:
    """DDG wraps outbound URLs like ``/l/?uddg=<encoded>``. Unwrap them."""
    if href.startswith("//duckduckgo.com/l/") or href.startswith("/l/"):
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        target = qs.get("uddg", [None])[0]
        if target:
            return urllib.parse.unquote(target)
    if href.startswith("//"):
        return f"https:{href}"
    return href


__all__ = ["DuckDuckGoProvider"]
