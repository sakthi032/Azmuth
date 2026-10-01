"""Automation interfaces and result types.

Two design rules drive every method here:

1. **The automation layer does not enforce permissions.** It performs
   operations. Authorization is the Tool Manager's job (STEP 8). What this
   module *does* provide is the security-relevant *mechanics* — argv-only
   subprocess execution, path sandboxing, application allowlisting — so
   that when a tool is authorized, it cannot accidentally escape its
   declared scope.

2. **Every method documents the capability a tool must declare.** That
   mapping lives in the method docstring, so a plugin author knows
   immediately which capability to gate on.

The abstract surface is broad enough to cover Phase 1 and stable enough
that adding keyboard/mouse/screenshot support later needs no interface
changes — only a new :class:`WindowsAutomation` subclass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AppLaunchResult:
    """Outcome of a successful application launch."""

    alias: str
    pid: int
    executable: str
    started_at: datetime = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "pid": self.pid,
            "executable": self.executable,
            "started_at": self.started_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """One row from the process list."""

    pid: int
    name: str
    session: str | None = None
    memory_kb: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "session": self.session,
            "memory_kb": self.memory_kb,
        }


@dataclass(frozen=True, slots=True)
class FileInfo:
    """Metadata about a file or directory entry."""

    path: str
    is_dir: bool
    size: int
    modified_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "modified_at": self.modified_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """One visible top-level window."""

    handle: int
    title: str
    process_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "title": self.title,
            "process_id": self.process_id,
        }


# --------------------------------------------------------------------------
# The interface
# --------------------------------------------------------------------------
class WindowsAutomation(ABC):
    """Contract every automation backend must satisfy.

    All methods are async for uniformity with the rest of the app, even
    where the underlying work is blocking. Implementations that do genuinely
    slow blocking I/O should run it in a worker thread via
    :func:`asyncio.to_thread` so the event loop stays responsive. Phase 1's
    :class:`WindowsAutomation` implementation does exactly that for the
    subprocess-based methods.

    The capability a tool must declare is documented per method. A method
    with no documented capability (e.g. status probes) is safe for any
    principal to invoke.
    """

    # -- availability ------------------------------------------------------
    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this backend can run on the current system.

        Must never raise. A False return means "unavailable", not "broken";
        callers use this to decide whether to surface the backend at all.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier, e.g. ``"windows"`` or ``"null"``."""

    # -- applications ------------------------------------------------------
    @abstractmethod
    async def open_application(self, alias: str) -> AppLaunchResult:
        """Launch a whitelisted application.

        Capability for the wrapping tool: ``OPEN_APPLICATION`` (restricted).

        ``alias`` must be one of the configured allowlist keys. Launching
        by arbitrary path is not supported on purpose — the allowlist is
        the security boundary that makes OPEN_APPLICATION safe to grant
        to restricted users.
        """

    @abstractmethod
    async def close_application(self, identifier: str) -> int:
        """Terminate a process by PID or image name. Returns count terminated.

        Capability for the wrapping tool: ``CLOSE_APPLICATION`` (restricted).

        Killing another user's processes or critical system processes is
        not permitted — the implementation filters by current-user session.
        """

    @abstractmethod
    async def list_processes(self) -> list[ProcessInfo]:
        """Return running processes visible to the current user.

        Capability for the wrapping tool: ``VIEW_SYSTEM_STATUS`` (restricted).
        """

    # -- files -------------------------------------------------------------
    @abstractmethod
    async def read_file(self, path: str) -> str:
        """Read a UTF-8 text file inside the sandbox.

        Capability for the wrapping tool: depends on the sandbox root the
        path resolves to. Paths under a *public* root →
        ``READ_PUBLIC_FILE`` (restricted); paths under a *personal* root →
        ``READ_PERSONAL_FILE`` (creator). The implementation does not decide
        this — the tool must know which root it is asking for.
        """

    @abstractmethod
    async def write_file(self, path: str, content: str, *, overwrite: bool = False) -> FileInfo:
        """Write a UTF-8 text file inside the sandbox.

        Capability for the wrapping tool: ``WRITE_FILE`` (creator).
        """

    @abstractmethod
    async def delete_file(self, path: str) -> bool:
        """Delete a file inside the sandbox.

        Capability for the wrapping tool: ``DELETE_FILE`` (creator + confirmation).
        """

    @abstractmethod
    async def list_directory(self, path: str) -> list[FileInfo]:
        """List entries of a directory inside the sandbox.

        Capability for the wrapping tool: ``LIST_FILES`` (creator).
        """

    # -- browser -----------------------------------------------------------
    @abstractmethod
    async def open_url(self, url: str, *, new_window: bool = False) -> None:
        """Open a URL in the user's default browser.

        Capability for the wrapping tool: ``OPEN_APPLICATION`` (restricted).

        Only ``http`` and ``https`` URLs are accepted — this blocks
        ``file://``, ``javascript:``, ``data:``, and OS-specific protocol
        handlers that could be abused.
        """

    # -- optional capabilities (Phase 1: unavailable) ----------------------
    @abstractmethod
    async def take_screenshot(self, save_to: str) -> FileInfo:
        """Capture the screen and save it as PNG.

        Capability for the wrapping tool: ``SCREENSHOT`` (restricted).

        Phase 1 raises :class:`ProviderUnavailableError` — a real
        implementation needs a library outside the stdlib.
        """

    @abstractmethod
    async def send_keys(self, keys: str) -> None:
        """Synthesise keyboard input.

        Capability for the wrapping tool: ``ADVANCED_AUTOMATION`` (creator).
        """

    @abstractmethod
    async def move_mouse(self, x: int, y: int) -> None:
        """Move the mouse cursor.

        Capability for the wrapping tool: ``ADVANCED_AUTOMATION`` (creator).
        """

    @abstractmethod
    async def click(self, x: int, y: int, *, button: str = "left") -> None:
        """Click at a screen coordinate.

        Capability for the wrapping tool: ``ADVANCED_AUTOMATION`` (creator).
        """

    # -- window management (Phase 1: unavailable) --------------------------
    @abstractmethod
    async def list_windows(self) -> list[WindowInfo]:
        """List visible top-level windows.

        Capability for the wrapping tool: ``VIEW_SYSTEM_STATUS`` (restricted).
        """

    @abstractmethod
    async def focus_window(self, identifier: str) -> bool:
        """Bring a window to the foreground.

        Capability for the wrapping tool: ``ADVANCED_AUTOMATION`` (creator).
        """