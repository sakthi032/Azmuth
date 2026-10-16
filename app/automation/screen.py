"""Screenshot capture via Pillow's ImageGrab.

Two API surfaces coexist here:

* **Functions** — ``capture_full_screen``, ``capture_window``. Used by the
  resolver and vision tiers. Return ``CaptureResult`` with raw PNG bytes.

* **ScreenController class** — the async wrapper the rest of Azmuth
  already depends on (``WindowsAutomationImpl``, tools). Preserved so the
  existing interface does not break.

Screenshots go through Pillow because it's the only cross-Windows-
multi-monitor-friendly capture path that works without native Windows
SDK bindings.
"""

from __future__ import annotations

import asyncio
import io
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from app.automation.base import FileInfo

from app.config.logging_setup import get_logger
from app.core.errors import ProviderUnavailableError

logger = get_logger("automation.screen")


# --------------------------------------------------------------------------
# New API — used by resolver and vision tiers
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CaptureResult:
    path: Path | None
    width: int
    height: int
    png_bytes: bytes

    def as_dict(self) -> dict:
        return {
            "path": str(self.path) if self.path else None,
            "width": self.width,
            "height": self.height,
            "png_size": len(self.png_bytes),
        }


def _require_pillow():
    try:
        from PIL import ImageGrab  # noqa: F401
    except ImportError as exc:
        raise ProviderUnavailableError(
            "Screenshot capture requires Pillow: pip install Pillow"
        ) from exc


async def capture_full_screen(save_to: Path | None = None) -> CaptureResult:
    """Capture every monitor as one composite image."""
    _require_pillow()

    def _do() -> CaptureResult:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        png = buf.getvalue()
        written_path: Path | None = None
        if save_to is not None:
            written_path = Path(save_to)
            written_path.parent.mkdir(parents=True, exist_ok=True)
            written_path.write_bytes(png)
        return CaptureResult(
            path=written_path,
            width=img.width,
            height=img.height,
            png_bytes=png,
        )

    result = await asyncio.to_thread(_do)
    logger.info(
        "screen captured",
        extra={
            "event": "SCREEN_CAPTURED",
            "width": result.width,
            "height": result.height,
            "bytes": len(result.png_bytes),
        },
    )
    return result

async def capture_window(hwnd: int, save_to: Path | None = None) -> CaptureResult:
    """Capture a window's content via PrintWindow.

    Works even when the window is occluded. Uses PW_RENDERFULLCONTENT
    so DirectX and Chrome surfaces are captured, not just GDI content.
    """
    _require_pillow()
    import ctypes
    from ctypes import wintypes

    def _do() -> CaptureResult:
        from PIL import Image

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # ---- 1. Get window dimensions ----
        rect = wintypes.RECT()
        user32.GetWindowRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT)
        ]
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            raise ProviderUnavailableError(
                f"window hwnd={hwnd} has invalid size {w}x{h}"
            )

        # ---- 2. Set up a memory DC and bitmap ----
        hwnd_dc = user32.GetWindowDC(hwnd)
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        gdi32.SelectObject(mem_dc, bitmap)

        # ---- 3. Ask the window to render itself into the memory DC ----
        user32.PrintWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, wintypes.UINT
        ]
        user32.PrintWindow(hwnd, mem_dc, 0x2)   # PW_RENDERFULLCONTENT

        # ---- 4. Pull the pixels out ----
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h            # negative = top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf, ctypes.byref(bmi), 0)

        # ---- 5. Convert raw BGRA bytes to a PIL image ----
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        img = img.convert("RGB")

        # ---- 6. Clean up GDI objects ----
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)

        # ---- 7. Encode and optionally write ----
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        png = bio.getvalue()

        written_path: Path | None = None
        if save_to is not None:
            written_path = Path(save_to)
            written_path.parent.mkdir(parents=True, exist_ok=True)
            written_path.write_bytes(png)

        return CaptureResult(
            path=written_path,
            width=w,
            height=h,
            png_bytes=png,
        )

    return await asyncio.to_thread(_do)

# --------------------------------------------------------------------------
# Legacy API — kept for WindowsAutomationImpl compatibility
# --------------------------------------------------------------------------
class ScreenController:
    """Screenshot capture wrapper for WindowsAutomationImpl.

    Thin adapter over the module-level capture functions so the existing
    automation interface keeps working.
    """

    def __init__(self) -> None:
        self._available = False
        if sys.platform == "win32":
            try:
                from PIL import ImageGrab  # noqa: F401
                self._available = True
            except ImportError:
                self._available = False

    def is_available(self) -> bool:
        return self._available

    async def take_screenshot(self, save_to: str) -> "FileInfo":
        """Capture the full screen and write it to ``save_to``.

        Returns a FileInfo describing the written file. Raises
        ProviderUnavailableError if Pillow is not installed.
        """
        if not self._available:
            raise ProviderUnavailableError(
                "screenshot capture requires Pillow: pip install Pillow"
            )
        from app.automation.base import FileInfo

        path = Path(save_to)
        result = await capture_full_screen(save_to=path)

        return FileInfo(
            path=str(path),
            size_bytes=len(result.png_bytes),
            created_at=datetime.now(timezone.utc),
        )


__all__ = [
    "CaptureResult",
    "ScreenController",
    "capture_full_screen",
    "capture_window",
]