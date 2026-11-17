"""Visual target tool — find UI elements and click them, verified."""

from __future__ import annotations

from typing import Any, ClassVar

from app.automation.resolver import TargetResolver
from app.automation.verifier import Verifier
from app.config.logging_setup import get_logger
from app.core.errors import AutomationError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool

logger = get_logger("tools.visual")


class ClickVisualTool(Tool):
    """Locate a UI target via UIA → CV → VLM and click it, with verification."""

    name: ClassVar[str] = "click_visual"
    description: ClassVar[str] = (
        "Click a UI element described in natural language, using a "
        "three-tier strategy: UIA → OCR → vision model. After clicking, "
        "the tool verifies that the UI actually changed. If it did not "
        "change, the tool retries once with a re-resolve. Use this when "
        "the target has no reliable UIA name, or when describing it "
        "visually. If you already know the element's exact visible name, "
        "prefer click_element — it is faster and does not need a "
        "screenshot."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
            },
            "window": {"type": "string", "maxLength": 200},
            "allow_vlm": {"type": "boolean"},
            "expect_change": {
                "type": "boolean",
                "description": (
                    "Default true. If the click is not expected to "
                    "change the UI (e.g. a checkbox that toggles state "
                    "without any visible layout change), set false to "
                    "skip verification."
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

    def __init__(
        self,
        resolver: TargetResolver,
        input_ctl,
        verifier: Verifier | None = None,
    ) -> None:
        self._resolver = resolver
        self._input = input_ctl
        self._verifier = verifier

    def is_available(self) -> bool:
        return True

    async def execute(
        self,
        *,
        description: str,
        window: str | None = None,
        allow_vlm: bool = True,
        expect_change: bool = True,
    ) -> dict[str, Any]:
        from app.automation.self_guard import is_self_foreground

        if is_self_foreground():
            raise AutomationError(
                "refusing to click: Azmuth's own window has focus."
            )

        # ---- Attempt 1 ----
        before = None
        if expect_change and self._verifier is not None:
            before = await self._verifier.snapshot(window)

        resolution = await self._resolver.resolve(
            description, window=window, allow_vlm=allow_vlm
        )
        await self._input.click(resolution.click_x, resolution.click_y)

        if not expect_change or self._verifier is None or before is None:
            return resolution.as_dict()

        # Give the UI a moment to react before checking.
        import asyncio
        await asyncio.sleep(0.4)

        after = await self._verifier.snapshot(window)
        if Verifier.changed(before, after):
            result = resolution.as_dict()
            result["verified"] = True
            result["change"] = Verifier.describe_change(before, after)
            return result

        # ---- Attempt 2 (retry) ----
        logger.info(
            "click produced no visible change, retrying",
            extra={"event": "CLICK_RETRY", "description": description},
        )
        before2 = after
        try:
            resolution2 = await self._resolver.resolve(
                description, window=window, allow_vlm=allow_vlm
            )
        except AutomationError:
            # Re-resolve failed — report the first click as unverified.
            result = resolution.as_dict()
            result["verified"] = False
            result["reason"] = (
                "no UI change detected and re-resolve failed. "
                "The click may have been a no-op, or the UI change is "
                "delayed. Try again or use a different description."
            )
            return result

        await self._input.click(resolution2.click_x, resolution2.click_y)
        await asyncio.sleep(0.4)

        after2 = await self._verifier.snapshot(window)
        result = resolution2.as_dict()
        if Verifier.changed(before2, after2):
            result["verified"] = True
            result["change"] = Verifier.describe_change(before2, after2)
            result["retried"] = True
        else:
            result["verified"] = False
            result["retried"] = True
            result["reason"] = (
                "clicked twice but no visible UI change was detected. "
                "The target may not respond to clicks, or the change "
                "is not reflected in the accessibility tree."
            )
        return result


__all__ = ["ClickVisualTool"]