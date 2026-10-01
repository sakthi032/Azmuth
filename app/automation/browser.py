"""Default-browser URL opening.

Two safety rules:

* **Only http and https URLs are accepted.** ``file://``, ``javascript:``,
  ``data:``, ``vbscript:``, and OS custom protocol handlers are rejected.
  A URL from a search result or the LLM is untrusted; opening it must not
  become a code-execution or local-file-exfiltration vector.
* **No shell.** :mod:`webbrowser` uses the OS's default-browser
  mechanism, which does not require a shell.
"""

from __future__ import annotations

import asyncio
import urllib.parse
import webbrowser

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError

logger = get_logger("automation.browser")

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class BrowserController:
    """Open URLs in the user's default browser."""

    async def open_url(self, url: str, *, new_window: bool = False) -> None:
        parsed = _validate_url(url)

        def _blocking() -> bool:
            try:
                if new_window:
                    return webbrowser.open_new(parsed.geturl())
                return webbrowser.open(parsed.geturl())
            except webbrowser.Error as exc:
                raise AutomationError(
                    f"no default browser could be opened: {exc}"
                ) from exc

        opened = await asyncio.to_thread(_blocking)
        if not opened:
            raise AutomationError(
                "the system did not report a browser as opened"
            )
        logger.info(
            "url opened",
            extra={
                "event": "URL_OPENED",
                "scheme": parsed.scheme,
                "host": parsed.hostname or "",
            },
        )


def _validate_url(url: str) -> urllib.parse.ParseResult:
    if not url or not url.strip():
        raise AutomationError("url must be non-empty")
    parsed = urllib.parse.urlparse(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise AutomationError(
            f"url scheme {scheme!r} is not allowed; "
            f"only {sorted(_ALLOWED_SCHEMES)} are accepted"
        )
    if not parsed.hostname:
        raise AutomationError(f"url {url!r} has no host component")
    return parsed