"""Refuse keyboard/mouse input when the target is Azmuth's own process.

Typing into Azmuth's own chat input would create a feedback loop: the
synthetic keystrokes become a new user message, which triggers another
turn, which types again. Clicking Azmuth's own UI has similar problems
— the model can accidentally trigger "New conversation", "Logout", etc.

This module is a runtime guard, not a prompt rule. Prompt rules can be
ignored by the model; this cannot.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_OWN_PID = os.getpid()


def own_pid() -> int:
    return _OWN_PID


def foreground_pid() -> int:
    """PID of the process owning the current foreground window."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def foreground_window_title() -> str:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def is_self_foreground() -> bool:
    """True if the currently focused window belongs to Azmuth."""
    pid = foreground_pid()
    return pid != 0 and pid == _OWN_PID


def describe_foreground() -> dict[str, object]:
    return {
        "pid": foreground_pid(),
        "own_pid": _OWN_PID,
        "title": foreground_window_title(),
        "is_self": is_self_foreground(),
    }

import threading

_lock = threading.Lock()

def acquire_automation_lock(timeout: float = 10.0) -> bool:
    """Acquire the global automation lock.

    Every input-emitting tool (type_text, click_at, press_keys,
    type_into_element) must hold this lock for the duration of its
    action. This prevents two tool calls — one from Azmuth, one from
    a smoke test — from interleaving keystrokes.
    """
    return _lock.acquire(timeout=timeout)


def release_automation_lock() -> None:
    try:
        _lock.release()
    except RuntimeError:
        pass


def is_automation_locked() -> bool:
    return _lock.locked()

import time as _time
import threading as _threading

# ── Last non-self foreground window (for capture) ─────────────────
_last_non_self_lock = _threading.Lock()
_last_non_self_hwnd: int = 0
_last_non_self_title: str = ""
_poller_thread: _threading.Thread | None = None
_poller_stop = _threading.Event()


def _poller_loop(interval: float = 0.5) -> None:
    """Poll the foreground window; remember the most recent non-self one."""
    global _last_non_self_hwnd, _last_non_self_title
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    while not _poller_stop.is_set():
        try:
            hwnd = int(user32.GetForegroundWindow() or 0)
            if hwnd and hwnd != 0:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if int(pid.value) != _OWN_PID:
                    length = user32.GetWindowTextLengthW(hwnd)
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    with _last_non_self_lock:
                        _last_non_self_hwnd = hwnd
                        _last_non_self_title = buf.value
        except Exception:
            pass
        _poller_stop.wait(interval)


def start_foreground_poller() -> None:
    """Start the poller once. Safe to call repeatedly."""
    global _poller_thread
    if _poller_thread is not None and _poller_thread.is_alive():
        return
    _poller_stop.clear()
    _poller_thread = _threading.Thread(
        target=_poller_loop,
        name="azmuth-fg-poller",
        daemon=True,
    )
    _poller_thread.start()


def last_non_self_foreground() -> tuple[int, str]:
    """(hwnd, title) of the most recent non-Azmuth foreground window."""
    with _last_non_self_lock:
        return _last_non_self_hwnd, _last_non_self_title


def clear_last_non_self() -> None:
    global _last_non_self_hwnd, _last_non_self_title
    with _last_non_self_lock:
        _last_non_self_hwnd = 0
        _last_non_self_title = ""


__all__ = [
    "own_pid",
    "foreground_pid",
    "foreground_window_title",
    "is_self_foreground",
    "describe_foreground",
    "acquire_automation_lock",
    "release_automation_lock",
    "is_automation_locked",
    "start_foreground_poller",
    "last_non_self_foreground",
    "clear_last_non_self",
]