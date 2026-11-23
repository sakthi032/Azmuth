"""Composite tools that combine multiple primitives into one atomic action.

These tools exist to prevent the LLM from entering a multi-round loop for
common patterns like "open app and type text". Each composite tool handles
the full sequence internally and returns a single result.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from app.automation.base import WindowsAutomation
from app.automation.input import InputController
from app.core.errors import AutomationError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


class TypeIntoAppTool(Tool):
    """Open an application, wait for its window, and type text via clipboard paste.

    Preferred for 'open X and type Y' requests. Handles the full sequence:
    1. Launch (or refocus) the application.
    2. Wait for its window to be ready (up to wait_timeout seconds).
    3. Focus the window.
    4. Paste the text via clipboard (pyperclip) or fall back to keystroke typing.

    This prevents the multi-round focus/type/focus loop that the LoopGuard catches.
    """

    name: ClassVar[str] = "type_into_app"
    description: ClassVar[str] = (
        "Open an application, wait until its window is ready, focus it, and "
        "type text into it in one atomic step. Preferred for 'open X and type Y' "
        "requests — avoids the multi-step open/focus/type loop. The app alias "
        "must be an allowlisted name (e.g. 'notepad', 'wordpad'). Uses clipboard "
        "paste for reliable text delivery. If the app is already open, it focuses "
        "the existing window instead of relaunching."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "Allowlist alias of the application, e.g. 'notepad'.",
            },
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4096,
                "description": "Text to type/paste into the application.",
            },
            "wait_timeout": {
                "type": "number",
                "minimum": 1,
                "maximum": 30,
                "description": "Seconds to wait for the window to appear (default 10).",
            },
        },
        "required": ["app", "text"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(
        self,
        automation: WindowsAutomation,
        input_ctl: InputController,
    ) -> None:
        self._automation = automation
        self._input = input_ctl

    def is_available(self) -> bool:
        return self._automation.is_available() and self._input.is_available()

    async def execute(
        self,
        *,
        app: str,
        text: str,
        wait_timeout: float = 10.0,
    ) -> dict[str, Any]:
        from app.automation.self_guard import is_self_foreground
        from app.automation.windows_mgmt import focus_window, list_windows, wait_for_window

        # -----------------------------------------------------------------
        # 1. Launch or find existing window
        # -----------------------------------------------------------------
        window_handle: int | None = None
        needle = app.lower()

        # Check if window already exists
        existing = [w for w in await asyncio.to_thread(list_windows) if needle in w.title.lower()]
        if existing:
            window_handle = existing[0].handle
        else:
            try:
                await self._automation.open_application(app)
            except AutomationError as exc:
                if "already" in str(exc).lower():
                    # App reported already open — scan for window
                    wins = [w for w in await asyncio.to_thread(list_windows) if needle in w.title.lower()]
                    if wins:
                        window_handle = wins[0].handle
                else:
                    raise

        # -----------------------------------------------------------------
        # 2. Wait for window readiness
        # -----------------------------------------------------------------
        if window_handle is None:
            # Try multiple needles: alias, alias capitalised, common Notepad variants
            needles = [app, app.capitalize(), "Notepad", "Untitled"]
            win_info = None
            for n in needles:
                try:
                    win_info = await asyncio.to_thread(wait_for_window, n, wait_timeout)
                    break
                except AutomationError:
                    continue

            if win_info is None:
                visible = ", ".join(
                    repr(w.title)
                    for w in await asyncio.to_thread(list_windows)
                    if w.title
                )[:200]
                raise AutomationError(
                    f"type_into_app: window for {app!r} did not appear within "
                    f"{wait_timeout}s. Visible windows: {visible}"
                )
            window_handle = win_info.handle

        # -----------------------------------------------------------------
        # 3. Focus the window
        # -----------------------------------------------------------------
        try:
            await asyncio.to_thread(focus_window, window_handle)
        except Exception as exc:
            raise AutomationError(
                f"type_into_app: could not focus {app!r} window (handle={window_handle}): {exc}. "
                f"Try set_window_state to restore it first."
            ) from exc

        await asyncio.sleep(0.3)

        # -----------------------------------------------------------------
        # 4. Self-guard check
        # -----------------------------------------------------------------
        if is_self_foreground():
            raise AutomationError(
                f"input blocked: Azmuth window is foreground after focusing {app!r}. "
                f"The focus did not transfer to the target application. "
                f"Try click_at on the target window's coordinates manually."
            )

        # -----------------------------------------------------------------
        # 5. Paste via clipboard or fall back to keystroke typing
        # -----------------------------------------------------------------
        method = "keystrokes"
        try:
            import pyperclip  # type: ignore[import-untyped]
            await asyncio.to_thread(pyperclip.copy, text)
            await self._input.press_keys("ctrl+v")
            method = "clipboard"
        except (ImportError, Exception):
            await self._input.type_text(text)

        return {
            "app": app,
            "typed": len(text),
            "window_handle": window_handle,
            "method": method,
        }


__all__ = ["TypeIntoAppTool"]
