
"""Write the multi-browser bridge file, then verify it parses.

Run:  py -m scripts.write_bridge
"""

from pathlib import Path
import shutil

CONTENT = '''"""WebSocket bridge to the Azmuth browser extension.

Multiple browsers can connect at once. Each reports its own browser
name in the ``hello`` message; the bridge routes commands to the
correct browser by name.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError

logger = get_logger("automation.chrome_bridge")

_PORT = 9280


def _get_serve():
    import websockets
    try:
        from websockets.asyncio.server import serve as new_serve
        return new_serve
    except ImportError:
        pass
    try:
        return websockets.serve
    except AttributeError as exc:
        raise AutomationError(
            f"no compatible websockets.serve in {websockets.__version__}"
        ) from exc


class ChromeBridge:
    """WebSocket server; each connected browser is a separate client."""

    def __init__(self, *, port: int = _PORT) -> None:
        self._port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._clients: dict[str, Any] = {}
        self._ready = threading.Event()
        self._pending: dict[str, asyncio.Future] = {}
        self._bind_error: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="azmuth-browser-bridge", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:  # noqa: BLE001
            pass

    @property
    def bind_error(self) -> str | None:
        return self._bind_error

    # -- queries -----------------------------------------------------------
    def connected_browsers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients.keys()))

    def is_available(self, browser: str | None = None) -> bool:
        if browser is None:
            return bool(self._clients)
        return browser.lower() in self._clients

    # -- command dispatch --------------------------------------------------
    def send_command(
        self,
        browser: str,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        key = browser.lower()
        if key not in self._clients:
            connected = ", ".join(self.connected_browsers()) or "(none)"
            raise AutomationError(
                f"{browser} is not connected to the Azmuth bridge. "
                f"Connected browsers: {connected}. Load the extension in "
                f"{browser} and reload it."
            )
        if self._loop is None:
            raise AutomationError("bridge loop is not running")

        cmd_id = uuid.uuid4().hex
        payload = {"id": cmd_id, "action": action, **(params or {})}
        future = asyncio.run_coroutine_threadsafe(
            self._send_and_wait(key, payload, timeout), self._loop
        )
        try:
            return future.result(timeout=timeout + 1.0)
        except Exception as exc:  # noqa: BLE001
            raise AutomationError(f"browser command failed: {exc}") from exc

    # -- internals ---------------------------------------------------------
    def _run(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
        except Exception as exc:  # noqa: BLE001
            self._bind_error = str(exc)
            logger.exception("bridge loop failed")

    async def _serve(self) -> None:
        serve = _get_serve()

        async def handler(ws):
            browser_key: str | None = None
            try:
                async for message in ws:
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("type") == "hello":
                        name = str(payload.get("browser") or "unknown").lower()
                        self._clients[name] = ws
                        browser_key = name
                        self._ready.set()
                        logger.info(
                            "browser connected",
                            extra={
                                "event": "BROWSER_EXT_CONNECTED",
                                "browser": name,
                            },
                        )
                        continue
                    cmd_id = payload.get("id")
                    if cmd_id and cmd_id in self._pending:
                        fut = self._pending.pop(cmd_id)
                        if not fut.done():
                            fut.set_result(payload.get("result", {}))
            except Exception:  # noqa: BLE001
                pass
            finally:
                if browser_key is not None:
                    self._clients.pop(browser_key, None)
                    logger.info(
                        "browser disconnected",
                        extra={
                            "event": "BROWSER_EXT_DISCONNECTED",
                            "browser": browser_key,
                        },
                    )
                if not self._clients:
                    self._ready.clear()

        try:
            async with serve(handler, "127.0.0.1", self._port):
                logger.info(
                    "browser bridge listening",
                    extra={"event": "BROWSER_BRIDGE_LISTENING",
                           "port": self._port},
                )
                await asyncio.Future()
        except OSError as exc:
            self._bind_error = f"bind failed: {exc}"
            logger.exception(
                "browser bridge could not bind port",
                extra={"event": "BROWSER_BRIDGE_BIND_FAILED"},
            )

    async def _send_and_wait(
        self, browser_key: str, payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        ws = self._clients.get(browser_key)
        if ws is None:
            raise AutomationError("browser disconnected before send")
        fut: asyncio.Future = self._loop.create_future()
        self._pending[payload["id"]] = fut
        try:
            await ws.send(json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            self._pending.pop(payload["id"], None)
            raise AutomationError(f"could not send: {exc}") from exc
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(payload["id"], None)
            raise AutomationError("browser command timed out")


_bridge: ChromeBridge | None = None


def get_bridge() -> ChromeBridge:
    global _bridge
    if _bridge is None:
        _bridge = ChromeBridge()
        _bridge.start()
    return _bridge


__all__ = ["ChromeBridge", "get_bridge"]
'''

target = Path("app/automation/chrome_extension_bridge.py")
target.write_text(CONTENT, encoding="utf-8")
print(f"Wrote {target} ({target.stat().st_size} bytes)")

# Clear pycache
for cache in Path("app/automation").rglob("__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)
    print(f"Cleared {cache}")

# Verify the new method is present.
import ast
src = target.read_text(encoding="utf-8")
tree = ast.parse(src)
methods = [
    n.name for n in ast.walk(tree)
    if isinstance(n, ast.FunctionDef)
]
assert "connected_browsers" in methods, (
    "connected_browsers missing - write failed"
)
print("Verified: connected_browsers is present")

# py -m scripts\write_bridge