
"""Window enumeration and window management."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError, ProviderUnavailableError

logger = get_logger("automation.windows_mgmt")


@dataclass(frozen=True, slots=True)
class WindowInfo:
    handle: int
    title: str
    process_id: int
    process_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "title": self.title,
            "process_id": self.process_id,
            "process_name": self.process_name,
        }


_WNDENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
)


def _is_windows() -> bool:
    return sys.platform == "win32"


def list_windows() -> list[WindowInfo]:
    if not _is_windows():
        return []

    user32 = ctypes.windll.user32
    results: list[WindowInfo] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if not title:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            results.append(
                WindowInfo(
                    handle=int(hwnd),
                    title=title,
                    process_id=int(pid.value),
                )
            )
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        user32.EnumWindows(_WNDENUMPROC(callback), 0)
    except Exception:  # noqa: BLE001
        logger.exception("EnumWindows failed")
        return []

    return results


def focus_window(handle: int) -> bool:
    """Bring a window to the foreground by its handle.

    Uses the AttachThreadInput workaround because Windows blocks
    SetForegroundWindow calls from a background process. The workaround
    temporarily joins our input queue with the foreground thread's, so
    our SetForegroundWindow call is treated as originating from the
    foreground.

    Returns True if the target window is now the foreground window.
    """
    if not _is_windows():
        raise ProviderUnavailableError("window focus is Windows-only")

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    SW_RESTORE = 9

    if not user32.IsWindow(handle):
        raise AutomationError(f"window handle {handle} does not exist")

    # Restore if minimized.
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, SW_RESTORE)

    # Fast path: try the direct call.
    if user32.SetForegroundWindow(handle):
        return True

    # Slow path: attach to the current foreground thread's input queue.
    fg = user32.GetForegroundWindow()
    our_thread = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)

    attached = False
    try:
        if fg_thread and fg_thread != our_thread:
            attached = bool(user32.AttachThreadInput(our_thread, fg_thread, True))
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
        return user32.GetForegroundWindow() == handle
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, fg_thread, False)


def focus_window_by_title(needle: str) -> bool:
    """Focus the first visible window whose title contains ``needle``.

    Case-insensitive. Raises if no match, so the caller can list the
    available titles rather than silently failing.
    """
    needle_lower = needle.lower()
    matches = [w for w in list_windows() if needle_lower in w.title.lower()]
    if not matches:
        titles = ", ".join(repr(w.title) for w in list_windows()[:15])
        raise AutomationError(
            f"no window title matched {needle!r}. Visible windows: {titles}"
        )
    return focus_window(matches[0].handle)


class WindowController:
    """Async wrapper used by WindowsAutomationImpl."""

    def __init__(self) -> None:
        self._available = _is_windows()

    def is_available(self) -> bool:
        return self._available

    async def list_windows(self) -> list[WindowInfo]:
        import asyncio
        return await asyncio.to_thread(list_windows)

    async def focus_window(self, identifier: str) -> bool:
        import asyncio
        # Accept either a numeric handle or a window-title substring.
        if identifier.strip().isdigit():
            return await asyncio.to_thread(focus_window, int(identifier))
        return await asyncio.to_thread(focus_window_by_title, identifier)


__all__ = [
    "WindowController",
    "WindowInfo",
    "focus_window",
    "focus_window_by_title",
    "list_windows",
]