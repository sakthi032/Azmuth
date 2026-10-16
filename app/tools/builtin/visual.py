"""Visual target tool — find UI elements by name or description."""

from __future__ import annotations

from typing import Any, ClassVar

from app.automation.resolver import TargetResolver
from app.core.errors import AutomationError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


class ClickVisualTool(Tool):
    """Locate a UI target via UIA → CV → VLM and click it."""

    name: ClassVar[str] = "click_visual"
    description: ClassVar[str] = (
        "Click a UI element described in natural language, using a "
        "three-tier strategy: UIA (fast, exact) → OCR / screenshot "
        "analysis → vision model. Use this when the target has no "
        "reliable UIA name, or when describing it visually: 'the "
        "download icon beside the second file', 'the green Submit "
        "button', 'the Save link in the top-right'. If you already know "
        "the element's exact visible name, prefer click_element — it is "
        "faster and does not need a screenshot."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "description": (
                    "Natural-language description of the target."
                ),
            },
            "window": {
                "type": "string",
                "description": (
                    "Optional window title substring. Scopes the search "
                    "so an identical label in another app is not picked."
                ),
            },
            "allow_vlm": {
                "type": "boolean",
                "description": (
                    "Set false to skip the network VLM tier and only use "
                    "UIA + OCR. Useful when offline or conserving quota."
                ),
            },
        },
        "required": ["description"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.ADVANCED_AUTOMATION
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(self, resolver: TargetResolver, input_ctl) -> None:
        self._resolver = resolver
        self._input = input_ctl

    def is_available(self) -> bool:
        return True

    async def execute(
        self,
        *,
        description: str,
        window: str | None = None,
        allow_vlm: bool = True,
    ) -> dict[str, Any]:
        from app.automation.self_guard import is_self_foreground

        if is_self_foreground():
            raise AutomationError(
                "refusing to click: Azmuth's own window has focus."
            )

        resolution = await self._resolver.resolve(
            description, window=window, allow_vlm=allow_vlm
        )
        await self._input.click(resolution.click_x, resolution.click_y)
        return resolution.as_dict()


__all__ = ["ClickVisualTool"]