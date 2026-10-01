"""Tool discovery and lookup.

Kept separate from :class:`~app.tools.manager.ToolManager` so that
registration (which is a startup concern) and invocation (which is a
per-request concern) can evolve independently. The plugin loader in STEP 16
will register tools here; the manager will read from here.
"""

from __future__ import annotations

from app.core.errors import ToolNotFoundError
from app.security.capabilities import Capability
from app.tools.base import Tool


class ToolRegistry:
    """Name → :class:`Tool` map with a tiny discovery surface."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # -- registration ------------------------------------------------------
    def register(self, tool: Tool, *, replace: bool = False) -> None:
        name = tool.name.strip()
        if not name:
            raise ValueError("tool name must be non-empty")
        if name in self._tools and not replace:
            raise ValueError(f"tool {name!r} is already registered")
        self._tools[name] = tool

    def unregister(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def clear(self) -> None:
        self._tools.clear()

    # -- lookup ------------------------------------------------------------
    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            known = ", ".join(self.names()) or "(none)"
            raise ToolNotFoundError(
                f"tool {name!r} is not registered. Registered tools: {known}"
            ) from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def tools(self) -> tuple[Tool, ...]:
        return tuple(self._tools[name] for name in self.names())

    def by_capability(self, capability: Capability) -> tuple[Tool, ...]:
        return tuple(t for t in self.tools() if t.capability is capability)

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self.tools())