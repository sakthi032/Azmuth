"""Coordinate translation between screenshot space and click space.

Windows has two coordinate spaces that disagree under DPI scaling:

* **Screenshot space** — what Pillow's ImageGrab returns. Always in
  physical pixels of the virtual desktop. On a 1920x1080 display at
  125% scaling, this is 1920x1080.

* **Input space** — what UIA rects use and what ``move_mouse`` expects.
  For a DPI-unaware Python process, this is logical pixels (1536x864
  on the same display).

Mixing the two without translating puts every click at the wrong
location — a classic DPI bug. Every CV/VLM result MUST be passed
through :func:`screenshot_to_input` before it reaches
:meth:`InputController.click`.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from app.config.logging_setup import get_logger

logger = get_logger("automation.dpi")


@dataclass(frozen=True)
class CoordinateSpaces:
    """The two coordinate systems and the ratio between them."""
    # What GetSystemMetrics returns (logical for a DPI-unaware process)
    input_width: int
    input_height: int
    # What a fresh screenshot reports (always physical)
    screenshot_width: int
    screenshot_height: int

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
        }


def query_spaces(screenshot_size: tuple[int, int] | None = None) -> CoordinateSpaces:
    """Build a CoordinateSpaces from the current desktop.

    If ``screenshot_size`` is provided (width, height), it is used as the
    screenshot space. Otherwise a fresh screen grab is attempted to
    discover it. If that also fails, both spaces are assumed equal.
    """
    user32 = ctypes.windll.user32
    input_w = int(user32.GetSystemMetrics(0))
    input_h = int(user32.GetSystemMetrics(1))

    if screenshot_size is not None:
        sw, sh = screenshot_size
    else:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(all_screens=True)
            sw, sh = img.width, img.height
        except Exception as exc:
            logger.warning(
                "dpi probe failed — assuming 1:1 scaling",
                extra={"event": "DPI_PROBE_FAILED", "reason": str(exc)},
            )
            sw, sh = input_w, input_h

    spaces = CoordinateSpaces(
        input_width=input_w,
        input_height=input_h,
        screenshot_width=sw,
        screenshot_height=sh,
    )
    logger.info(
        "coordinate spaces",
        extra={
            "event": "DPI_SPACES",
            "input": f"{input_w}x{input_h}",
            "screenshot": f"{sw}x{sh}",
            "scale": f"{spaces.scale_x:.3f}",
        },
    )
    return spaces


__all__ = ["CoordinateSpaces", "query_spaces"]