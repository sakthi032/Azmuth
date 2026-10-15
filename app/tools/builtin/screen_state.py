"""Observation tools — let the model see the screen before acting."""

from __future__ import annotations

from typing import Any, ClassVar

from app.automation.uia import UIAutomationController
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


class GetScreenStateTool(Tool):
    """Return the UI tree of the focused window."""

    name: ClassVar[str] = "get_screen_state"
    description: ClassVar[str] = (
        "Return a structured snapshot of the currently-focused window: "
        "every interactive element (buttons, edit fields, menus, tabs, "
        "links) with its name, type, coordinates, enabled state, and "
        "current value. ALWAYS call this before clicking or typing so "
        "you know what is actually on screen. Also call it after an "
        "action that should change the screen, to verify it worked. "
        "Read-only."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "interactive_only": {
                "type": "boolean",
                "description": (
                    "If true (default), return only interactive elements. "
                    "Set false to see the whole tree — useful when "
                    "debugging why an element is missing."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Max elements to return (default 80).",
            },
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
        self,
        *,
        interactive_only: bool = True,
        limit: int = 80,
    ) -> dict[str, Any]:
        return await self._uia.get_window_tree(
            window=None,
            interactive_only=interactive_only,
            limit=limit,
        )


class GetWindowStateTool(Tool):
    """Return the UI tree of a named window."""

    name: ClassVar[str] = "get_window_state"
    description: ClassVar[str] = (
        "Like get_screen_state, but for a specific window by title "
        "substring. Use this when the window you want to inspect is not "
        "currently focused, or when you need to confirm which window an "
        "element belongs to. Examples: window='notepad', window='Chrome'."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "window": {"type": "string", "minLength": 1, "maxLength": 200},
            "interactive_only": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["window"],
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
        self,
        *,
        window: str,
        interactive_only: bool = True,
        limit: int = 80,
    ) -> dict[str, Any]:
        return await self._uia.get_window_tree(
            window=window,
            interactive_only=interactive_only,
            limit=limit,
        )

class VerifyElementTool(Tool):
    """Check whether an element exists on screen, without clicking it."""

    name: ClassVar[str] = "verify_element"
    description: ClassVar[str] = (
        "Check whether a named element exists in the focused window (or "
        "a named window). Read-only — does not click or focus anything. "
        "Use this to VERIFY that a previous action worked: after clicking "
        "'Save', call verify_element(name='Saved') or re-observe to "
        "confirm the UI changed as expected. Returns present=true/false "
        "plus the matched element's details."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 200},
            "window": {
                "type": "string",
                "description": "Optional window title substring.",
            },
            "control_type": {
                "type": "string",
                "description": "Optional: 'Button', 'Edit', etc.",
            },
        },
        "required": ["name"],
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
        self,
        *,
        name: str,
        window: str | None = None,
        control_type: str | None = None,
    ) -> dict[str, Any]:
        elements = await self._uia.find_elements(
            name=name, control_type=control_type, limit=5
        )
        if not elements:
            return {"present": False, "name": name}
        best = elements[0]
        return {
            "present": True,
            "name": best.name,
            "control_type": best.control_type,
            "is_enabled": best.is_enabled,
            "score": round(best.score, 2),
        }


__all__ = [
    "GetScreenStateTool",
    "GetWindowStateTool",
    "VerifyElementTool",
]
