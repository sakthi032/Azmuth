"""Keyboard and mouse input — Phase 1 stub.

Real implementations need either ``pyautogui`` or raw ``ctypes`` +
``SendInput``. Both are outside the stdlib and are deferred. Phase 1
raises :class:`ProviderUnavailableError` so callers know the capability
is not present rather than thinking a no-op succeeded.
"""

from __future__ import annotations

from app.core.errors import ProviderUnavailableError


class InputController:
    """Keyboard/mouse synthesis. Unavailable in Phase 1."""

    async def send_keys(self, keys: str) -> None:  # noqa: ARG002
        raise ProviderUnavailableError(
            "keyboard input requires an optional input library "
            "(pyautogui) that is not installed in Phase 1"
        )

    async def move_mouse(self, x: int, y: int) -> None:  # noqa: ARG002
        raise ProviderUnavailableError(
            "mouse control requires an optional input library "
            "(pyautogui) that is not installed in Phase 1"
        )

    async def click(self, x: int, y: int, *, button: str = "left") -> None:  # noqa: ARG002
        raise ProviderUnavailableError(
            "mouse clicks require an optional input library "
            "(pyautogui) that is not installed in Phase 1"
        )