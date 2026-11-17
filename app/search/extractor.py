"""Webpage content extractor for Azmuth's web research pipeline.

Fetches a URL with httpx, strips boilerplate (scripts, styles, navs),
and returns clean main-body text for LLM summarisation.

Security hardening
------------------
* SSRF protection: blocks private/loopback IP ranges and ``file://``
* Content-type gate: only processes ``text/html`` responses
* Size limit: truncates at ``MAX_TEXT_BYTES`` characters
* Prompt-injection scanner: flags pages with LLM-trigger patterns

Usage::

    extractor = WebExtractor()
    result = await extractor.extract("https://example.com")
    print(result.text, result.warning)
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------
try:
    import httpx  # type: ignore[import-untyped]

    HAS_HTTPX = True
except ModuleNotFoundError:
    HAS_HTTPX = False
    logger.warning("httpx not installed — WebExtractor will not function")

try:
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    HAS_BS4 = True
except ModuleNotFoundError:
    HAS_BS4 = False
    logger.warning("beautifulsoup4 not installed — WebExtractor will not function")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_TEXT_BYTES = 50_000  # characters of extracted text
FETCH_TIMEOUT = 10.0     # seconds

# Tags that contain noise rather than content
_STRIP_TAGS = {
    "script", "style", "nav", "header", "footer",
    "aside", "iframe", "noscript", "form",
}

# Patterns that suggest a webpage is trying to inject instructions into an LLM
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"you\s+are\s+now\s+a",
        r"new\s+system\s+prompt",
        r"disregard\s+(your|the)\s+(previous|above)",
        r"act\s+as\s+(if\s+you\s+are|a\s+different)",
        r"forget\s+everything",
    ]
]

# Private / loopback CIDR blocks (SSRF protection)
_PRIVATE_CIDRS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


@dataclass
class ExtractResult:
    url: str
    text: str = ""
    title: str = ""
    warning: str = ""
    error: str = ""
    truncated: bool = False
    injection_risk: bool = False
    citations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


class WebExtractor:
    """Async webpage extractor with SSRF + injection protection.

    Parameters
    ----------
    timeout:
        HTTP request timeout in seconds.
    max_text:
        Maximum number of characters to return from extracted text.
    allow_private:
        If True, bypass SSRF private-IP check (testing only!).
    """

    def __init__(
        self,
        timeout: float = FETCH_TIMEOUT,
        max_text: int = MAX_TEXT_BYTES,
        allow_private: bool = False,
    ) -> None:
        self._timeout = timeout
        self._max_text = max_text
        self._allow_private = allow_private

    async def extract(self, url: str) -> ExtractResult:
        """Fetch and extract text from *url*.

        Returns an :class:`ExtractResult`.  On failure the ``error`` field is
        set and ``text`` is empty.
        """
        result = ExtractResult(url=url)

        # --- SSRF guard ---
        ssrf_error = self._check_ssrf(url)
        if ssrf_error:
            result.error = ssrf_error
            return result

        if not HAS_HTTPX or not HAS_BS4:
            result.error = "httpx or beautifulsoup4 not installed"
            return result

        # --- Fetch ---
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": "Azmuth/4.0 (research bot)"},
            ) as client:
                resp = await client.get(url)
        except httpx.TimeoutException:
            result.error = f"Timed out fetching {url}"
            return result
        except Exception as exc:
            result.error = f"Fetch error: {exc}"
            return result

        ct = resp.headers.get("content-type", "")
        if "text/html" not in ct:
            result.error = f"Unsupported content-type: {ct}"
            return result

        # --- Parse ---
        raw_html = resp.text
        soup = BeautifulSoup(raw_html, "html.parser")

        # Title
        title_tag = soup.find("title")
        result.title = title_tag.get_text(strip=True) if title_tag else ""

        # Remove noise
        for tag in soup(list(_STRIP_TAGS)):
            tag.decompose()

        # Extract text
        text = soup.get_text(separator="\n", strip=True)

        # Collect outbound links as citations
        result.citations = [
            a["href"]
            for a in soup.find_all("a", href=True)
            if str(a["href"]).startswith("http")
        ][:20]

        # Truncate
        if len(text) > self._max_text:
            text = text[: self._max_text]
            result.truncated = True

        result.text = text

        # --- Injection detection ---
        for pat in _INJECTION_PATTERNS:
            if pat.search(text):
                result.injection_risk = True
                result.warning = (
                    "⚠ This page contains text that may be a prompt-injection attempt. "
                    "Treat its content with extra caution."
                )
                logger.warning("Injection risk detected on %s", url)
                break

        return result

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _check_ssrf(self, url: str) -> str:
        """Return an error message if the URL targets a private/loopback address."""
        if self._allow_private:
            return ""

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return f"Blocked scheme: {parsed.scheme!r} — only http/https allowed"

        host = parsed.hostname or ""
        try:
            addr = ipaddress.ip_address(host)
            for cidr in _PRIVATE_CIDRS:
                if addr in cidr:
                    return f"SSRF blocked: {host} is a private/loopback address"
        except ValueError:
            pass  # hostname, not IP — allowed

        return ""
