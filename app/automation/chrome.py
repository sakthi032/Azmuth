
"""Chromium DevTools Protocol client.

Works against any Chromium-based browser: Chrome, Edge, Brave, Opera,
Vivaldi, Chromium itself. They all speak the same HTTP endpoints on
their own debug port.

The debug port is only open when the browser was launched with
``--remote-debugging-port=<port>``. See
:mod:`app.automation.browsers` for the profile registry.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError

logger = get_logger("automation.browser")


@dataclass(frozen=True, slots=True)
class BrowserTab:
    id: str
    title: str
    url: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "url": self.url}


# Backward-compatible alias.
ChromeTab = BrowserTab


class BrowserDevTools:
    """Talk to any Chromium browser over its DevTools HTTP endpoint."""

    def __init__(
        self,
        *,
        port: int = 9222,
        timeout: float = 3.0,
        browser_name: str = "browser",
    ) -> None:
        self._base_url = f"http://localhost:{port}"
        self._timeout = timeout
        self._browser_name = browser_name

    # -- introspection -----------------------------------------------------
    @property
    def browser_name(self) -> str:
        return self._browser_name

    @property
    def base_url(self) -> str:
        return self._base_url

    def is_available(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/json/version", timeout=self._timeout
            ) as response:
                return response.status == 200
        except Exception:
            return False

    def focus_tab(self, tab_id: str) -> None:
        """Bring a tab to the foreground.

        Uses the CDP ``/json/activate/<id>`` endpoint. This is a GET
        with no body; the response is the empty string on success.
        """
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/json/activate/{tab_id}",
                timeout=self._timeout,
            ) as response:
                if response.status >= 400:
                    raise AutomationError(
                        f"could not focus tab: HTTP {response.status}"
                    )
        except urllib.error.URLError as exc:
            raise AutomationError(f"could not focus tab: {exc}") from exc

    # -- read --------------------------------------------------------------
    def list_tabs(self) -> list[BrowserTab]:
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/json", timeout=self._timeout
            ) as response:
                targets = json.loads(response.read())
        except urllib.error.URLError as exc:
            raise AutomationError(
                f"{self._browser_name} DevTools is not reachable at "
                f"{self._base_url}. Launch the browser in debug mode first."
            ) from exc

        return [
            BrowserTab(
                id=t.get("id", ""),
                title=t.get("title", ""),
                url=t.get("url", ""),
            )
            for t in targets
            if t.get("type") == "page"
        ]

    # -- write -------------------------------------------------------------
    def close_tab(self, target_id: str) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/json/close/{target_id}",
                timeout=self._timeout,
            ) as response:
                return response.status == 200
        except urllib.error.URLError as exc:
            raise AutomationError(f"could not close tab: {exc}") from exc

    def close_tabs_matching(self, needle: str) -> list[str]:
        needle_lower = needle.lower()
        tabs = self.list_tabs()
        matches = [
            t for t in tabs
            if needle_lower in t.title.lower() or needle_lower in t.url.lower()
        ]
        if not matches:
            titles = ", ".join(repr(t.title) for t in tabs[:10]) or "(none)"
            raise AutomationError(
                f"no {self._browser_name} tab matched {needle!r}. "
                f"Open tabs: {titles}"
            )
        closed: list[str] = []
        for tab in matches:
            if self.close_tab(tab.id):
                closed.append(tab.id)
        return closed

    def open_tab(self, url: str) -> BrowserTab:
        """Open a new tab in the CDP-connected browser.

        Chrome requires the PUT method on ``/json/new`` (since v111).
        Using POST returns 405 and no tab is created.
        """
        request = urllib.request.Request(
            f"{self._base_url}/json/new?{urllib.parse.quote(url, safe='')}",
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:200]
            raise AutomationError(
                f"could not open tab: HTTP {exc.code} - {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AutomationError(f"could not open tab: {exc.reason}") from exc

        if not payload.get("id"):
            raise AutomationError(
                f"Chrome did not return a tab id for {url!r}"
            )
        return BrowserTab(
            id=payload.get("id", ""),
            title=payload.get("title", ""),
            url=payload.get("url", ""),
        )

    # -- tab lookup --------------------------------------------------------
    def find_tab(self, needle: str) -> BrowserTab | None:
        """Return the first tab whose title or url contains ``needle``."""
        needle_lower = needle.lower()
        for tab in self.list_tabs():
            if needle_lower in tab.title.lower() or needle_lower in tab.url.lower():
                return tab
        return None

    # -- JavaScript execution ---------------------------------------------
    def eval_js(self, tab_id: str, expression: str) -> Any:
        """Evaluate ``expression`` in the given tab and return its value.

        Requires the ``websocket-client`` package. The CDP endpoint is
        the same one DevTools itself uses. Never raises for a JS
        exception - the exception text is returned as the result so the
        caller can report it.
        """
        try:
            import websocket  # type: ignore[import-untyped]
        except ImportError as exc:
            raise AutomationError(
                "javascript execution requires the 'websocket-client' "
                "package. Install with: pip install websocket-client"
            ) from exc

        # Get the WebSocket debugger URL for this tab.
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/json", timeout=self._timeout
            ) as response:
                targets = json.loads(response.read())
        except urllib.error.URLError as exc:
            raise AutomationError(f"could not list tabs: {exc}") from exc

        ws_url: str | None = None
        for t in targets:
            if t.get("id") == tab_id and t.get("webSocketDebuggerUrl"):
                ws_url = t["webSocketDebuggerUrl"]
                break
        if ws_url is None:
            raise AutomationError(f"tab {tab_id} has no WebSocket debugger URL")

        command = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }
        try:
            # ``suppress_origin=True`` prevents websocket-client from
            # sending an Origin header. Chrome 111+ rejects WebSocket
            # handshakes whose Origin doesn't match the debug port, and
            # a handshake without an Origin is always accepted.
            ws = websocket.create_connection(
                ws_url,
                timeout=self._timeout,
                suppress_origin=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise AutomationError(f"could not connect to tab: {exc}") from exc

        try:
            ws.send(json.dumps(command))
            raw = ws.recv()
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AutomationError(f"malformed CDP response: {exc}") from exc

        # A JS exception is reported in result.exceptionDetails.
        result = payload.get("result", {})
        if "exceptionDetails" in result:
            text = result["exceptionDetails"].get("text", "javascript error")
            raise AutomationError(f"javascript error: {text}")
        return result.get("result", {}).get("value")

    def navigate_tab(self, tab_id: str, url: str) -> None:
        """Navigate an existing tab to a URL.

        Uses JavaScript location.href, which is a same-tab navigation.
        No new tab is created. No keystrokes are needed, so this works
        regardless of which window has OS focus.
        """
        import json as _json
        expression = (
            f"location.href = {_json.dumps(url)}; "
            f"'navigating to ' + location.href"
        )
        self.eval_js(tab_id, expression)


# Backward-compatible alias. Anything that imports ``ChromeDevTools``
# still works; it just talks to port 9222 by default.
ChromeDevTools = BrowserDevTools


__all__ = [
    "BrowserDevTools",
    "BrowserTab",
    "ChromeDevTools",
    "ChromeTab",
]
