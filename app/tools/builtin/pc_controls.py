"""PC control tools — volume, mute, brightness, lock, shutdown timer,
open folder, open website. All Creator-only via the capability policy.

Destructive operations (``lock_screen``, ``shutdown_timer``) require
confirmation because they declare ``CONTROL_POWER`` whose policy has
``requires_confirmation=True``. Reversible operations declare
``CONTROL_SYSTEM_STATE`` which has no confirmation.

No tool accepts a caller-supplied shell command. Arguments are
validated against a JSON schema before reaching the controller.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.automation.pc_controls import PCController
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


class SetVolumeTool(Tool):
    name: ClassVar[str] = "set_volume"
    description: ClassVar[str] = (
        "Set the system output volume. 0 = silent, 100 = maximum. "
        "Reversible. Creator only."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Volume level 0-100.",
            },
        },
        "required": ["level"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CONTROL_SYSTEM_STATE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, pc: PCController) -> None:
        self._pc = pc

    def is_available(self) -> bool:
        """Return False when pycaw or comtypes is not installed."""
        try:
            import pycaw.pycaw  # noqa: F401
            import comtypes  # noqa: F401
            return True
        except ImportError:
            return False

    def unavailable_reason(self) -> str | None:
        return "set_volume requires pycaw (pip install pycaw comtypes)"

    async def execute(self, *, level: int) -> dict[str, Any]:
        result = await self._pc.set_volume(int(level))
        return {"success": result.success, "level": int(level)}


class MuteTool(Tool):
    name: ClassVar[str] = "mute"
    description: ClassVar[str] = (
        "Mute or unmute the system output. Reversible. Creator only."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "muted": {
                "type": "boolean",
                "description": "True to mute, False to unmute.",
            },
        },
        "required": ["muted"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CONTROL_SYSTEM_STATE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, pc: PCController) -> None:
        self._pc = pc

    def is_available(self) -> bool:
        """Return False when pycaw or comtypes is not installed."""
        try:
            import pycaw.pycaw  # noqa: F401
            import comtypes  # noqa: F401
            return True
        except ImportError:
            return False

    def unavailable_reason(self) -> str | None:
        return "mute requires pycaw (pip install pycaw comtypes)"

    async def execute(self, *, muted: bool) -> dict[str, Any]:
        result = await self._pc.mute(bool(muted))
        return {"success": result.success, "muted": bool(muted)}


class SetBrightnessTool(Tool):
    name: ClassVar[str] = "set_brightness"
    description: ClassVar[str] = (
        "Set display brightness. 0 = dimmest, 100 = brightest. "
        "Reversible. Creator only. Prefer this over set_setting('brightness')."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Brightness 0-100.",
            },
        },
        "required": ["level"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CONTROL_SYSTEM_STATE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, pc: PCController) -> None:
        self._pc = pc

    async def execute(self, *, level: int) -> dict[str, Any]:
        result = await self._pc.set_brightness(int(level))
        return {"success": result.success, "level": int(level)}


class LockScreenTool(Tool):
    name: ClassVar[str] = "lock_screen"
    description: ClassVar[str] = (
        "Lock the Windows session immediately. User must log back in. "
        "Creator only; requires confirmation."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CONTROL_POWER
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    requires_confirmation: ClassVar[bool] = False  # policy provides it
    can_run_async: ClassVar[bool] = True

    def __init__(self, pc: PCController) -> None:
        self._pc = pc

    async def execute(self) -> dict[str, Any]:
        result = await self._pc.lock_screen()
        return {"success": result.success}


class ShutdownTimerTool(Tool):
    name: ClassVar[str] = "shutdown_timer"
    description: ClassVar[str] = (
        "Schedule or cancel a Windows shutdown. action='set' with "
        "seconds>0 schedules; action='cancel' aborts. Creator only; "
        "requires confirmation."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "cancel"],
                "description": "'set' to schedule, 'cancel' to abort.",
            },
            "seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": 86400,
                "description": (
                    "Delay before shutdown, only used when "
                    "action='set'. 0 = immediate."
                ),
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CONTROL_POWER
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, pc: PCController) -> None:
        self._pc = pc

    async def execute(
        self, *, action: str, seconds: int = 0
    ) -> dict[str, Any]:
        if action == "set":
            result = await self._pc.schedule_shutdown(int(seconds))
            return {
                "success": result.success,
                "action": "set",
                "seconds": int(seconds),
                "message": result.message,
            }
        if action == "cancel":
            result = await self._pc.cancel_shutdown()
            return {
                "success": result.success,
                "action": "cancel",
                "message": result.message,
            }
        raise ValueError(f"invalid action {action!r}")


class OpenFolderTool(Tool):
    name: ClassVar[str] = "open_folder"
    description: ClassVar[str] = (
        "Open a folder in Windows Explorer. Path must exist and be a "
        "directory. Creator only."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CONTROL_SYSTEM_STATE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, pc: PCController) -> None:
        self._pc = pc

    async def execute(self, *, path: str) -> dict[str, Any]:
        result = await self._pc.open_folder(path)
        return {"success": result.success, "path": result.value}


class OpenWebsiteTool(Tool):
    name: ClassVar[str] = "open_website"
    description: ClassVar[str] = (
        "Open a URL in the user's default browser. Only http:// and "
        "https:// URLs are accepted. Creator only."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CONTROL_SYSTEM_STATE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, pc: PCController) -> None:
        self._pc = pc

    async def execute(self, *, url: str) -> dict[str, Any]:
        result = await self._pc.open_website(url)
        return {"success": result.success, "url": url}


__all__ = [
    "LockScreenTool",
    "MuteTool",
    "OpenFolderTool",
    "OpenWebsiteTool",
    "SetBrightnessTool",
    "SetVolumeTool",
    "ShutdownTimerTool",
]