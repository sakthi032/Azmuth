"""Coordinate translation between screenshot space and click space.

Windows has two coordinate systems that disagree under DPI scaling:

* **Screenshot space** -- what Pillow's ImageGrab returns. Always in
  physical pixels of the **virtual desktop** (all monitors combined).
  On a 1920x1080 display at 125% scaling this is 1920x1080; on a
  dual-monitor setup (3840x1080 combined) this is 3840x1080.

* **Input space** -- what UIA rects use and what ``move_mouse`` expects.
  For a DPI-unaware Python process, this is logical pixels (1536x864
  on the same single display), and the virtual origin may not be (0, 0)
  when a secondary monitor is placed to the left or above the primary.

Mixing the two without translating puts every click at the wrong
location -- a classic DPI bug. Every CV/VLM result MUST be passed
through :func:`screenshot_to_input` before it reaches
:meth:`InputController.click`.

Multi-monitor fix (Phase 1.1):
  ``GetSystemMetrics(0/1)`` only reports the primary monitor's
  *logical* size.  For the full virtual desktop we must use:
    SM_XVIRTUALSCREEN  = 76   (left edge, may be negative)
    SM_YVIRTUALSCREEN  = 77   (top  edge, may be negative)
    SM_CXVIRTUALSCREEN = 78   (total logical width  across all monitors)
    SM_CYVIRTUALSCREEN = 79   (total logical height across all monitors)
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field

from app.config.logging_setup import get_logger

logger = get_logger("automation.dpi")

# SM_* indices for the virtual desktop
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


@dataclass(frozen=True)
class CoordinateSpaces:
    """The two coordinate systems and the ratio between them.

    ``virtual_x`` / ``virtual_y`` hold the logical origin of the virtual
    desktop (may be negative when a monitor is placed left/above primary).
    Click coordinates must be offset by this origin before normalising to
    the [0, 65535] SendInput range when MOUSEEVENTF_VIRTUALDESK is set.
    """

    # Logical size of the full virtual desktop (all monitors)
    input_width: int
    input_height: int
    # Physical size of the full virtual desktop screenshot
    screenshot_width: int
    screenshot_height: int
    # Logical origin of the virtual desktop (may be negative)
    virtual_x: int = field(default=0)
    virtual_y: int = field(default=0)

    @property
    def scale_x(self) -> float:
        """screenshot_x * scale_x == input_x"""
        if self.screenshot_width <= 0:
            return 1.0
        return self.input_width / self.screenshot_width

    @property
    def scale_y(self) -> float:
        """screenshot_y * scale_y == input_y"""
        if self.screenshot_height <= 0:
            return 1.0
        return self.input_height / self.screenshot_height

    def screenshot_to_input(self, sx: int, sy: int) -> tuple[int, int]:
        """Convert a screenshot pixel to input-space coordinates."""
        return (
            int(round(sx * self.scale_x)),
            int(round(sy * self.scale_y)),
        )

    def input_to_screenshot(self, ix: int, iy: int) -> tuple[int, int]:
        """Convert input-space coordinates to screenshot pixels."""
        if self.scale_x <= 0 or self.scale_y <= 0:
            return ix, iy
        return (
            int(round(ix / self.scale_x)),
            int(round(iy / self.scale_y)),
        )

    def as_dict(self) -> dict:
        return {
            "input": [self.input_width, self.input_height],
            "screenshot": [self.screenshot_width, self.screenshot_height],
            "scale": [round(self.scale_x, 4), round(self.scale_y, 4)],
            "dpi_percent": int(round(self.scale_x * 100)),
            "virtual_origin": [self.virtual_x, self.virtual_y],
        }


def query_spaces(screenshot_size: tuple[int, int] | None = None) -> CoordinateSpaces:
    """Build a CoordinateSpaces from the current desktop.

    Uses SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN so that multi-monitor
    setups are measured correctly (not just the primary monitor).

    If ``screenshot_size`` is provided (width, height), it is used as the
    screenshot space. Otherwise a fresh screen grab is attempted to
    discover it. If that also fails, both spaces are assumed equal.
    """
    user32 = ctypes.windll.user32
    # Virtual desktop origin (can be negative on multi-monitor)
    virt_x = int(user32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
    virt_y = int(user32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
    # Full logical span of the virtual desktop across all monitors
    input_w = int(user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
    input_h = int(user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))
    # Fall back to primary metrics if virtual desktop metrics return 0
    # (non-Windows CI or mocked environment)
    if input_w <= 0 or input_h <= 0:
        input_w = int(user32.GetSystemMetrics(0))
        input_h = int(user32.GetSystemMetrics(1))
        virt_x = 0
        virt_y = 0

    if screenshot_size is not None:
        sw, sh = screenshot_size
    else:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(all_screens=True)
            sw, sh = img.width, img.height
        except Exception as exc:
            logger.warning(
                "dpi probe failed -- assuming 1:1 scaling",
                extra={"event": "DPI_PROBE_FAILED", "reason": str(exc)},
            )
            sw, sh = input_w, input_h

    spaces = CoordinateSpaces(
        input_width=input_w,
        input_height=input_h,
        screenshot_width=sw,
        screenshot_height=sh,
        virtual_x=virt_x,
        virtual_y=virt_y,
    )
    logger.info(
        "coordinate spaces",
        extra={
            "event": "DPI_SPACES",
            "input": f"{input_w}x{input_h}",
            "screenshot": f"{sw}x{sh}",
            "scale": f"{spaces.scale_x:.3f}",
            "virtual_origin": f"({virt_x},{virt_y})",
        },
    )
    return spaces


__all__ = ["CoordinateSpaces", "query_spaces"]
