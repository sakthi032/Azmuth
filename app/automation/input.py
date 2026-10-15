"""Synthetic keyboard and mouse input via ctypes.

Uses ``user32.SendInput`` for keystrokes and the Win32 clipboard for
bulk text entry. No external dependency. Works on every Python 3
version, does not fight with pyautogui's packaging, and does not have
the circular-import problem pyautogui 0.9.54 has on Python 3.13.

Text entry uses the clipboard by default: it is atomic (one WM_PASTE
event instead of N keystrokes) and works on every Windows app,
including Win11's RichEdit-based Notepad which drops rapid
KEYEVENTF_UNICODE keystrokes under load.

The primitives here are what the tools type_text and press_keys wrap.
They are gated at the tool layer by ADVANCED_AUTOMATION and require
creator mode plus confirmation.
"""

from __future__ import annotations

import asyncio
import ctypes
import time
from ctypes import wintypes

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError, ProviderUnavailableError

logger = get_logger("automation.input")

# ctypes.wintypes lacks a couple of Win32 typedefs we need.
if not hasattr(wintypes, "HGLOBAL"):
    wintypes.HGLOBAL = ctypes.c_void_p
if not hasattr(wintypes, "HANDLE"):
    wintypes.HANDLE = ctypes.c_void_p


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

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002

_VK_CONTROL = 0x11
_VK_V = 0x56


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
    def _key_input(vk: int = 0, flags: int = 0, scan: int = 0) -> _INPUT:
        """Build a KEYBDINPUT.

        Virtual-key input: pass ``vk``, leave ``scan=0``.
        Unicode input: pass ``vk=0``, set ``flags |= KEYEVENTF_UNICODE``,
        and put the codepoint in ``scan``. Windows requires exactly this
        layout — see the KEYBDINPUT docs.
        """
        inp = _INPUT()
        inp.type = _INPUT_KEYBOARD
        inp.ki = _KEYBDINPUT(vk, scan, flags, 0, None)
        return inp

    def _flush_modifiers(self) -> None:
        """Release every modifier key. Cheap insurance against stuck state."""
        for name in ("shift", "ctrl", "alt", "win"):
            vk = _VK_CODES.get(name)
            if vk is not None:
                try:
                    self._send(self._key_input(vk, _KEYEVENTF_KEYUP))
                except Exception:
                    pass

    # -- mouse -------------------------------------------------------------
    async def move_mouse(
        self, x: int, y: int, *, duration: float = 0.0
    ) -> None:
        del duration
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

    # -- keyboard: raw -----------------------------------------------------
    async def press_keys(self, keys: str) -> None:
        """Press a key or a chord. e.g. 'enter', 'ctrl+s', 'alt+tab'."""
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

        try:
            for vk in vks:
                self._send(self._key_input(vk))
            for vk in reversed(vks):
                self._send(self._key_input(vk, _KEYEVENTF_KEYUP))
        finally:
            self._flush_modifiers()

        logger.info(
            "keys pressed",
            extra={"event": "KEYS_PRESSED", "keys": keys},
        )

    async def send_keys(self, keys: str) -> None:
        """Alias for :meth:`press_keys`."""
        await self.press_keys(keys)

    # -- keyboard: text ----------------------------------------------------
    async def type_text(
        self,
        text: str,
        *,
        interval: float = 0.05,
        key_hold: float = 0.012,
        use_clipboard: bool = True,
        target_hwnd: int | None = None,
    ) -> None:
        """Type text into the focused (or explicitly targeted) window.

        Default path: clipboard paste with focus enforcement. If paste
        raises, this method raises — it never silently falls back to
        keystrokes, because keystrokes after a partial paste corrupt
        the result.

        Set ``use_clipboard=False`` to force the per-character keystroke
        path (useful for password fields or clipboard-hostile targets).
        Set ``target_hwnd`` to pin the window the paste must land in;
        the routine will verify that window has focus before sending
        Ctrl+V, and raise if it cannot be focused.
        """
        self._require()
        if not text:
            return

        self._flush_modifiers()

        if use_clipboard:
            await self._paste_via_clipboard(text, target_hwnd=target_hwnd)
            return

        # Per-character fallback (used when the caller explicitly opts in).
        for ch in text:
            cp = ord(ch)
            self._send(self._key_input(0, _KEYEVENTF_UNICODE, scan=cp))
            if key_hold:
                await asyncio.sleep(key_hold)
            self._send(
                self._key_input(
                    0, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, scan=cp
                )
            )
            if interval:
                await asyncio.sleep(interval)

        logger.info(
            "text typed (keystroke path)",
            extra={"event": "TEXT_TYPED", "length": len(text)},
        )


    # -- clipboard ---------------------------------------------------------
    async def _paste_via_clipboard(
        self, text: str, *, target_hwnd: int | None = None
    ) -> None:
        """Write text to clipboard, ensure target is foreground, send Ctrl+V.

        Raises AutomationError if focus cannot be verified on the target.
        Never falls back silently — the caller decides.
        """
        saved_text = await asyncio.to_thread(self._write_clipboard, text)

        try:
            # Step 1: ensure the target window has foreground.
            if target_hwnd is not None:
                already = self._get_foreground_hwnd() == target_hwnd
                if not already:
                    # Only force when necessary — SetForegroundWindow
                    # resets internal keyboard focus and breaks pasting
                    # into child controls (Notepad's RichEdit, etc.).
                    focused = await asyncio.to_thread(
                        self._force_foreground, target_hwnd
                    )
                    if not focused:
                        raise AutomationError(
                            f"could not focus target window "
                            f"hwnd={target_hwnd}. Another process may "
                            f"own the foreground queue."
                        )
                    await asyncio.sleep(0.10)

                # Verify focus either way.
                actual = self._get_foreground_hwnd()
                if actual != target_hwnd:
                    actual_title = self._get_window_title(actual)
                    raise AutomationError(
                        f"focus verification failed: expected "
                        f"hwnd={target_hwnd}, got hwnd={actual} "
                        f"(title={actual_title!r}). Paste aborted."
                    )

            # Diagnostic — log keyboard focus before paste. If this is 0
            # or points to the top-level window (not a child), Ctrl+V will
            # be silently dropped.
            logger.info(
                "paste attempt",
                extra={
                    "event": "PASTE_ATTEMPT",
                    "foreground": self._get_foreground_hwnd(),
                    "keyboard_focus": self._get_keyboard_focus(),
                    "target": target_hwnd,
                },
            )
            # Step 2: send Ctrl+V.
            self._send(self._key_input(_VK_CONTROL))
            await asyncio.sleep(0.02)
            self._send(self._key_input(_VK_V))
            await asyncio.sleep(0.02)
            self._send(self._key_input(_VK_V, _KEYEVENTF_KEYUP))
            await asyncio.sleep(0.02)
            self._send(self._key_input(_VK_CONTROL, _KEYEVENTF_KEYUP))

            logger.info(
                "text pasted",
                extra={
                    "event": "TEXT_PASTED",
                    "length": len(text),
                    "target_hwnd": target_hwnd,
                },
            )
        finally:
            if saved_text is not None:
                await asyncio.sleep(0.20)
                try:
                    await asyncio.to_thread(
                        self._write_clipboard, saved_text
                    )
                except Exception:
                    logger.warning(
                        "clipboard restore failed (paste already succeeded)",
                        extra={"event": "CLIPBOARD_RESTORE_FAILED"},
                    )

    @staticmethod
    def _write_clipboard(payload: str) -> str | None:
        """Write payload to the Win32 clipboard. Returns previous text.

        All restype/argtypes are declared explicitly — without them,
        64-bit handles get truncated to 32-bit ints by ctypes defaults.
        """
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE

        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL

        # Retry OpenClipboard — another process may briefly hold it.
        opened = False
        for _ in range(20):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            raise OSError("OpenClipboard failed after 1s of retries")

        saved: str | None = None
        try:
            if user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
                h = user32.GetClipboardData(_CF_UNICODETEXT)
                if h:
                    ptr = kernel32.GlobalLock(h)
                    if ptr:
                        try:
                            saved = ctypes.wstring_at(ptr)
                        finally:
                            kernel32.GlobalUnlock(h)

            user32.EmptyClipboard()
            encoded = (payload + "\0").encode("utf-16-le")
            h_mem = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(encoded))
            if not h_mem:
                raise OSError("GlobalAlloc failed")
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                kernel32.GlobalFree(h_mem)
                raise OSError("GlobalLock failed")
            try:
                ctypes.memmove(ptr, encoded, len(encoded))
            finally:
                kernel32.GlobalUnlock(h_mem)
            if not user32.SetClipboardData(_CF_UNICODETEXT, h_mem):
                kernel32.GlobalFree(h_mem)
                raise OSError("SetClipboardData failed")
            # Success — clipboard owns h_mem now; do NOT free it.
        finally:
            user32.CloseClipboard()

        return saved

    # -- focus helpers -----------------------------------------------------
    @staticmethod
    def _get_foreground_hwnd() -> int:
        try:
            return int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    @staticmethod
    def _get_window_title(hwnd: int) -> str:
        if not hwnd:
            return ""
        try:
            user32 = ctypes.windll.user32
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value
        except Exception:
            return ""

    @staticmethod
    def _force_foreground(hwnd: int) -> bool:
        """Bring a window to foreground and verify.

        Uses AttachThreadInput because SetForegroundWindow is restricted:
        a process that does not currently own the foreground queue cannot
        simply steal focus. Attaching our thread to the foreground
        thread's input queue lifts that restriction for the duration of
        the attach.
        """
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND

        if user32.SetForegroundWindow(hwnd) and \
           user32.GetForegroundWindow() == hwnd:
            return True

        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        our_thread = kernel32.GetCurrentThreadId()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        if not fg_thread or fg_thread == our_thread:
            return user32.GetForegroundWindow() == hwnd

        attached = False
        try:
            attached = bool(
                user32.AttachThreadInput(our_thread, fg_thread, True)
            )
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(our_thread, fg_thread, False)

        return user32.GetForegroundWindow() == hwnd

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
    
    def click_sync(self, x: int, y: int, *, button: str = "left") -> None:
        """Synchronous click for worker-thread callers.

        Same as :meth:`click` but without the async wrapping. Safe to call
        from threads that don't have a running event loop (e.g. the UIA
        worker thread).
        """
        self._require()
        if button not in {"left", "right", "middle"}:
            raise AutomationError(f"unsupported mouse button: {button}")
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
        down, up = {
            "left": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
            "right": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
            "middle": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
        }[button]
        self._send(self._mouse_input(down))
        self._send(self._mouse_input(up))
        logger.info(
            "mouse clicked (sync)",
            extra={"event": "MOUSE_CLICKED", "x": x, "y": y, "button": button},
        )

    @staticmethod
    def _get_keyboard_focus() -> int:
        """HWND that currently holds keyboard focus (may be a child)."""
        try:
            user32 = ctypes.windll.user32
            user32.GetFocus.argtypes = []
            user32.GetFocus.restype = wintypes.HWND
            return int(user32.GetFocus() or 0)
        except Exception:
            return 0

__all__ = ["InputController"]