"""Browser tab tools backed by the extension bridge.

Each tool is bound to a specific browser (chrome, edge, brave, opera,
vivaldi) at construction. The same class serves every browser; the
factory builds one instance per installed browser.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from app.automation.chrome_extension_bridge import get_bridge
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


class _ExtToolBase(Tool):
    """Common infrastructure for extension-backed browser tools."""

    #: set by subclasses
    action: ClassVar[str] = ""
    #: set on the instance by the factory
    browser: ClassVar[str] = "chrome"

    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"tab_id": {"type": "integer"}},
        "required": ["tab_id"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def is_available(self) -> bool:
        try:
            return get_bridge().is_available(self.browser)
        except Exception:  # noqa: BLE001
            return False

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        bridge = get_bridge()
        result = await asyncio.to_thread(
            bridge.send_command, self.browser, self.action, kwargs
        )
        return {"browser": self.browser, **result}


class BrowserExtListTabsTool(_ExtToolBase):
    action = "list"
    capability = Capability.VIEW_SYSTEM_STATUS
    risk_level = RiskLevel.LOW
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def execute(self, **_: Any) -> dict[str, Any]:
        bridge = get_bridge()
        result = await asyncio.to_thread(
            bridge.send_command, self.browser, "list", {}
        )
        return {"browser": self.browser, **result}


class BrowserExtPinTabTool(_ExtToolBase):
    action = "pin"


class BrowserExtUnpinTabTool(_ExtToolBase):
    action = "unpin"


class BrowserExtMuteTabTool(_ExtToolBase):
    action = "mute"


class BrowserExtUnmuteTabTool(_ExtToolBase):
    action = "unmute"


_TOOL_CLASSES: dict[str, type[_ExtToolBase]] = {
    "list": BrowserExtListTabsTool,
    "pin": BrowserExtPinTabTool,
    "unpin": BrowserExtUnpinTabTool,
    "mute": BrowserExtMuteTabTool,
    "unmute": BrowserExtUnmuteTabTool,
}

_DESCRIPTIONS = {
    "list": (
        "List every tab in {display} with numeric tab id and pinned / "
        "muted state. Uses the real browser tabs API (via the Azmuth "
        "extension), so it reports pin/mute state that CDP does not."
    ),
    "pin": "Pin a {display} tab by numeric tab id.",
    "unpin": "Unpin a {display} tab by numeric tab id.",
    "mute": "Mute a {display} tab by numeric tab id.",
    "unmute": "Unmute a {display} tab by numeric tab id.",
}


def build_extension_tools(
    browser_names: tuple[str, ...],
) -> tuple[Tool, ...]:
    """Build one tool set per browser name.

    Called by the factory with the list of installed Chromium browsers.
    Each browser gets ``<name>_ext_list_tabs``, ``<name>_ext_pin_tab``,
    and so on.
    """
    tools: list[Tool] = []
    for browser in browser_names:
        display = browser.capitalize()
        for action, cls in _TOOL_CLASSES.items():
            instance = cls()
            object.__setattr__(instance, "browser", browser)
            object.__setattr__(
                instance,
                "name",
                f"{browser}_ext_{action}_tab"
                if action != "list"
                else f"{browser}_ext_list_tabs",
            )
            object.__setattr__(
                instance,
                "description",
                _DESCRIPTIONS[action].format(display=display),
            )
            tools.append(instance)
    return tuple(tools)


__all__ = ["build_extension_tools"]