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
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.automation.base import FileInfo
from app.automation.policy import PathPolicy
from app.config.logging_setup import get_logger
from app.core.errors import AutomationError

logger = get_logger("automation.files")

#: Refuse to read files larger than this.
_MAX_READ_BYTES = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# Result types for the newer operations
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FileOpResult:
    source: str
    destination: str
    size_bytes: int
    kind: str = "file"        # "file" or "dir"

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "destination": self.destination,
            "size_bytes": self.size_bytes,
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class FileStat:
    path: str
    exists: bool
    kind: str                  # "file" | "dir" | "missing"
    size_bytes: int
    modified: datetime | None

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "exists": self.exists,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "modified": self.modified.isoformat() if self.modified else None,
        }


class FileController:
    """Read, write, delete, list, move, copy files under a sandbox policy."""

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
        self, path: str, content: str, *, overwrite: bool = True
    ) -> FileInfo:
        resolved = self._policy.resolve_and_check(path)

        def _blocking() -> FileInfo:
            if resolved.exists() and resolved.is_dir():
                raise AutomationError(
                    f"{resolved} is a directory; cannot write to it"
                )
            if resolved.exists() and not overwrite:
                raise AutomationError(
                    f"{resolved} already exists; pass overwrite=True to replace"
                )
            resolved.parent.mkdir(parents=True, exist_ok=True)

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
            if resolved.is_dir():
                raise AutomationError(
                    f"{resolved} is a directory; refusing to delete"
                )
            try:
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
                entries = sorted(
                    resolved.iterdir(), key=lambda p: p.name.lower()
                )
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

    # -- move / rename -----------------------------------------------------
    async def move_path(
        self, source: str, destination: str, *, overwrite: bool = True
    ) -> FileOpResult:
        src = self._policy.resolve_and_check(source)
        dst = self._policy.resolve_and_check(destination)

        def _blocking() -> tuple[Path, int, str]:
            if not src.exists():
                raise AutomationError(f"source does not exist: {src}")
            target = dst / src.name if dst.is_dir() else dst
            if target.exists() and not overwrite:
                raise AutomationError(
                    f"destination exists and overwrite=False: {target}"
                )
            kind = "dir" if src.is_dir() else "file"
            try:
                shutil.move(str(src), str(target))
            except (OSError, shutil.Error) as exc:
                raise AutomationError(f"move failed: {exc}") from exc
            size = target.stat().st_size if target.is_file() else 0
            return target, size, kind

        try:
            target, size, kind = await asyncio.to_thread(_blocking)
        except AutomationError:
            raise
        except Exception as exc:
            raise AutomationError(f"move failed: {exc}") from exc

        logger.info(
            "path moved",
            extra={
                "event": "FILE_MOVED",
                "source": str(src),
                "destination": str(target),
            },
        )
        return FileOpResult(
            source=str(src),
            destination=str(target),
            size_bytes=size,
            kind=kind,
        )

    # -- copy --------------------------------------------------------------
    async def copy_path(
        self, source: str, destination: str, *, overwrite: bool = True
    ) -> FileOpResult:
        src = self._policy.resolve_and_check(source)
        dst = self._policy.resolve_and_check(destination)

        def _blocking() -> tuple[Path, int, str]:
            if not src.exists():
                raise AutomationError(f"source does not exist: {src}")
            target = dst / src.name if dst.is_dir() else dst
            if target.exists() and not overwrite:
                raise AutomationError(
                    f"destination exists and overwrite=False: {target}"
                )
            kind = "dir" if src.is_dir() else "file"
            try:
                if src.is_dir():
                    if target.exists():
                        # Only remove an existing target if overwrite
                        # was explicitly allowed — the caller already
                        # confirmed at the tool layer.
                        shutil.rmtree(target)
                    shutil.copytree(str(src), str(target))
                    total = sum(
                        f.stat().st_size for f in target.rglob("*")
                        if f.is_file()
                    )
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(target))
                    total = target.stat().st_size
            except (OSError, shutil.Error) as exc:
                raise AutomationError(f"copy failed: {exc}") from exc
            return target, total, kind

        try:
            target, size, kind = await asyncio.to_thread(_blocking)
        except AutomationError:
            raise
        except Exception as exc:
            raise AutomationError(f"copy failed: {exc}") from exc

        logger.info(
            "path copied",
            extra={
                "event": "FILE_COPIED",
                "source": str(src),
                "destination": str(target),
            },
        )
        return FileOpResult(
            source=str(src),
            destination=str(target),
            size_bytes=size,
            kind=kind,
        )

    # -- mkdir -------------------------------------------------------------
    async def make_directory(self, path: str) -> bool:
        p = self._policy.resolve_and_check(path)

        def _blocking() -> bool:
            existed = p.exists()
            if existed and not p.is_dir():
                raise AutomationError(
                    f"{p} exists and is not a directory"
                )
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise AutomationError(
                    f"could not create {p}: {exc}"
                ) from exc
            return not existed

        created = await asyncio.to_thread(_blocking)
        logger.info(
            "directory created" if created else "directory already existed",
            extra={"event": "DIRECTORY_MKDIR", "path": str(p)},
        )
        return created

    # -- stat --------------------------------------------------------------
    async def stat_path(self, path: str) -> FileStat:
        p = self._policy.resolve_and_check(path)

        def _blocking() -> FileStat:
            if not p.exists():
                return FileStat(
                    path=str(p), exists=False, kind="missing",
                    size_bytes=0, modified=None,
                )
            st = p.stat()
            return FileStat(
                path=str(p),
                exists=True,
                kind="dir" if p.is_dir() else "file",
                size_bytes=st.st_size,
                modified=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            )

        return await asyncio.to_thread(_blocking)


__all__ = ["FileController", "FileOpResult", "FileStat"]