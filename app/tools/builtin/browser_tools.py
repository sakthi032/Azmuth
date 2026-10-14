
"""Browser tab-control tools, one set per installed Chromium browser.

Each browser gets three tools: ``<name>_list_tabs``,
``<name>_close_tab``, and ``<name>_launch_debug``. Only browsers that
are actually installed contribute tools.

The concrete classes here declare sensible class-level defaults so the
security audit and any direct import see valid Tool subclasses. The
factory then overrides ``name`` / ``description`` per instance with
browser-specific values.
"""

from __future__ import annotations
from app.core.errors import AutomationError
import asyncio
import subprocess
import sys
import time
from typing import Any, ClassVar
import json
from app.automation.browsers import BrowserProfile, cdp_profile_dir
from app.automation.chrome import BrowserDevTools
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


# --------------------------------------------------------------------------
# Base - shared constructor and defaults
# --------------------------------------------------------------------------
class BrowserToolBase(Tool):
    """Shared base for the browser tab tools.

    Not instantiated directly. Declares defaults for every attribute
    ``Tool`` expects so the security audit and any accidental direct
    import see a complete Tool subclass.
    """

    name: ClassVar[str] = "browser_tool"
    description: ClassVar[str] = "Base class for browser tab tools."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    action: ClassVar[str] = ""

    def __init__(
        self,
        profile: BrowserProfile,
        devtools: BrowserDevTools,
    ) -> None:
        self._profile = profile
        self._devtools = devtools


# --------------------------------------------------------------------------
# Launch debug
# --------------------------------------------------------------------------
class BrowserLaunchDebugTool(BrowserToolBase):
    """Close the browser and relaunch with its debug port open."""

    name = "browser_launch_debug"
    description = "Close the browser and relaunch with the debug port open."
    action = "launch_debug"
    capability = Capability.ADVANCED_AUTOMATION
    risk_level = RiskLevel.HIGH
    requires_confirmation = True

    def is_available(self) -> bool:
        # The launcher is only useful when the browser isn't already
        # exposing a debug port. When it is, hide this tool so the model
        # can't call it redundantly.
        if sys.platform != "win32" or not self._profile.is_installed():
            return False
        return not self._devtools.is_available()

    async def execute(self) -> dict[str, Any]:
        exe = self._profile.find_executable()
        if exe is None:
            return {
                "success": False,
                "error": f"{self._profile.display_name} is not installed",
            }

        profile_dir = cdp_profile_dir(self._profile)
        profile_dir.mkdir(parents=True, exist_ok=True)

        def _do() -> dict[str, Any]:
            # subprocess.run(
            #     ["taskkill", "/IM", exe.name, "/F"],
            #     stdin=subprocess.DEVNULL,
            #     stdout=subprocess.DEVNULL,
            #     stderr=subprocess.DEVNULL,
            #     shell=False,
            #     check=False,
            # )
            # time.sleep(1.0)

            subprocess.Popen(
                [
                    str(exe),
                    f"--user-data-dir={profile_dir}",
                    "--profile-directory=Default",
                    f"--remote-debugging-port={self._profile.debug_port}",
                    "--remote-allow-origins=*",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
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

            for _ in range(20):
                time.sleep(0.5)
                if self._devtools.is_available():
                    return {"success": True, "port": self._profile.debug_port}
            return {
                "success": False,
                "error": (
                    f"{self._profile.display_name} launched but the "
                    f"debug port {self._profile.debug_port} did not open"
                ),
            }

        return await asyncio.to_thread(_do)


# --------------------------------------------------------------------------
# List tabs
# --------------------------------------------------------------------------
class BrowserListTabsTool(BrowserToolBase):
    """List every open tab in the browser."""

    name = "browser_list_tabs"
    description = "List every open tab in the browser."
    action = "list_tabs"
    capability = Capability.VIEW_SYSTEM_STATUS
    risk_level = RiskLevel.LOW
    requires_confirmation = False

    def is_available(self) -> bool:
        return self._devtools.is_available()

    async def execute(self) -> dict[str, Any]:
        tabs = await asyncio.to_thread(self._devtools.list_tabs)
        return {
            "browser": self._profile.name,
            "count": len(tabs),
            "tabs": [t.as_dict() for t in tabs],
        }


# --------------------------------------------------------------------------
# Close tab
# --------------------------------------------------------------------------
class BrowserCloseTabTool(BrowserToolBase):
    """Close a tab whose title or URL matches."""

    name = "browser_close_tab"
    description = "Close a tab whose title or URL matches."
    action = "close_tab"
    capability = Capability.ADVANCED_AUTOMATION
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = False
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "match": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "required": ["match"],
        "additionalProperties": False,
    }

    def is_available(self) -> bool:
        return self._devtools.is_available()

    async def execute(self, *, match: str) -> dict[str, Any]:
        closed = await asyncio.to_thread(
            self._devtools.close_tabs_matching, match
        )
        return {
            "browser": self._profile.name,
            "closed_count": len(closed),
            "match": match,
        }

class BrowserOpenUrlTool(BrowserToolBase):
    """Open a URL in a new tab of the CDP-connected browser."""

    name = "browser_open_url"
    description = "Open a URL in a new tab of the CDP-connected browser."
    action = "open_url"
    capability = Capability.OPEN_APPLICATION
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "minLength": 1, "maxLength": 2048},
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def is_available(self) -> bool:
        return self._devtools.is_available()

    async def execute(self, *, url: str) -> dict[str, Any]:
        tab = await asyncio.to_thread(self._devtools.open_tab, url)
        return {
            "browser": self._profile.name,
            "opened": url,
            "tab_id": tab.id,
        }
        
# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def build_browser_tools(
    profiles: tuple[BrowserProfile, ...],
) -> tuple[Tool, ...]:
    """Build the tool set for every installed Chromium browser."""
    
    # If the extension is connected, hide the CDP tab tools entirely.
    # The extension does everything CDP does for tabs, plus more, and
    # does not require the debug port. Having both visible confused the
    # model.
    try:
        from app.automation.chrome_extension_bridge import get_bridge
        bridge = get_bridge()
        for profile in profiles:
            if bridge.is_available(profile.name):
                # Skip this profile's CDP tools — extension covers it.
                continue
    except Exception:
        pass
    
    tools: list[Tool] = []
    for profile in profiles:
        if not profile.is_installed():
            continue
        devtools = BrowserDevTools(
            port=profile.debug_port,
            browser_name=profile.display_name,
        )
        tools.append(_named(
            BrowserOpenUrlTool,
            f"{profile.name}_open_url",
            (
                f"Open a URL in a new tab of the running {profile.display_name} "
                f"instance that has its debug port open. Use this instead of "
                f"the generic open_url when the user wants the URL in the "
                f"same browser Azmuth can control tabs in."
            ),
            profile, devtools,
        ))

        tools.append(_named(
            BrowserFocusTabTool,
            f"{profile.name}_focus_tab",
            (
                f"Bring a specific {profile.display_name} tab to the "
                f"foreground by title/url substring. Use this before "
                f"typing into the page, or before pressing keys that "
                f"should affect this tab."
            ),
            profile, devtools,
        ))

        tools.append(_named(
            BrowserListTabsTool,
            f"{profile.name}_list_tabs",
            (
                f"List every open tab in {profile.display_name}, with "
                f"title, url, and target id. Requires "
                f"{profile.display_name} to be running with its debug "
                f"port ({profile.debug_port}) open."
            ),
            profile, devtools,
        ))
        tools.append(_named(
            BrowserCloseTabTool,
            f"{profile.name}_close_tab",
            (
                f"Close a {profile.display_name} tab whose title or URL "
                f"contains the given text (case-insensitive). Requires "
                f"{profile.display_name} in debug mode."
            ),
            profile, devtools,
        ))
        tools.append(_named(
            BrowserLaunchDebugTool,
            f"{profile.name}_launch_debug",
            (
                f"Close {profile.display_name} and relaunch it with the "
                f"debug port open so tab tools work."
            ),
            profile, devtools,
        ))
        
        tools.append(_named(
            BrowserEvalJsTool,
            f"{profile.name}_eval_js",
            (
                f"Run JavaScript in a specific {profile.display_name} tab. "
                f"Given a tab title/url substring and a JavaScript "
                f"expression, executes it in the page context and returns "
                f"the result. Use this to interact with web page content "
                f"that UI Automation cannot see — read text, click "
                f"elements by selector, fill forms."
            ),
            profile, devtools,
        ))
        tools.append(_named(
            BrowserClickOnPageTool,
            f"{profile.name}_click_on_page",
            (
                f"Click an element on a web page in a {profile.display_name} "
                f"tab by its visible text. Example: "
                f"click_on_page(tab_match='deepseek', text='Settings'). "
                f"Use this when UI Automation can't see the element — "
                f"web page buttons are usually invisible to Windows UI tools."
            ),
            profile, devtools,
        ))

        tools.append(_named(
            BrowserNavigateTabTool,
            f"{profile.name}_navigate_tab",
            (
                f"Navigate an existing {profile.display_name} tab to a URL "
                f"WITHOUT opening a new tab. Use this for 'search X in "
                f"existing tab' requests. Example: "
                f"{profile.name}_navigate_tab(tab_match='youtube', "
                f"url='https://www.youtube.com/results?search_query=parithabangal')"
            ),
            profile, devtools,
        ))

    return tuple(tools)


def _named(
    base_cls: type[Tool],
    name: str,
    description: str,
    profile: BrowserProfile,
    devtools: BrowserDevTools,
) -> Tool:
    """Instantiate ``base_cls`` with per-browser name and description."""
    tool = base_cls(profile, devtools)  # type: ignore[call-arg]
    object.__setattr__(tool, "name", name)
    object.__setattr__(tool, "description", description)
    return tool


# --------------------------------------------------------------------------
# Execute JavaScript in a tab
# --------------------------------------------------------------------------
class BrowserEvalJsTool(BrowserToolBase):
    """Run JavaScript in a specific browser tab."""

    name = "browser_eval_js"
    description = "Run JavaScript in a specific browser tab."
    action = "eval_js"
    capability = Capability.ADVANCED_AUTOMATION
    risk_level = RiskLevel.HIGH
    requires_confirmation = True
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_match": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "Substring of the tab title or URL.",
            },
            "javascript": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4000,
                "description": "JavaScript to evaluate in the tab.",
            },
        },
        "required": ["tab_match", "javascript"],
        "additionalProperties": False,
    }

    def is_available(self) -> bool:
        # Hide CDP tools when the extension covers this browser — one
        # family at a time keeps the model from mixing tool names.
        try:
            from app.automation.chrome_extension_bridge import get_bridge
            if get_bridge().is_available(self._profile.name):
                return False
        except Exception:
            pass
        return self._devtools.is_available()

    async def execute(self, *, tab_match: str, javascript: str) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            tab = self._devtools.find_tab(tab_match)
            if tab is None:
                raise AutomationError(
                    f"no {self._profile.display_name} tab matched {tab_match!r}"
                )
            value = self._devtools.eval_js(tab.id, javascript)
            return {
                "browser": self._profile.name,
                "tab_title": tab.title,
                "result": value,
            }

        return await asyncio.to_thread(_do)


class BrowserFocusTabTool(BrowserToolBase):
    """Bring a specific browser tab to the foreground."""

    name = "browser_focus_tab"
    description = "Bring a specific browser tab to the foreground."
    action = "focus_tab"
    capability = Capability.ADVANCED_AUTOMATION
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = False
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "match": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "required": ["match"],
        "additionalProperties": False,
    }

    def is_available(self) -> bool:
        return self._devtools.is_available()

    async def execute(self, *, match: str) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            tab = self._devtools.find_tab(match)
            if tab is None:
                raise AutomationError(
                    f"no {self._profile.display_name} tab matched {match!r}"
                )
            self._devtools.focus_tab(tab.id)
            return {"browser": self._profile.name, "focused_tab": tab.title}

        return await asyncio.to_thread(_do)


class BrowserClickOnPageTool(BrowserToolBase):
    """Click an element on a web page by its visible text."""

    name = "browser_click_on_page"
    description = "Click an element on a web page by its visible text."
    action = "click_on_page"
    capability = Capability.ADVANCED_AUTOMATION
    risk_level = RiskLevel.HIGH
    requires_confirmation = True
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_match": {"type": "string", "minLength": 1, "maxLength": 200},
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "Visible text of the element to click.",
            },
            "tag": {
                "type": "string",
                "maxLength": 40,
                "description": "Optional: restrict to a specific HTML tag (button, a, div).",
            },
        },
        "required": ["tab_match", "text"],
        "additionalProperties": False,
    }

    def is_available(self) -> bool:
        return self._devtools.is_available()

    async def execute(
        self, *, tab_match: str, text: str, tag: str | None = None
    ) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            tab = self._devtools.find_tab(tab_match)
            if tab is None:
                raise AutomationError(
                    f"no {self._profile.display_name} tab matched {tab_match!r}"
                )

            # Build a JS snippet that finds the element and clicks it.
            # Handles <button>, <a>, and any element whose visible text
            # contains the target string. Reports what was clicked.
            tag_clause = f"'{tag}'" if tag else "null"
            js = f"""
            (() => {{
                const target = {json.dumps(text)};
                const tagFilter = {tag_clause};
                const lower = target.toLowerCase();
                const candidates = Array.from(
                    document.querySelectorAll('button, a, [role="button"], [role="menuitem"], [role="tab"], div, span')
                );
                for (const el of candidates) {{
                    if (tagFilter && el.tagName.toLowerCase() !== tagFilter.toLowerCase()) continue;
                    if (el.offsetParent === null) continue;
                    const txt = (el.innerText || el.textContent || '').trim();
                    if (!txt) continue;
                    if (txt.toLowerCase().includes(lower)) {{
                        el.scrollIntoView({{block: 'center'}});
                        el.click();
                        return {{clicked: true, tag: el.tagName, text: txt.slice(0, 80)}};
                    }}
                }}
                return {{clicked: false}};
            }})()
            """
            value = self._devtools.eval_js(tab.id, js)
            return {
                "browser": self._profile.name,
                "tab_title": tab.title,
                "result": value,
            }

        return await asyncio.to_thread(_do)


class BrowserNavigateTabTool(BrowserToolBase):
    """Navigate an existing tab to a URL. Does NOT open a new tab."""

    name = "browser_navigate_tab"
    description = "Navigate an existing tab to a URL. Does not open a new tab."
    action = "navigate_tab"
    capability = Capability.ADVANCED_AUTOMATION
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = False
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tab_match": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "Substring of the tab title or URL.",
            },
            "url": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "Full URL to load in the tab.",
            },
        },
        "required": ["tab_match", "url"],
        "additionalProperties": False,
    }

    def is_available(self) -> bool:
        return self._devtools.is_available()

    async def execute(self, *, tab_match: str, url: str) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            tab = self._devtools.find_tab(tab_match)
            if tab is None:
                raise AutomationError(
                    f"no {self._profile.display_name} tab matched {tab_match!r}"
                )
            self._devtools.navigate_tab(tab.id, url)
            return {
                "browser": self._profile.name,
                "tab_title_before": tab.title,
                "navigated_to": url,
            }

        return await asyncio.to_thread(_do)


__all__ = [
    "BrowserClickOnPageTool",
    "BrowserCloseTabTool",
    "BrowserEvalJsTool",
    "BrowserFocusTabTool",
    "BrowserLaunchDebugTool",
    "BrowserListTabsTool",
    "BrowserNavigateTabTool",
    "BrowserOpenUrlTool",
    "BrowserToolBase",
    "build_browser_tools",
]
