"""Window enumeration, focus, and state control."""

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
    is_minimized: bool = False
    is_maximized: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "title": self.title,
            "process_id": self.process_id,
            "process_name": self.process_name,
            "is_minimized": self.is_minimized,
            "is_maximized": self.is_maximized,
        }


_WNDENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
)

# ShowWindow constants
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOW = 5


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
                    is_minimized=bool(user32.IsIconic(hwnd)),
                    is_maximized=bool(user32.IsZoomed(hwnd)),
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
    """Bring a window to the foreground by its handle."""
    if not _is_windows():
        raise ProviderUnavailableError("window focus is Windows-only")

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if not user32.IsWindow(handle):
        raise AutomationError(f"window handle {handle} does not exist")

    if user32.IsIconic(handle):
        user32.ShowWindow(handle, SW_RESTORE)

    if user32.SetForegroundWindow(handle):
        return True

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


def set_window_state(handle: int, state: str) -> bool:
    """Set a window's state.

    ``state`` is one of: ``maximize``, ``minimize``, ``restore``, ``show``.
    Returns True on success. Uses ``ShowWindow`` which is what the
    taskbar buttons and title-bar buttons ultimately call.
    """
    if not _is_windows():
        raise ProviderUnavailableError("window state control is Windows-only")

    user32 = ctypes.windll.user32
    if not user32.IsWindow(handle):
        raise AutomationError(f"window handle {handle} does not exist")

    mapping = {
        "maximize": SW_MAXIMIZE,
        "minimize": SW_MINIMIZE,
        "restore": SW_RESTORE,
        "show": SW_SHOW,
    }
    key = state.strip().lower()
    cmd = mapping.get(key)
    if cmd is None:
        raise AutomationError(
            f"unknown window state {state!r}. Use one of: "
            f"{', '.join(sorted(mapping))}."
        )

    user32.ShowWindow(handle, cmd)

    # If we asked to maximize or restore, also bring to front so the
    # user sees the change.
    if key in ("maximize", "restore"):
        try:
            focus_window(handle)
        except Exception:  # noqa: BLE001
            pass

    return True


def _find_window(needle: str) -> WindowInfo:
    """Return the first visible window whose title contains ``needle``."""
    needle_lower = needle.lower()
    matches = [w for w in list_windows() if needle_lower in w.title.lower()]
    if not matches:
        titles = ", ".join(repr(w.title) for w in list_windows()[:15])
        raise AutomationError(
            f"no window title matched {needle!r}. Visible windows: {titles}"
        )
    return matches[0]


def focus_window_by_title(needle: str) -> bool:
    return focus_window(_find_window(needle).handle)


def set_window_state_by_title(needle: str, state: str) -> WindowInfo:
    target = _find_window(needle)
    set_window_state(target.handle, state)
    return target


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
        if identifier.strip().isdigit():
            return await asyncio.to_thread(focus_window, int(identifier))
        return await asyncio.to_thread(focus_window_by_title, identifier)

    async def set_window_state(self, identifier: str, state: str) -> dict[str, Any]:
        import asyncio
        if identifier.strip().isdigit():
            handle = int(identifier)
            await asyncio.to_thread(set_window_state, handle, state)
            return {"handle": handle, "state": state}
        info = await asyncio.to_thread(set_window_state_by_title, identifier, state)
        return {"handle": info.handle, "title": info.title, "state": state}


__all__ = [
    "WindowController",
    "WindowInfo",
    "focus_window",
    "focus_window_by_title",
    "list_windows",
    "set_window_state",
    "set_window_state_by_title",
]