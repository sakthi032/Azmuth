
"""Built-in tools shipped with Azmuth.

The factory takes the subsystems each tool needs to talk to. This is the
seam that keeps tools decoupled from the concrete runtime: the tool
receives a SearchManager, not a module-level global.
"""

from __future__ import annotations

from app.automation.base import WindowsAutomation
from app.search.manager import SearchManager
from app.tools.base import Tool
from app.tools.builtin.echo import EchoTool
from app.tools.builtin.get_time import GetTimeTool
from app.tools.builtin.search_web import SearchWebTool
from app.tools.builtin.system import (
    ListProcessesTool,
    OpenApplicationTool,
    OpenUrlTool,
)


def build_builtin_tools(
    *,
    search: SearchManager | None = None,
    automation: WindowsAutomation | None = None,
) -> tuple[Tool, ...]:
    """Instantiate the tools Azmuth ships with.

    ``search`` and ``automation`` are optional. When omitted, the tools
    that depend on them are not included, so a test or a stripped-down
    build can register just echo and get_time.
    """
    tools: list[Tool] = [EchoTool(), GetTimeTool()]
    if search is not None:
        tools.append(SearchWebTool(search))
    if automation is not None:
        tools.extend([
            OpenApplicationTool(automation),
            OpenUrlTool(automation),
            ListProcessesTool(automation),
        ])
    return tuple(tools)


__all__ = [
    "EchoTool",
    "GetTimeTool",
    "ListProcessesTool",
    "OpenApplicationTool",
    "OpenUrlTool",
    "SearchWebTool",
    "build_builtin_tools",
]
