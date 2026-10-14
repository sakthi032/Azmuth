
"""Browser tab tools backed by the extension bridge.

One tool set per installed Chromium browser. Each tool wraps one
extension action. The extension uses the real chrome.tabs API, so
these cover actions CDP does not expose: pin, mute, group, close
without the debug port.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from app.automation.chrome_extension_bridge import get_bridge
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


# --------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------
class _ExtToolBase(Tool):
    action: ClassVar[str] = ""
    browser: ClassVar[str] = "chrome"

    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def is_available(self) -> bool:
        try:
            return get_bridge().is_available(self.browser)
        except Exception:
            return False

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        bridge = get_bridge()
        result = await asyncio.to_thread(
            bridge.send_command, self.browser, self.action, kwargs
        )
        return {"browser": self.browser, **result}


_TAB_ID_PARAM = {
    "type": "object",
    "properties": {"tab_id": {"type": "integer"}},
    "required": ["tab_id"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Concrete tools
# --------------------------------------------------------------------------
class BrowserExtListTabsTool(_ExtToolBase):
    action = "list"
    capability = Capability.VIEW_SYSTEM_STATUS
    risk_level = RiskLevel.LOW
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object", "properties": {}, "additionalProperties": False,
    }


class BrowserExtCloseTabTool(_ExtToolBase):
    action = "close"
    risk_level = RiskLevel.HIGH
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtCloseMatchingTool(_ExtToolBase):
    action = "close_matching"
    risk_level = RiskLevel.HIGH
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "match": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "required": ["match"],
        "additionalProperties": False,
    }


class BrowserExtOpenTabTool(_ExtToolBase):
    action = "open"
    capability = Capability.OPEN_APPLICATION
    risk_level = RiskLevel.LOW
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "minLength": 1, "maxLength": 2048},
            "active": {"type": "boolean"},
        },
        "required": ["url"],
        "additionalProperties": False,
    }


class BrowserExtActivateTabTool(_ExtToolBase):
    action = "activate"
    risk_level = RiskLevel.LOW
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtNavigateTabTool(_ExtToolBase):
    action = "navigate"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_id": {"type": "integer"},
            "url": {"type": "string", "minLength": 1, "maxLength": 2048},
        },
        "required": ["tab_id", "url"],
        "additionalProperties": False,
    }


class BrowserExtReloadTabTool(_ExtToolBase):
    action = "reload"
    risk_level = RiskLevel.LOW
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_id": {"type": "integer"},
            "bypass_cache": {"type": "boolean"},
        },
        "required": ["tab_id"],
        "additionalProperties": False,
    }


class BrowserExtPinTabTool(_ExtToolBase):
    action = "pin"
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtUnpinTabTool(_ExtToolBase):
    action = "unpin"
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtMuteTabTool(_ExtToolBase):
    action = "mute"
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtUnmuteTabTool(_ExtToolBase):
    action = "unmute"
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtDuplicateTabTool(_ExtToolBase):
    action = "duplicate"
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtDiscardTabTool(_ExtToolBase):
    action = "discard"
    risk_level = RiskLevel.LOW
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtGroupTabTool(_ExtToolBase):
    action = "group"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_id": {"type": "integer"},
            "title": {"type": "string", "maxLength": 64},
        },
        "required": ["tab_id"],
        "additionalProperties": False,
    }


class BrowserExtUngroupTabTool(_ExtToolBase):
    action = "ungroup"
    parameters: ClassVar[dict[str, Any]] = _TAB_ID_PARAM


class BrowserExtMoveTabTool(_ExtToolBase):
    action = "move"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_id": {"type": "integer"},
            "index": {"type": "integer", "minimum": -1},
            "window_id": {"type": "integer"},
        },
        "required": ["tab_id", "index"],
        "additionalProperties": False,
    }


class BrowserExtEvalJsTool(_ExtToolBase):
    action = "eval_js"
    capability = Capability.ADVANCED_AUTOMATION
    risk_level = RiskLevel.HIGH
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_id": {"type": "integer"},
            "javascript": {"type": "string", "minLength": 1, "maxLength": 4000},
        },
        "required": ["tab_id", "javascript"],
        "additionalProperties": False,
    }


class BrowserExtClickOnPageTool(_ExtToolBase):
    action = "click_on_page"
    capability = Capability.ADVANCED_AUTOMATION
    risk_level = RiskLevel.HIGH
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_id": {"type": "integer"},
            "text": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "required": ["tab_id", "text"],
        "additionalProperties": False,
    }

# --------------------------------------------------------------------------
# Name + description table
# --------------------------------------------------------------------------
_SPECS: dict[type[_ExtToolBase], tuple[str, str]] = {
    BrowserExtListTabsTool: (
        "ext_list_tabs",
        "List every tab in {display} with numeric id, pinned, muted, "
        "active flags. Uses the real tabs API, not CDP.",
    ),
    BrowserExtCloseTabTool: (
        "ext_close_tab",
        "Close a single {display} tab by its numeric tab id.",
    ),
    BrowserExtCloseMatchingTool: (
        "ext_close_matching",
        "Close every {display} tab whose title or URL contains the given "
        "substring (case-insensitive). Preferred close tool — one call, "
        "no need to list first.",
    ),
    BrowserExtOpenTabTool: (
        "ext_open_tab",
        "Open a URL in a new {display} tab. Set active=false for a "
        "background tab.",
    ),
    BrowserExtActivateTabTool: (
        "ext_activate_tab",
        "Bring a {display} tab to the front (switch to it) by numeric id. "
        "Also focuses the browser window.",
    ),
    BrowserExtNavigateTabTool: (
        "ext_navigate_tab",
        "Navigate an existing {display} tab to a URL by numeric id. "
        "Does NOT open a new tab.",
    ),
    BrowserExtReloadTabTool: (
        "ext_reload_tab",
        "Reload a {display} tab by numeric id.",
    ),
    BrowserExtPinTabTool: (
        "ext_pin_tab",
        "Pin a {display} tab by numeric id.",
    ),
    BrowserExtUnpinTabTool: (
        "ext_unpin_tab",
        "Unpin a {display} tab by numeric id.",
    ),
    BrowserExtMuteTabTool: (
        "ext_mute_tab",
        "Mute a {display} tab by numeric id.",
    ),
    BrowserExtUnmuteTabTool: (
        "ext_unmute_tab",
        "Unmute a {display} tab by numeric id.",
    ),
    BrowserExtDuplicateTabTool: (
        "ext_duplicate_tab",
        "Duplicate a {display} tab by numeric id.",
    ),
    BrowserExtDiscardTabTool: (
        "ext_discard_tab",
        "Discard (suspend) a {display} tab to free memory. The tab stays "
        "in the strip and reloads when clicked.",
    ),
    BrowserExtGroupTabTool: (
        "ext_group_tab",
        "Add a {display} tab to a new tab group with an optional title.",
    ),
    BrowserExtUngroupTabTool: (
        "ext_ungroup_tab",
        "Remove a {display} tab from its group.",
    ),
    BrowserExtMoveTabTool: (
        "ext_move_tab",
        "Move a {display} tab to a different index or window.",
    ),

    BrowserExtEvalJsTool: (
        "ext_eval_js",
        "Run JavaScript in a {display} tab and return the value. The "
        "expression is evaluated in the page context. Use for reading "
        "DOM, finding links, inspecting state.",
    ),
    BrowserExtClickOnPageTool: (
        "ext_click_on_page",
        "Click an element on a {display} page by its visible text. "
        "Finds the first visible element whose text contains the given "
        "string and clicks it.",
    ),
}


def build_extension_tools(browser_names: tuple[str, ...]) -> tuple[Tool, ...]:
    """Build a tool set for every named browser.

    Called with ``installed_profiles()`` names — chrome, edge, brave,
    opera, vivaldi. Each browser gets one tool per action, named
    ``<browser>_ext_<action>``.
    """
    tools: list[Tool] = []
    for browser in browser_names:
        display = browser.capitalize()
        for cls, (suffix, desc) in _SPECS.items():
            instance = cls()
            object.__setattr__(instance, "browser", browser)
            object.__setattr__(instance, "name", f"{browser}_{suffix}")
            object.__setattr__(
                instance, "description", desc.format(display=display)
            )
            tools.append(instance)
    return tuple(tools)


__all__ = ["build_extension_tools"]
