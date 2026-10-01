"""The real :class:`WindowsAutomation` implementation.

Composes the controllers into a single object. Every method is a thin
delegation — the interesting decisions (allowlist, sandbox, argv-only
subprocess) live in the controllers.
"""

from __future__ import annotations

import sys

from app.automation.applications import ApplicationController
from app.automation.base import (
    AppLaunchResult,
    FileInfo,
    ProcessInfo,
    WindowInfo,
    WindowsAutomation,
)
from app.automation.browser import BrowserController
from app.automation.files import FileController
from app.automation.input import InputController
from app.automation.policy import ApplicationPolicy, PathPolicy
from app.automation.screen import ScreenController
from app.automation.windows_mgmt import WindowController


class WindowsAutomationImpl(WindowsAutomation):
    """Automation backend for Windows 11 using stdlib only.

    On non-Windows platforms :meth:`is_available` returns ``False`` so a
    caller can fall back to :class:`NullAutomation` without try/except
    noise. The file and browser controllers are actually portable; only
    the subprocess-based application and process methods are
    Windows-specific.
    """

    def __init__(
        self,
        *,
        application_policy: ApplicationPolicy | None = None,
        path_policy: PathPolicy | None = None,
    ) -> None:
        self._applications = ApplicationController(
            application_policy or ApplicationPolicy.default_windows()
        )
        self._files = FileController(
            path_policy or PathPolicy()  # no roots ⇒ all file ops rejected
        )
        self._browser = BrowserController()
        self._screen = ScreenController()
        self._input = InputController()
        self._windows = WindowController()

    # -- availability ------------------------------------------------------
    @property
    def name(self) -> str:
        return "windows"

    def is_available(self) -> bool:
        return sys.platform == "win32"

    # -- applications ------------------------------------------------------
    async def open_application(self, alias: str) -> AppLaunchResult:
        return await self._applications.open(alias)

    async def close_application(self, identifier: str) -> int:
        return await self._applications.close(identifier)

    async def list_processes(self) -> list[ProcessInfo]:
        return await self._applications.list_processes()

    # -- files -------------------------------------------------------------
    async def read_file(self, path: str) -> str:
        return await self._files.read_text(path)

    async def write_file(
        self, path: str, content: str, *, overwrite: bool = False
    ) -> FileInfo:
        return await self._files.write_text(path, content, overwrite=overwrite)

    async def delete_file(self, path: str) -> bool:
        return await self._files.delete(path)

    async def list_directory(self, path: str) -> list[FileInfo]:
        return await self._files.list_dir(path)

    # -- browser -----------------------------------------------------------
    async def open_url(self, url: str, *, new_window: bool = False) -> None:
        await self._browser.open_url(url, new_window=new_window)

    # -- optional (Phase 1 stubs) -----------------------------------------
    async def take_screenshot(self, save_to: str) -> FileInfo:
        return await self._screen.take_screenshot(save_to)

    async def send_keys(self, keys: str) -> None:
        await self._input.send_keys(keys)

    async def move_mouse(self, x: int, y: int) -> None:
        await self._input.move_mouse(x, y)

    async def click(self, x: int, y: int, *, button: str = "left") -> None:
        await self._input.click(x, y, button=button)

    # -- window management (Phase 1 stubs) --------------------------------
    async def list_windows(self) -> list[WindowInfo]:
        return await self._windows.list_windows()

    async def focus_window(self, identifier: str) -> bool:
        return await self._windows.focus_window(identifier)