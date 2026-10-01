"""Sandboxed file operations.

Every path passes through :class:`~app.automation.policy.PathPolicy`
before any I/O happens. The sandbox rejects:

* paths outside every allowed root,
* ``..`` traversal that escapes a root (by resolving first),
* symlinks that point outside a root (``Path.resolve`` follows them).

Writes are atomic: content goes to a temp file in the same directory,
then :func:`os.replace` swaps it in. A crash mid-write cannot leave a
truncated destination.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError
from app.automation.base import FileInfo
from app.automation.policy import PathPolicy

logger = get_logger("automation.files")

#: Refuse to read files larger than this. Prevents a runaway read from
#: exhausting memory when the user accidentally points at a large binary.
_MAX_READ_BYTES = 8 * 1024 * 1024


class FileController:
    """Read, write, delete, and list files under a sandbox policy."""

    def __init__(self, policy: PathPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> PathPolicy:
        return self._policy

    # -- read --------------------------------------------------------------
    async def read_text(self, path: str) -> str:
        resolved = self._policy.resolve_and_check(path)

        def _blocking() -> str:
            if not resolved.is_file():
                raise AutomationError(f"not a file: {resolved}")
            size = resolved.stat().st_size
            if size > _MAX_READ_BYTES:
                raise AutomationError(
                    f"file {resolved} is {size} bytes, exceeding the "
                    f"{_MAX_READ_BYTES}-byte read limit"
                )
            try:
                return resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise AutomationError(
                    f"file {resolved} is not valid UTF-8 text"
                ) from exc
            except OSError as exc:
                raise AutomationError(
                    f"could not read {resolved}: {exc}"
                ) from exc

        text = await asyncio.to_thread(_blocking)
        logger.info(
            "file read",
            extra={"event": "FILE_READ", "path": str(resolved)},
        )
        return text

    # -- write -------------------------------------------------------------
    async def write_text(
        self, path: str, content: str, *, overwrite: bool = False
    ) -> FileInfo:
        resolved = self._policy.resolve_and_check(path)

        def _blocking() -> FileInfo:
            if resolved.exists() and not overwrite:
                raise AutomationError(
                    f"{resolved} already exists; pass overwrite=True to replace it"
                )
            resolved.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write: temp file in the same directory, fsync, replace.
            fd, temp_path = tempfile.mkstemp(
                dir=str(resolved.parent),
                prefix=f".{resolved.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, resolved)
            except OSError as exc:
                # Best-effort cleanup of the temp file on failure.
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise AutomationError(
                    f"could not write {resolved}: {exc}"
                ) from exc

            stat = resolved.stat()
            return FileInfo(
                path=str(resolved),
                is_dir=False,
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ),
            )

        info = await asyncio.to_thread(_blocking)
        logger.info(
            "file written",
            extra={
                "event": "FILE_WRITTEN",
                "path": str(resolved),
                "size": info.size,
            },
        )
        return info

    # -- delete ------------------------------------------------------------
    async def delete(self, path: str) -> bool:
        resolved = self._policy.resolve_and_check(path)

        def _blocking() -> bool:
            try:
                if resolved.is_dir():
                    # Refuse to recursively delete a directory via this
                    # method. The caller must remove contents first or use
                    # a dedicated (and confirmation-gated) directory API.
                    raise AutomationError(
                        f"{resolved} is a directory; refusing to delete"
                    )
                resolved.unlink()
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise AutomationError(
                    f"could not delete {resolved}: {exc}"
                ) from exc
            return True

        removed = await asyncio.to_thread(_blocking)
        if removed:
            logger.info(
                "file deleted",
                extra={"event": "FILE_DELETED", "path": str(resolved)},
            )
        return removed

    # -- list --------------------------------------------------------------
    async def list_dir(self, path: str) -> list[FileInfo]:
        resolved = self._policy.resolve_and_check(path)

        def _blocking() -> list[FileInfo]:
            if not resolved.is_dir():
                raise AutomationError(f"not a directory: {resolved}")
            results: list[FileInfo] = []
            try:
                entries = sorted(resolved.iterdir(), key=lambda p: p.name.lower())
            except OSError as exc:
                raise AutomationError(
                    f"could not list {resolved}: {exc}"
                ) from exc
            for entry in entries:
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                results.append(
                    FileInfo(
                        path=str(entry),
                        is_dir=entry.is_dir(),
                        size=stat.st_size,
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ),
                    )
                )
            return results

        entries = await asyncio.to_thread(_blocking)
        logger.info(
            "directory listed",
            extra={
                "event": "DIRECTORY_LISTED",
                "path": str(resolved),
                "count": len(entries),
            },
        )
        return entries