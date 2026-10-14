
"""Tools that interact with the Windows system.

Two categories live here:

* **Fast-path tools** - wrap a specific automation API
  (open an app, close a process, list windows, change brightness).
  Used when a direct implementation exists.
* **Generic UI tools** - drive Windows UI Automation and pyautogui.
  Used for anything that has no scriptable API: Night Light, Bluetooth,
  focus assist, and every other toggle in the Settings app.

The AI decides which to call. The code provides the primitives.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.automation.base import WindowsAutomation
from app.automation.input import InputController
from app.automation.settings import SettingsController
from app.automation.uia import UIAutomationController
from app.core.errors import AutomationError, ProviderUnavailableError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool
from app.automation.discovery import AppDiscovery

# --------------------------------------------------------------------------
# Application control
# --------------------------------------------------------------------------
class OpenApplicationTool(Tool):
    """Launch a whitelisted application by alias."""

    name: ClassVar[str] = "open_application"
    description: ClassVar[str] = (
        "Open an application. Accepts an allowlist alias ('chrome', "
        "'notepad', 'settings'), a display name from list_installed_apps, "
        "or an absolute executable path inside an approved root. If you "
        "are unsure what to call the app, call list_installed_apps first "
        "and pass the exact name it returns."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "alias": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "The allowlist alias, e.g. 'chrome'.",
            },
        },
        "required": ["alias"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.OPEN_APPLICATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    def is_available(self) -> bool:
        return self._automation.is_available()

    async def execute(self, *, alias: str) -> dict[str, Any]:
        try:
            result = await self._automation.open_application(alias)
        except (AutomationError, ProviderUnavailableError) as exc:
            raise AutomationError(str(exc)) from exc
        return result.as_dict()


class OpenUrlTool(Tool):
    """Open a URL in the default browser."""

    name: ClassVar[str] = "open_url"
    description: ClassVar[str] = (
        "Open a URL in the user's default browser. Only http and https "
        "URLs are allowed."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "pattern": "^https?://",
                "description": "An http:// or https:// URL.",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.OPEN_APPLICATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    def is_available(self) -> bool:
        return self._automation.is_available()

    async def execute(self, *, url: str) -> dict[str, Any]:
        await self._automation.open_url(url)
        return {"opened": url}


class CloseApplicationTool(Tool):
    """Close a running application by name or PID."""

    name: ClassVar[str] = "close_application"
    description: ClassVar[str] = (
        "Close a running application. Accepts either a process ID "
        "(as a string of digits) or an image name like 'notepad.exe'. "
        "To close an app by its window title, first call list_windows "
        "to find its process ID, then pass that PID here."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "identifier": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "required": ["identifier"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CLOSE_APPLICATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    def is_available(self) -> bool:
        return self._automation.is_available()

    async def execute(self, *, identifier: str) -> dict[str, Any]:
        count = await self._automation.close_application(identifier)
        return {"identifier": identifier, "terminated": count}


# --------------------------------------------------------------------------
# System inspection
# --------------------------------------------------------------------------
class ListProcessesTool(Tool):
    """List running processes visible to the current user."""

    name: ClassVar[str] = "list_processes"
    description: ClassVar[str] = (
        "List the currently running processes on the machine. "
        "Read-only. Useful for answering 'is X running' questions."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    def is_available(self) -> bool:
        return self._automation.is_available()

    async def execute(self) -> dict[str, Any]:
        processes = await self._automation.list_processes()
        summary = [p.as_dict() for p in processes[:60]]
        return {"count": len(processes), "processes": summary}


class ListWindowsTool(Tool):
    """List visible top-level windows with their owning process."""

    name: ClassVar[str] = "list_windows"
    description: ClassVar[str] = (
        "List every visible top-level window on the desktop. Each entry "
        "includes the window title, its process ID, and the process image "
        "name. Use this to find the process ID of a specific window before "
        "closing it, or to answer 'is X open' questions. Read-only."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    def is_available(self) -> bool:
        return self._automation.is_available()

    async def execute(self) -> dict[str, Any]:
        import asyncio

        from app.automation.windows_mgmt import list_windows

        windows = await asyncio.to_thread(list_windows)
        names = await self._automation.list_processes()
        by_pid = {p.pid: p.name for p in names}

        entries = [
            {
                "handle": w.handle,
                "title": w.title,
                "process_id": w.process_id,
                "process_name": by_pid.get(w.process_id),
            }
            for w in windows
        ]
        return {"count": len(entries), "windows": entries}


# --------------------------------------------------------------------------
# Settings with a real scriptable API
# --------------------------------------------------------------------------
class SetSettingTool(Tool):
    """Fast path for settings that have a direct API."""

    name: ClassVar[str] = "set_setting"
    description: ClassVar[str] = (
        "Change a Windows setting that has a direct scriptable API. "
        "Supported: 'brightness' (0-100 integer), 'dark_mode' (boolean). "
        "For any OTHER setting (Night Light, Bluetooth, focus assist, "
        "etc.), use open_settings_page + set_toggle instead."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": ["brightness", "dark_mode"]},
            "value": {
                "oneOf": [
                    {"type": "integer", "minimum": 0, "maximum": 100},
                    {"type": "boolean"},
                ],
            },
        },
        "required": ["name", "value"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.MODIFY_SYSTEM_CONFIG
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, settings: SettingsController) -> None:
        self._settings = settings

    async def execute(self, *, name: str, value: Any) -> dict[str, Any]:
        if name == "brightness":
            result = await self._settings.set_brightness(int(value))
        elif name == "dark_mode":
            result = await self._settings.set_dark_mode(bool(value))
        else:
            raise AutomationError(
                f"set_setting does not handle {name!r}. "
                f"Use open_settings_page + set_toggle for this one."
            )
        return {"setting": result.setting, "value": result.value, "success": True}


# --------------------------------------------------------------------------
# Generic UI Automation tools
# --------------------------------------------------------------------------
class OpenSettingsPageTool(Tool):
    """Open a Windows Settings page by URI suffix."""

    name: ClassVar[str] = "open_settings_page"
    description: ClassVar[str] = (
        "Open a Windows Settings page. Pass the URI suffix, e.g. "
        "'display', 'bluetooth', 'sound', 'network', 'personalization', "
        "'windowsupdate', 'privacy', 'apps'. The page opens in the "
        "background and stays visible for subsequent UI tools."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "page": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "pattern": "^[a-z0-9\\-]+$",
            },
        },
        "required": ["page"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.OPEN_APPLICATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    async def execute(self, *, page: str) -> dict[str, Any]:
        import asyncio
        import os

        uri = f"ms-settings:{page}"
        try:
            await asyncio.to_thread(os.startfile, uri)
        except OSError as exc:
            raise AutomationError(f"could not open {uri}: {exc}") from exc
        return {"opened": uri}


class SetToggleTool(Tool):
    """Set any named toggle in any window."""

    name: ClassVar[str] = "set_toggle"
    description: ClassVar[str] = (
        "Find a named toggle switch (checkbox, switch, on/off control) "
        "anywhere on screen and set it to a specific state. Idempotent: "
        "reads the current state first, so it is safe to call twice. "
        "Use this for Night Light, Bluetooth, Wi-Fi, focus assist, and "
        "any other setting without a direct API. The setting must be "
        "visible on screen - call open_settings_page first if needed, "
        "then wait a moment for it to render."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "The toggle's visible label, e.g. 'Night light'.",
            },
            "on": {"type": "boolean"},
        },
        "required": ["name", "on"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, uia: UIAutomationController) -> None:
        self._uia = uia

    def is_available(self) -> bool:
        return self._uia.is_available()

    async def execute(self, *, name: str, on: bool) -> dict[str, Any]:
        await self._uia.set_toggle(name=name, on=on)
        return {"toggle": name, "on": on}


class ClickElementTool(Tool):
    """Click any named UI element."""

    name: ClassVar[str] = "click_element"
    description: ClassVar[str] = (
        "Find a user-interface element by its visible name and click it. "
        "Works on buttons, menu items, links, and list entries. Use this "
        "when set_toggle does not apply. Examples: 'Save', 'OK', "
        "'Sign in', 'Restart now'."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 200},
            "control_type": {
                "type": "string",
                "description": "Optional filter: 'Button', 'MenuItem', etc.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, uia: UIAutomationController) -> None:
        self._uia = uia

    def is_available(self) -> bool:
        return self._uia.is_available()

    async def execute(
        self, *, name: str, control_type: str | None = None
    ) -> dict[str, Any]:
        await self._uia.invoke_element(name=name, control_type=control_type)
        return {"clicked": name}


class ListUIElementsTool(Tool):
    """List visible UI elements matching a name fragment."""

    name: ClassVar[str] = "list_ui_elements"
    description: ClassVar[str] = (
        "List UI elements on screen whose name contains the given text. "
        "Use this to discover what is clickable before calling "
        "click_element or set_toggle. Read-only."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, uia: UIAutomationController) -> None:
        self._uia = uia

    def is_available(self) -> bool:
        return self._uia.is_available()

    async def execute(
        self, *, name: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        elements = await self._uia.find_elements(name=name, limit=limit)
        return {
            "count": len(elements),
            "elements": [e.as_dict() for e in elements],
        }


# --------------------------------------------------------------------------
# Raw input
# --------------------------------------------------------------------------
class TypeTextTool(Tool):
    """Type text into whatever window has focus."""

    name: ClassVar[str] = "type_text"
    description: ClassVar[str] = (
        "Type a string of text as if the user typed it. The text goes "
        "into whatever window currently has keyboard focus. Use with "
        "care - it will type into whatever is focused, including the "
        "wrong window."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 4096},
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, input_ctl: InputController) -> None:
        self._input = input_ctl

    def is_available(self) -> bool:
        return self._input.is_available()

    async def execute(self, *, text: str) -> dict[str, Any]:
        await self._input.type_text(text)
        return {"typed": len(text)}


class PressKeysTool(Tool):
    """Press a key or key combination."""

    name: ClassVar[str] = "press_keys"
    description: ClassVar[str] = (
        "Press a key or chord in the currently focused window. Examples: "
        "'enter', 'esc', 'tab', 'ctrl+c', 'alt+tab', 'win', 'win+i'. "
        "Use this to trigger keyboard shortcuts."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "keys": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "required": ["keys"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, input_ctl: InputController) -> None:
        self._input = input_ctl

    def is_available(self) -> bool:
        return self._input.is_available()

    async def execute(self, *, keys: str) -> dict[str, Any]:
        await self._input.press_keys(keys)
        return {"pressed": keys}


# --------------------------------------------------------------------------
# Direct cursor control
# --------------------------------------------------------------------------
class MoveMouseTool(Tool):
    """Move the mouse cursor to absolute screen coordinates."""

    name: ClassVar[str] = "move_mouse"
    description: ClassVar[str] = (
        "Move the mouse cursor to (x, y) in screen pixels. The origin is "
        "the top-left corner of the primary display. Use this when you "
        "need to position the cursor before clicking, or to hover over a "
        "UI element that click_element cannot find."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "minimum": 0, "maximum": 10000},
            "y": {"type": "integer", "minimum": 0, "maximum": 10000},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, input_ctl: InputController) -> None:
        self._input = input_ctl

    def is_available(self) -> bool:
        return self._input.is_available()

    async def execute(self, *, x: int, y: int) -> dict[str, Any]:
        await self._input.move_mouse(x, y)
        return {"moved_to": [x, y]}


class ClickAtTool(Tool):
    """Click at absolute screen coordinates."""

    name: ClassVar[str] = "click_at"
    description: ClassVar[str] = (
        "Click the mouse at (x, y) in screen pixels. Use for coordinates "
        "that click_element cannot reach - e.g. within a page rendered "
        "inside a browser, or a custom control. Options: 'left' (default), "
        "'right', 'middle'. Set 'double' to true for a double-click."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "minimum": 0, "maximum": 10000},
            "y": {"type": "integer", "minimum": 0, "maximum": 10000},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
            },
            "double": {"type": "boolean"},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, input_ctl: InputController) -> None:
        self._input = input_ctl

    def is_available(self) -> bool:
        return self._input.is_available()

    async def execute(
        self,
        *,
        x: int,
        y: int,
        button: str = "left",
        double: bool = False,
    ) -> dict[str, Any]:
        if double:
            await self._input.double_click(x, y)
        else:
            await self._input.click(x, y, button=button)
        return {"clicked_at": [x, y], "button": button, "double": double}


class GetCursorPositionTool(Tool):
    """Return the current mouse position."""

    name: ClassVar[str] = "get_cursor_position"
    description: ClassVar[str] = (
        "Return the current (x, y) position of the mouse cursor, in screen "
        "pixels. Read-only. Useful before a click_at so you can confirm "
        "where the cursor is."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, input_ctl: InputController) -> None:
        self._input = input_ctl

    def is_available(self) -> bool:
        return self._input.is_available()

    async def execute(self) -> dict[str, Any]:
        x, y = await self._input.position()
        return {"x": x, "y": y}


class ScrollTool(Tool):
    """Scroll the mouse wheel."""

    name: ClassVar[str] = "scroll"
    description: ClassVar[str] = (
        "Scroll the mouse wheel. Positive values scroll up, negative "
        "scroll down. One notch is 1; a page scroll is usually 5-10. "
        "The cursor should be over the area you want to scroll."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "amount": {"type": "integer", "minimum": -100, "maximum": 100},
        },
        "required": ["amount"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, input_ctl: InputController) -> None:
        self._input = input_ctl

    def is_available(self) -> bool:
        return self._input.is_available()

    async def execute(self, *, amount: int) -> dict[str, Any]:
        await self._input.scroll(amount)
        return {"scrolled": amount}
    
    
class FocusWindowTool(Tool):
    """Bring a window to the foreground."""

    name: ClassVar[str] = "focus_window"
    description: ClassVar[str] = (
        "Bring a window to the foreground so it receives keyboard input. "
        "Accepts either a window title substring or the numeric handle "
        "from list_windows. Use this before type_text or press_keys "
        "when the target is not the currently-focused window."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "identifier": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "A window title substring or numeric handle.",
            },
        },
        "required": ["identifier"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    def is_available(self) -> bool:
        return self._automation.is_available()

    async def execute(self, *, identifier: str) -> dict[str, Any]:
        focused = await self._automation.focus_window(identifier)
        return {"identifier": identifier, "focused": focused}
    
    

class ListInstalledAppsTool(Tool):
    """List applications installed on this machine."""

    name: ClassVar[str] = "list_installed_apps"
    description: ClassVar[str] = (
        "List the applications installed on this machine, with their "
        "display names and executable paths. Read-only. Use this before "
        "open_application when you do not know whether an app is "
        "installed or what it is called. Discovery is cached - call with "
        "refresh=true to re-scan."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "refresh": {"type": "boolean"},
            "query": {
                "type": "string",
                "description": "Optional substring to filter by name.",
            },
        },
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, discovery: "AppDiscovery") -> None:
        self._discovery = discovery

    def is_available(self) -> bool:
        import sys
        return sys.platform == "win32"

    async def execute(
        self, *, refresh: bool = False, query: str | None = None
    ) -> dict[str, Any]:
        import asyncio
        apps = await asyncio.to_thread(
            self._discovery.list_installed, force=refresh
        )
        if query:
            needle = query.lower()
            apps = [a for a in apps if needle in a.name.lower()]
        return {
            "count": len(apps),
            "apps": [a.as_dict() for a in apps],
        }

   
class ListChromeProfilesTool(Tool):
    """List Chrome profiles installed on this machine."""

    name: ClassVar[str] = "chrome_list_profiles"
    description: ClassVar[str] = (
        "List every Chrome profile on this machine, with its user-visible "
        "name and the directory Chrome uses internally. Use this before "
        "chrome_open_profile."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object", "properties": {}, "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def is_available(self) -> bool:
        import sys
        return sys.platform == "win32"

    async def execute(self) -> dict[str, Any]:
        import asyncio
        from app.automation.chrome_profiles import list_chrome_profiles
        profiles = await asyncio.to_thread(list_chrome_profiles)
        return {"count": len(profiles), "profiles": [p.as_dict() for p in profiles]}


class ChromeOpenProfileTool(Tool):
    """Launch Chrome with a specific profile."""

    name: ClassVar[str] = "chrome_open_profile"
    description: ClassVar[str] = (
        "Open Chrome with a specific profile. Use the 'directory' value "
        "from chrome_list_profiles, not the display name. Optional URL "
        "argument opens that URL in the profile. The user's currently "
        "running Chrome (if any) stays untouched - this launches a new "
        "window in the specified profile."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "directory": {"type": "string", "minLength": 1, "maxLength": 64},
            "url": {"type": "string", "maxLength": 2048},
        },
        "required": ["directory"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.OPEN_APPLICATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    async def execute(
        self, *, directory: str, url: str | None = None
    ) -> dict[str, Any]:
        import asyncio
        import shutil
        import subprocess
        import sys

        if sys.platform != "win32":
            raise AutomationError("chrome_open_profile requires Windows")

        chrome_exe = shutil.which("chrome") or shutil.which("chrome.exe")
        if not chrome_exe:
            for candidate in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ):
                from pathlib import Path as _P
                if _P(candidate).is_file():
                    chrome_exe = candidate
                    break
        if not chrome_exe:
            raise AutomationError("chrome.exe not found on this machine")

        argv = [chrome_exe, f"--profile-directory={directory}"]
        if url:
            argv.append(url)

        def _launch() -> None:
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                shell=False,
                creationflags=(
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
            )

        await asyncio.to_thread(_launch)
        return {"launched": directory, "url": url}


class SetWindowStateTool(Tool):
    """Set the state of a window: maximize, minimize, restore, or show."""

    name: ClassVar[str] = "set_window_state"
    description: ClassVar[str] = (
        "Set a window's state. Use 'maximize' to fill the screen, "
        "'minimize' to hide to the taskbar, 'restore' to un-maximize or "
        "un-minimize, or 'show' to reveal a hidden window. Accepts "
        "either a window title substring or the numeric handle from "
        "list_windows. Focuses the window after maximizing or restoring."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "identifier": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "Window title substring or numeric handle.",
            },
            "state": {
                "type": "string",
                "enum": ["maximize", "minimize", "restore", "show"],
            },
        },
        "required": ["identifier", "state"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    def is_available(self) -> bool:
        return self._automation.is_available()

    async def execute(self, *, identifier: str, state: str) -> dict[str, Any]:
        return await self._automation.set_window_state(identifier, state)
 
__all__ = [
    "ClickAtTool",
    "ClickElementTool",
    "CloseApplicationTool",
    "GetCursorPositionTool",
    "ListProcessesTool",
    "ListUIElementsTool",
    "ListWindowsTool",
    "MoveMouseTool",
    "OpenApplicationTool",
    "OpenSettingsPageTool",
    "OpenUrlTool",
    "PressKeysTool",
    "ScrollTool",
    "SetSettingTool",
    "SetToggleTool",
    "TypeTextTool",
]
