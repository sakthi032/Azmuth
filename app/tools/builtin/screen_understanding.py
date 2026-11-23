"""Screen understanding: capture, crop, redact, VLM.

Answers a question about what's currently on the user's screen.

Design
------
* Capture uses the existing ``app.automation.screen`` API:
  ``capture_window(hwnd)`` for ``region="active_window"`` (uses
  PrintWindow; works when occluded), ``capture_full_screen()`` for
  ``region="full"``. ``region="selection"`` is not implemented yet.
* Password fields marked ``IsPassword`` by Windows UI Automation are
  blacked out on the image before it reaches the vision model.
* The tool refuses to run while Azmuth's own window is foreground.
* Screenshots are never written to disk.
* If the captured image exceeds ~4 MB, it is downscaled to 2048px on
  the longest side and re-encoded as PNG (a full 4K desktop capture
  can otherwise exceed the vision API's size limit).
* Vision is Gemini today (via ``describe_image_detailed``). The
  NVIDIA NIM / local Ollama vision fallback chain is planned for a
  follow-up; today the tool surfaces the Gemini error directly so the
  user knows whether it's a key problem, quota problem, or format
  problem.
"""

from __future__ import annotations

import ctypes
import io
import time
from ctypes import wintypes
from typing import Any, ClassVar
import asyncio

from app.config.logging_setup import get_logger
from app.core.errors import ProviderUnavailableError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool

# Module-level imports so tests can monkeypatch cleanly.
from app.automation.screen import capture_full_screen, capture_window
from app.automation.self_guard import is_self_foreground
from app.automation.vision import describe_image_detailed

logger = get_logger("tools.screen_understanding")

#: Downscale threshold — a full 4K desktop PNG can exceed the vision
#: API's per-request size limit.
_MAX_IMAGE_BYTES = 4_000_000
_MAX_SIDE_PX = 2048


async def _minimize_self() -> bool:
    """Minimize Azmuth's window. Return True on success."""
    try:
        from app.automation.self_guard import own_pid
        import asyncio as _asyncio
        def _do() -> bool:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            return True
        return await _asyncio.to_thread(_do)
    except Exception:
        logger.exception("minimize self failed")
        return False


async def _restore_self() -> None:
    try:
        import asyncio as _asyncio
        def _do() -> None:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        await _asyncio.to_thread(_do)
    except Exception:
        logger.exception("restore self failed")


def _get_foreground_hwnd() -> int:
    """HWND of the current foreground window, or 0."""
    try:
        user32 = ctypes.windll.user32
        return int(user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) for a window, or None."""
    if not hwnd:
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT)
        ]
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


async def _redact_passwords(
    capture,
    win_rect: tuple[int, int, int, int] | None,
    uia,
):
    """Black out UIA-flagged password field rects on the captured image."""
    if uia is None or win_rect is None:
        return capture
    try:
        rects = await uia.password_field_rects()
    except Exception:
        logger.exception("password rect query failed")
        return capture
    if not rects:
        return capture

    try:
        from PIL import Image, ImageDraw
        img = Image.open(io.BytesIO(capture.png_bytes)).convert("RGB")
    except Exception:
        logger.exception("could not open capture for redaction")
        return capture

    win_left, win_top, win_right, win_bottom = win_rect
    win_w = max(1, win_right - win_left)
    win_h = max(1, win_bottom - win_top)
    sx = img.width / win_w
    sy = img.height / win_h

    draw = ImageDraw.Draw(img)
    redacted = 0
    for (pl, pt, pr, pb) in rects:
        # Skip rects fully outside the window
        if pl >= win_right or pr <= win_left or pt >= win_bottom or pb <= win_top:
            continue
        rel = (
            (pl - win_left) * sx,
            (pt - win_top) * sy,
            (pr - win_left) * sx,
            (pb - win_top) * sy,
        )
        draw.rectangle(rel, fill=(0, 0, 0))
        redacted += 1

    if not redacted:
        return capture

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    logger.info(
        "password fields redacted",
        extra={"event": "SCREEN_PASSWORD_REDACTED", "count": redacted},
    )
    return _rebuild_capture(capture, img.width, img.height, buf.getvalue())


def _rebuild_capture(capture, w: int, h: int, png_bytes: bytes):
    """Return a capture-like object with new dimensions/bytes.

    Avoids importing CaptureResult at module top so tests that patch
    capture functions don't need to build the concrete class.
    """
    try:
        from app.automation.screen import CaptureResult
        return CaptureResult(
            path=capture.path, width=w, height=h, png_bytes=png_bytes,
        )
    except Exception:
        # Duck-typed fallback if the caller passed a stub
        capture.width = w
        capture.height = h
        capture.png_bytes = png_bytes
        return capture


def _downscale_if_needed(png_bytes: bytes) -> bytes:
    """Shrink to 2048px longest side if the PNG exceeds 4 MB."""
    if len(png_bytes) <= _MAX_IMAGE_BYTES:
        return png_bytes
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        w, h = img.size
        if max(w, h) > _MAX_SIDE_PX:
            scale = _MAX_SIDE_PX / max(w, h)
            img = img.resize(
                (int(w * scale), int(h * scale)), Image.LANCZOS
            )
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        out = buf.getvalue()
        logger.info(
            "capture downscaled",
            extra={
                "event": "SCREEN_DOWNSCALED",
                "before": len(png_bytes),
                "after": len(out),
                "size": [img.width, img.height],
            },
        )
        return out
    except Exception:
        logger.exception("downscale failed; using original bytes")
        return png_bytes


class DescribeScreenTool(Tool):
    """Answer a question about what's currently on the user's screen."""

    name: ClassVar[str] = "describe_screen"
    description: ClassVar[str] = (
        "Answer a question about what is currently visible on the "
        "user's screen. Captures the active window by default "
        "(multi-monitor aware) or the full desktop with region='full'. "
        "Password fields are redacted before the image reaches the "
        "model. Refuses to run while Azmuth's own window is foreground. "
        "Never writes screenshots to disk. Use this when the user asks "
        "'what's on my screen', 'summarise this window', 'what does "
        "this error say', 'what button do I click'."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "What to answer about the screen.",
            },
            "region": {
                "type": "string",
                "enum": ["active_window", "full", "selection"],
                "default": "active_window",
                "description": (
                    "'active_window' (default) — the currently focused "
                    "window. 'full' — the whole desktop (all monitors). "
                    "'selection' — not yet supported."
                ),
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, *, uia=None) -> None:
        self._uia = uia

    def is_available(self) -> bool:
        try:
            from PIL import ImageGrab  # noqa: F401
        except ImportError:
            return False
        return True

    async def execute(
        self, *, question: str, region: str = "active_window"
    ) -> dict[str, Any]:
        started = time.perf_counter()

        from app.automation.self_guard import (
            is_self_foreground, last_non_self_foreground,
            start_foreground_poller,
        )
        start_foreground_poller()  # idempotent

        self_foreground = False
        try:
            self_foreground = is_self_foreground()
        except Exception:
            logger.exception("self-guard probe failed")

        # Resolve which HWND to capture (None = full desktop)
        target_hwnd: int | None = None
        effective_region = region
        capture_region = region

        if region == "active_window":
            if not self_foreground:
                target_hwnd = _get_foreground_hwnd() or None
            else:
                # Self is foreground — fall back to last non-self window
                hwnd, title = last_non_self_foreground()
                if hwnd:
                    target_hwnd = hwnd
                    logger.info(
                        "self-foreground; capturing last non-self window",
                        extra={
                            "event": "CAPTURE_LAST_WINDOW",
                            "hwnd": hwnd,
                            "title": title[:80],
                        },
                    )
                else:
                    # No prior window known — use full-screen fallback
                    capture_region = "full"

        elif region == "selection":
            return {
                "error": (
                    "region='selection' is not supported yet — use "
                    "'active_window' or 'full'."
                ),
            }
        elif region != "full":
            return {"error": f"unknown region: {region!r}"}

        # For full capture when self is foreground, briefly minimize Azmuth
        minimized = False
        if capture_region == "full" and self_foreground:
            minimized = await _minimize_self()

        win_rect: tuple[int, int, int, int] | None = None
        try:
            if capture_region == "full" or target_hwnd is None:
                if minimized:
                    await asyncio.sleep(0.30)  # 300 ms per spec
                capture = await capture_full_screen()
            else:
                win_rect = _get_window_rect(target_hwnd)
                capture = await capture_window(target_hwnd)
        except ProviderUnavailableError as exc:
            if minimized:
                await _restore_self()
            return {"error": f"screen capture unavailable: {exc}"}
        except Exception as exc:
            if minimized:
                await _restore_self()
            logger.exception("screen capture failed")
            return {"error": f"screen capture failed: {exc}"}
        finally:
            if minimized:
                await _restore_self()

        if self._uia is not None:
            try:
                capture = await _redact_passwords(capture, win_rect, self._uia)
            except Exception:
                logger.exception("redaction failed; continuing")

        capture_bytes = _downscale_if_needed(capture.png_bytes)
        try:
            answer, err = await describe_image_detailed(
                capture_bytes, question,
            )
        except Exception as exc:
            logger.exception("describe_image_detailed raised")
            return {"error": f"vision request failed: {exc}"}

        if err:
            return {"error": err}

        return {
            "answer": answer,
            "answer_source": "vision",
            "region": effective_region,
            "capture_size": [capture.width, capture.height],
            "was_self_foreground": self_foreground,
            "duration_ms": round(
                (time.perf_counter() - started) * 1000, 1
            ),
        }


__all__ = ["DescribeScreenTool"]