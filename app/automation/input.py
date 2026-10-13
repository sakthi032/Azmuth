
"""Synthetic keyboard and mouse input via ctypes.

Uses ``user32.SendInput`` directly. No external dependency. Works on
every Python 3 version, does not fight with pyautogui's packaging, and
does not have the circular-import problem that pyautogui 0.9.54 has on
Python 3.13.

The primitives here are what the tools ``type_text`` and ``press_keys``
wrap. They are gated at the tool layer by ``ADVANCED_AUTOMATION`` and
require creator mode plus confirmation.
"""

from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError, ProviderUnavailableError

logger = get_logger("automation.input")


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_ABSOLUTE = 0x8000

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004

_WHEEL_DELTA = 120


# --------------------------------------------------------------------------
# Structures required by SendInput
# --------------------------------------------------------------------------
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


# --------------------------------------------------------------------------
# Virtual key codes
# --------------------------------------------------------------------------
_VK_CODES: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B, "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C,
    "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, "winleft": 0x5B, "lwin": 0x5B,
    "winright": 0x5C, "rwin": 0x5C,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63,
    "num4": 0x64, "num5": 0x65, "num6": 0x66, "num7": 0x67,
    "num8": 0x68, "num9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "separator": 0x6C,
    "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "numlock": 0x90,
    "scrolllock": 0x91,
    "shiftleft": 0xA0, "shiftright": 0xA1,
    "ctrlleft": 0xA2, "ctrlright": 0xA3,
    "altleft": 0xA4, "altright": 0xA5,
}


def _vk_for(key: str) -> int | None:
    k = key.strip().lower()
    if k in _VK_CODES:
        return _VK_CODES[k]
    if len(k) == 1:
        return ord(k.upper())
    return None


def _is_windows() -> bool:
    import sys
    return sys.platform == "win32"


# --------------------------------------------------------------------------
# Controller
# --------------------------------------------------------------------------
class InputController:
    """Keyboard and mouse synthesis via ``user32.SendInput``.

    On non-Windows platforms :meth:`is_available` returns ``False`` and
    every operation raises :class:`ProviderUnavailableError`.
    """

    def __init__(self) -> None:
        self._user32 = None
        if _is_windows():
            try:
                self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            except OSError:
                self._user32 = None

    def is_available(self) -> bool:
        return self._user32 is not None

    # -- internals ---------------------------------------------------------
    def _require(self) -> None:
        if self._user32 is None:
            raise ProviderUnavailableError(
                "keyboard and mouse control are only available on Windows."
            )

    def _send(self, *inputs: _INPUT) -> None:
        assert self._user32 is not None
        n = len(inputs)
        arr = (_INPUT * n)(*inputs)
        sent = self._user32.SendInput(n, arr, ctypes.sizeof(_INPUT))
        if sent != n:
            err = ctypes.get_last_error()
            raise AutomationError(
                f"SendInput failed (sent {sent}/{n}, WinError {err})"
            )

    @staticmethod
    def _mouse_input(
        flags: int, dx: int = 0, dy: int = 0, data: int = 0
    ) -> _INPUT:
        inp = _INPUT()
        inp.type = _INPUT_MOUSE
        inp.mi = _MOUSEINPUT(dx, dy, data, flags, 0, None)
        return inp

    @staticmethod
    def _key_input(vk: int, flags: int = 0) -> _INPUT:
        inp = _INPUT()
        inp.type = _INPUT_KEYBOARD
        inp.ki = _KEYBDINPUT(vk, 0, flags, 0, None)
        return inp

    # -- mouse -------------------------------------------------------------
    async def move_mouse(
        self, x: int, y: int, *, duration: float = 0.0
    ) -> None:
        del duration  # instant move; smooth animation is not needed here
        self._require()
        screen_w = self._user32.GetSystemMetrics(0)
        screen_h = self._user32.GetSystemMetrics(1)
        if screen_w <= 1 or screen_h <= 1:
            raise AutomationError("could not determine screen size")
        ax = int(x * 65535 / (screen_w - 1))
        ay = int(y * 65535 / (screen_h - 1))
        self._send(
            self._mouse_input(
                _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE, ax, ay
            )
        )
        logger.info(
            "mouse moved",
            extra={"event": "MOUSE_MOVED", "x": x, "y": y},
        )

    async def click(
        self,
        x: int | None = None,
        y: int | None = None,
        *,
        button: str = "left",
    ) -> None:
        self._require()
        if button not in {"left", "right", "middle"}:
            raise AutomationError(f"unsupported mouse button: {button}")
        if x is not None and y is not None:
            await self.move_mouse(x, y)

        down, up = {
            "left": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
            "right": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
            "middle": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
        }[button]
        self._send(self._mouse_input(down))
        self._send(self._mouse_input(up))
        logger.info(
            "mouse clicked",
            extra={"event": "MOUSE_CLICKED", "x": x, "y": y, "button": button},
        )

    async def double_click(
        self, x: int | None = None, y: int | None = None
    ) -> None:
        await self.click(x, y)
        await asyncio.sleep(0.05)
        await self.click()

    async def scroll(self, amount: int) -> None:
        self._require()
        self._send(
            self._mouse_input(_MOUSEEVENTF_WHEEL, data=amount * _WHEEL_DELTA)
        )

    # -- keyboard ----------------------------------------------------------
    async def type_text(self, text: str, *, interval: float = 0.02) -> None:
        """Type a string, one character at a time.

        Uses ``KEYEVENTF_UNICODE``, so arbitrary characters - Tamil,
        Cyrillic, emoji - work without any keyboard-layout mapping.
        """
        self._require()
        if not text:
            return
        for ch in text:
            cp = ord(ch)
            self._send(self._key_input(cp, _KEYEVENTF_UNICODE))
            self._send(
                self._key_input(cp, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)
            )
            if interval:
                await asyncio.sleep(interval)
        logger.info(
            "text typed",
            extra={"event": "TEXT_TYPED", "length": len(text)},
        )

    async def press_keys(self, keys: str) -> None:
        """Press a key or a chord.

        ``"enter"`` presses one key. ``"ctrl+s"`` presses a chord. Key
        names follow the standard spellings: ``"win"``, ``"ctrl"``,
        ``"alt"``, ``"shift"``, ``"enter"``, ``"esc"``, ``"tab"``,
        arrow keys, F1-F12.
        """
        self._require()
        parts = [p.strip().lower() for p in keys.split("+") if p.strip()]
        if not parts:
            raise AutomationError("no keys specified")

        vks: list[int] = []
        for p in parts:
            vk = _vk_for(p)
            if vk is None:
                raise AutomationError(f"unknown key name: {p!r}")
            vks.append(vk)

        # Press in order, release in reverse - correct for any chord.
        for vk in vks:
            self._send(self._key_input(vk))
        for vk in reversed(vks):
            self._send(self._key_input(vk, _KEYEVENTF_KEYUP))

        logger.info(
            "keys pressed",
            extra={"event": "KEYS_PRESSED", "keys": keys},
        )

    async def send_keys(self, keys: str) -> None:
        """Alias for :meth:`press_keys`, kept for the automation layer's
        existing call site in ``WindowsAutomationImpl``.
        """
        await self.press_keys(keys)

    # -- screen ------------------------------------------------------------
    async def screen_size(self) -> tuple[int, int]:
        self._require()
        return (
            self._user32.GetSystemMetrics(0),
            self._user32.GetSystemMetrics(1),
        )

    async def position(self) -> tuple[int, int]:
        self._require()
        point = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        return (point.x, point.y)


__all__ = ["InputController"]

