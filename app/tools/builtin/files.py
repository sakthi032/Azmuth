
"""File-system tools.

Wraps the FileController from app.automation.files. Every operation
goes through the sandbox policy, so paths outside the configured roots
are refused before any I/O happens. That enforcement is what makes it
safe to expose these tools to the AI at all.

The sandbox roots are set by the caller. A common choice is the
project directory plus the user's home directory. Whatever is
configured is what the AI can touch - nothing else.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from app.automation.files import FileController
from app.core.errors import AutomationError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------
class FileReadTool(Tool):
    """Read a UTF-8 text file inside the sandbox."""

    name: ClassVar[str] = "file_read"
    description: ClassVar[str] = (
        "Read a UTF-8 text file. The path must be inside the configured "
        "sandbox roots. Returns the file's text contents. Use "
        "file_list first to see what is available."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
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

    def is_available(self) -> bool:
        return bool(self._files.policy.roots)

    async def execute(self, *, path: str) -> dict[str, Any]:
        try:
            text = await self._files.read_text(path)
        except AutomationError:
            raise
        return {
            "path": path,
            "size": len(text),
            "content": text,
        }


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
class FileWriteTool(Tool):
    """Write a UTF-8 text file inside the sandbox."""

    name: ClassVar[str] = "file_write"
    description: ClassVar[str] = (
        "Write a UTF-8 text file. The path must be inside the sandbox "
        "roots. By default refuses to overwrite an existing file; pass "
        "overwrite=true to replace it. Writes are atomic: a crash "
        "mid-write cannot leave a truncated file."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
            "content": {"type": "string", "maxLength": 1_000_000},
            "overwrite": {"type": "boolean"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.WRITE_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    def is_available(self) -> bool:
        return bool(self._files.policy.roots)

    async def execute(
        self, *, path: str, content: str, overwrite: bool = False
    ) -> dict[str, Any]:
        info = await self._files.write_text(path, content, overwrite=overwrite)
        return info.as_dict()


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------
class FileDeleteTool(Tool):
    """Delete a file inside the sandbox."""

    name: ClassVar[str] = "file_delete"
    description: ClassVar[str] = (
        "Delete a single file inside the sandbox. Directories are "
        "refused; delete their contents first if needed. Requires "
        "explicit confirmation."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
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

    def is_available(self) -> bool:
        return bool(self._files.policy.roots)

    async def execute(self, *, path: str) -> dict[str, Any]:
        removed = await self._files.delete(path)
        return {"path": path, "deleted": removed}


# --------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------
class FileListTool(Tool):
    """List a directory inside the sandbox."""

    name: ClassVar[str] = "file_list"
    description: ClassVar[str] = (
        "List the entries of a directory inside the sandbox. Returns "
        "name, size, modification time, and whether each entry is a "
        "directory. Use this to discover what files exist before "
        "reading or writing."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.LIST_FILES
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, files: FileController) -> None:
        self._files = files

    def is_available(self) -> bool:
        return bool(self._files.policy.roots)

    async def execute(self, *, path: str) -> dict[str, Any]:
        entries = await self._files.list_dir(path)
        return {
            "path": path,
            "count": len(entries),
            "entries": [e.as_dict() for e in entries],
        }


__all__ = [
    "FileDeleteTool",
    "FileListTool",
    "FileReadTool",
    "FileWriteTool",
]