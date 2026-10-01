
"""Tools that interact with the Windows system.

Every tool here wraps a method on WindowsAutomation (STEP 11). The
automation layer enforces the sandbox and allowlist; the capability
policy enforces who may call them. Both gates remain in effect — a tool
that reaches these methods has already passed the permission check.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.automation.base import WindowsAutomation
from app.core.errors import AutomationError, ProviderUnavailableError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


class OpenApplicationTool(Tool):
    """Launch a whitelisted application by alias."""

    name: ClassVar[str] = "open_application"
    description: ClassVar[str] = (
        "Open a local application by name. Only apps on the configured "
        "allowlist can be launched; arbitrary paths are refused. "
        "Examples: 'chrome', 'notepad', 'calc'."
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
        # Return a compact list; the full one is usually too long.
        summary = [p.as_dict() for p in processes[:60]]
        return {"count": len(processes), "processes": summary}
