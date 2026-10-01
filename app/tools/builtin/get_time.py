"""Return the current time.

Also read-only. Uses only the stdlib. Included so that a *useful* builtin
exists alongside ``echo`` without pulling in any dependency or touching the
OS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


class GetTimeTool(Tool):
    """Return the current UTC time in ISO 8601."""

    name: ClassVar[str] = "get_time"
    description: ClassVar[str] = (
        "Return the current UTC date and time in ISO 8601 format."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.USE_BASIC_TOOLS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    async def execute(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "iso": now.isoformat(timespec="seconds"),
            "epoch": now.timestamp(),
            "timezone": "UTC",
        }