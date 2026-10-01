"""Window management — Phase 1 stub.

Listing and focusing windows needs ``pygetwindow`` or ``ctypes`` +
``user32.EnumWindows``. Deferred; Phase 1 raises
:class:`ProviderUnavailableError`.
"""

from __future__ import annotations

from app.core.errors import ProviderUnavailableError
from app.automation.base import WindowInfo


class WindowController:
    """Window enumeration and focus. Unavailable in Phase 1."""

    async def list_windows(self) -> list[WindowInfo]:
        raise ProviderUnavailableError(
            "window enumeration requires an optional library "
            "(pygetwindow) that is not installed in Phase 1"
        )

    async def focus_window(self, identifier: str) -> bool:  # noqa: ARG002
        raise ProviderUnavailableError(
            "window focus requires an optional library "
            "(pygetwindow) that is not installed in Phase 1"
        )