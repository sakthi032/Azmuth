"""Null automation backend.

Every method raises :class:`ProviderUnavailableError`. Used on platforms
where the real backend is not supported, or when an explicit
configuration requests it. Chosen over "silently return empty results"
so callers can distinguish *unavailable* from *nothing found*.
"""

from __future__ import annotations
from typing import Any, ClassVar
from app.core.errors import ProviderUnavailableError
from app.automation.base import (
    AppLaunchResult,
    FileInfo,
    ProcessInfo,
    WindowInfo,
    WindowsAutomation,
)


class NullAutomation(WindowsAutomation):
    """Automation backend that refuses every operation."""

    def __init__(self, reason: str = "no automation backend is configured") -> None:
        self._reason = reason

    @property
    def name(self) -> str:
        return "null"

    def is_available(self) -> bool:
        return False

    async def open_application(self, alias: str) -> AppLaunchResult:
        raise ProviderUnavailableError(self._reason)

    async def close_application(self, identifier: str) -> int:
        raise ProviderUnavailableError(self._reason)

    async def list_processes(self) -> list[ProcessInfo]:
        raise ProviderUnavailableError(self._reason)

    async def read_file(self, path: str) -> str:
        raise ProviderUnavailableError(self._reason)

    async def write_file(self, path: str, content: str, *, overwrite: bool = False) -> FileInfo:
        raise ProviderUnavailableError(self._reason)

    async def delete_file(self, path: str) -> bool:
        raise ProviderUnavailableError(self._reason)

    async def list_directory(self, path: str) -> list[FileInfo]:
        raise ProviderUnavailableError(self._reason)

    async def open_url(self, url: str, *, new_window: bool = False) -> None:
        raise ProviderUnavailableError(self._reason)

    async def take_screenshot(self, save_to: str) -> FileInfo:
        raise ProviderUnavailableError(self._reason)

    async def send_keys(self, keys: str) -> None:
        raise ProviderUnavailableError(self._reason)

    async def move_mouse(self, x: int, y: int) -> None:
        raise ProviderUnavailableError(self._reason)

    async def click(self, x: int, y: int, *, button: str = "left") -> None:
        raise ProviderUnavailableError(self._reason)

    async def list_windows(self) -> list[WindowInfo]:
        raise ProviderUnavailableError(self._reason)

    async def focus_window(self, identifier: str) -> bool:
        raise ProviderUnavailableError(self._reason)

    async def set_window_state(self, identifier: str, state: str) -> dict[str, Any]:
        raise ProviderUnavailableError(self._reason)