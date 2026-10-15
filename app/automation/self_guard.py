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

__all__ = [
    "own_pid",
    "foreground_pid",
    "foreground_window_title",
    "is_self_foreground",
    "describe_foreground",
]