"""The sample ``uppercase`` plugin.

Shows the minimum a plugin must implement:

* a :class:`Plugin` subclass named as declared in ``plugin.json``,
* one or more :class:`Tool` subclasses,
* a :meth:`register_tools` that hands the tools to the manager.

The plugin is intentionally trivial — the point is the wiring, not the
functionality. Its single tool uses :class:`Capability.CHAT`, matching
the manifest's declared capability.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.plugins.base import Plugin
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool
from app.tools.manager import ToolManager


class UppercaseTool(Tool):
    """Convert a string to uppercase."""

    name: ClassVar[str] = "uppercase"
    description: ClassVar[str] = "Return the given text in uppercase."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 4096},
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CHAT
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    async def execute(self, *, text: str) -> str:
        return text.upper()


class UppercasePlugin(Plugin):
    """Plugin entry point."""

    def register_tools(self, tool_manager: ToolManager) -> None:
        tool_manager.register(UppercaseTool())