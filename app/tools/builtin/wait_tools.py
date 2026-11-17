"""Wait tools — poll until a UI element appears or disappears."""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

from app.automation.uia import UIAutomationController
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool

_POLL_INTERVAL = 0.25


class WaitForElementTool(Tool):
    """Poll until a named element appears in a window (or timeout)."""

    name: ClassVar[str] = "wait_for_element"
    description: ClassVar[str] = (
        "Wait up to `timeout` seconds for a UI element to appear. After "
        "an action that triggers a delay, use this to block until the "
        "next state is ready. Example: after clicking 'Download', wait "
        "for 'Open' to appear. Use control_type to narrow the match "
        "when a short name would match multiple elements — e.g. "
        "name='New', control_type='MenuItem' finds the File menu item, "
        "not the 'Add New Tab' button."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 200},
            "window": {"type": "string", "maxLength": 200},
            "control_type": {
                "type": "string",
                "maxLength": 40,
                "description": (
                    "Optional filter: 'MenuItem', 'Button', 'Edit', "
                    "'TabItem', 'Document', 'Text'. Highly recommended "
                    "for single-word queries."
                ),
            },
            "timeout": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 120.0,
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
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        attempts = 0

        while True:
            attempts += 1
            try:
                elements = await self._uia.find_elements(
                    name=name,
                    control_type=control_type,
                    limit=5,
                    window=window,
                )
            except Exception:
                elements = []

            if elements:
                elapsed = time.perf_counter() - started
                best = elements[0]
                return {
                    "found": True,
                    "name": best.name,
                    "control_type": best.control_type,
                    "waited_seconds": round(elapsed, 2),
                    "attempts": attempts,
                }

            elapsed = time.perf_counter() - started
            if elapsed >= timeout:
                return {
                    "found": False,
                    "name": name,
                    "control_type": control_type,
                    "waited_seconds": round(elapsed, 2),
                    "attempts": attempts,
                    "reason": (
                        f"element {name!r}"
                        + (f" of type {control_type}" if control_type else "")
                        + f" did not appear within {timeout}s"
                    ),
                }

            await asyncio.sleep(_POLL_INTERVAL)


class WaitForAbsenceTool(Tool):
    """Poll until a named element is gone from a window (or timeout)."""

    name: ClassVar[str] = "wait_for_absence"
    description: ClassVar[str] = (
        "Wait up to `timeout` seconds for a UI element to disappear. "
        "Use control_type to narrow the match when the name would match "
        "multiple elements. Example: name='Loading', control_type='Text' "
        "waits for the spinner label specifically."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 200},
            "window": {"type": "string", "maxLength": 200},
            "control_type": {
                "type": "string",
                "maxLength": 40,
            },
            "timeout": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 120.0,
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
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        attempts = 0

        while True:
            attempts += 1
            try:
                elements = await self._uia.find_elements(
                    name=name,
                    control_type=control_type,
                    limit=5,
                    window=window,
                )
            except Exception:
                elements = []

            if not elements:
                elapsed = time.perf_counter() - started
                return {
                    "gone": True,
                    "name": name,
                    "control_type": control_type,
                    "waited_seconds": round(elapsed, 2),
                    "attempts": attempts,
                }

            elapsed = time.perf_counter() - started
            if elapsed >= timeout:
                return {
                    "gone": False,
                    "name": name,
                    "control_type": control_type,
                    "waited_seconds": round(elapsed, 2),
                    "attempts": attempts,
                    "reason": (
                        f"element {name!r}"
                        + (f" of type {control_type}" if control_type else "")
                        + f" was still present after {timeout}s"
                    ),
                }

            await asyncio.sleep(_POLL_INTERVAL)


__all__ = ["WaitForAbsenceTool", "WaitForElementTool"]