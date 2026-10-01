"""Screenshot capture — Phase 1 stub.

A real implementation needs either Pillow's ``ImageGrab`` or raw GDI
via :mod:`ctypes`. Neither is in the stdlib and the spec forbids
unnecessary dependencies, so Phase 1 raises
:class:`ProviderUnavailableError` and lets the caller fall back.

Silently pretending to succeed would be worse: the UI would show a
"success" that produced no file, and callers would have no way to know.
"""

from __future__ import annotations

from app.core.errors import ProviderUnavailableError
from app.automation.base import FileInfo


class ScreenController:
    """Screenshot capture. Unavailable without an optional dependency."""

    async def take_screenshot(self, save_to: str) -> FileInfo:  # noqa: ARG002
        raise ProviderUnavailableError(
            "screenshot capture requires an optional image library "
            "(Pillow) that is not installed in Phase 1"
        )