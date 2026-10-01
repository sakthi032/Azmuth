"""Tool subsystem public API.

    from app.tools import ToolManager, Tool, ToolResult

Concrete tools live under ``app.tools.builtin`` (and later, plugins).
Callers register tools with :class:`ToolManager` and invoke them through
:meth:`ToolManager.invoke`.
"""

from app.tools.base import RiskLevel, Tool, ToolMetadata, ToolResult
from app.tools.builtin import EchoTool, GetTimeTool, build_builtin_tools
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry
from app.tools.schema import validate as validate_arguments

__all__ = [
    "EchoTool",
    "GetTimeTool",
    "RiskLevel",
    "Tool",
    "ToolManager",
    "ToolMetadata",
    "ToolRegistry",
    "ToolResult",
    "build_builtin_tools",
    "validate_arguments",
]