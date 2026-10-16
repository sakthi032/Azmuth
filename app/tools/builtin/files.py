"""File tools — read, write, delete, list, move, copy, mkdir, stat."""

from __future__ import annotations

from typing import Any, ClassVar

from app.automation.files import FileController
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------
class FileReadTool(Tool):
    name: ClassVar[str] = "file_read"
    description: ClassVar[str] = (
        "Read a UTF-8 text file. Returns the content up to 8 MB. "
        "Use file_stat first if you are not sure the file exists."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.READ_PERSONAL_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    async def execute(self, *, path: str) -> dict[str, Any]:
        content = await self._files.read_text(path)
        return {"path": path, "content": content, "length": len(content)}


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
class FileWriteTool(Tool):
    name: ClassVar[str] = "file_write"
    description: ClassVar[str] = (
        "Write UTF-8 text to a file. Creates the file (and any parent "
        "directories) if missing. Overwrites by default. Writes are "
        "atomic — a partial write cannot corrupt the destination."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
            "content": {"type": "string", "maxLength": 500_000},
            "overwrite": {
                "type": "boolean",
                "description": "Default true. Set false to refuse to "
                               "replace an existing file.",
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.WRITE_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    async def execute(
        self, *, path: str, content: str, overwrite: bool = True
    ) -> dict[str, Any]:
        info = await self._files.write_text(
            path, content, overwrite=overwrite
        )
        return info.as_dict()


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------
class FileDeleteTool(Tool):
    name: ClassVar[str] = "file_delete"
    description: ClassVar[str] = (
        "Delete a single file. Refuses to delete directories — to "
        "remove a directory tree, delete its contents one by one or "
        "move it out of the sandbox. Irreversible."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.DELETE_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    async def execute(self, *, path: str) -> dict[str, Any]:
        deleted = await self._files.delete(path)
        return {"path": path, "deleted": deleted}


# --------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------
class FileListTool(Tool):
    name: ClassVar[str] = "file_list"
    description: ClassVar[str] = (
        "List entries in a directory. Returns name, kind (file/dir), "
        "size, and modified time for each entry."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.READ_PERSONAL_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    async def execute(self, *, path: str) -> dict[str, Any]:
        entries = await self._files.list_dir(path)
        return {
            "path": path,
            "count": len(entries),
            "entries": [e.as_dict() for e in entries],
        }


# --------------------------------------------------------------------------
# Move / rename (this is also "cut + paste")
# --------------------------------------------------------------------------
class FileMoveTool(Tool):
    name: ClassVar[str] = "file_move"
    description: ClassVar[str] = (
        "Move or rename a file or directory. Same as cut + paste. If "
        "the destination is an existing directory, the source is moved "
        "inside it. If the destination does not exist, the source is "
        "renamed to it. Cross-drive moves work."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "minLength": 1, "maxLength": 512},
            "destination": {"type": "string", "minLength": 1, "maxLength": 512},
            "overwrite": {"type": "boolean"},
        },
        "required": ["source", "destination"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.WRITE_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    async def execute(
        self,
        *,
        source: str,
        destination: str,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        result = await self._files.move_path(
            source, destination, overwrite=overwrite
        )
        return result.as_dict()


# --------------------------------------------------------------------------
# Copy
# --------------------------------------------------------------------------
class FileCopyTool(Tool):
    name: ClassVar[str] = "file_copy"
    description: ClassVar[str] = (
        "Copy a file or directory. Same as copy + paste. Directory "
        "copies are recursive. If destination is an existing "
        "directory, source is copied inside it."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "minLength": 1, "maxLength": 512},
            "destination": {"type": "string", "minLength": 1, "maxLength": 512},
            "overwrite": {"type": "boolean"},
        },
        "required": ["source", "destination"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.WRITE_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    async def execute(
        self,
        *,
        source: str,
        destination: str,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        result = await self._files.copy_path(
            source, destination, overwrite=overwrite
        )
        return result.as_dict()


# --------------------------------------------------------------------------
# mkdir
# --------------------------------------------------------------------------
class FileMakeDirTool(Tool):
    name: ClassVar[str] = "file_mkdir"
    description: ClassVar[str] = (
        "Create a directory, including any missing parent directories. "
        "Idempotent — a no-op if the directory already exists."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.WRITE_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    async def execute(self, *, path: str) -> dict[str, Any]:
        created = await self._files.make_directory(path)
        return {"path": path, "created": created}


# --------------------------------------------------------------------------
# stat
# --------------------------------------------------------------------------
class FileStatTool(Tool):
    name: ClassVar[str] = "file_stat"
    description: ClassVar[str] = (
        "Return metadata for a path: whether it exists, file vs "
        "directory, size, and modified time. Use this before file_move "
        "or file_delete to confirm the target is what you think it is."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.READ_PERSONAL_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    async def execute(self, *, path: str) -> dict[str, Any]:
        info = await self._files.stat_path(path)
        return info.as_dict()


__all__ = [
    "FileCopyTool",
    "FileDeleteTool",
    "FileListTool",
    "FileMakeDirTool",
    "FileMoveTool",
    "FileReadTool",
    "FileStatTool",
    "FileWriteTool",
]