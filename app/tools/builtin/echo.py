"""Echo tool — the simplest honest sample.

Read-only, no side effects, requires only CHAT. Useful for end-to-end tests
of the tool pipeline and for the brain's development loop.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool


class EchoTool(Tool):
    """Return the message it was given."""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = (
        "Return the given message unchanged. Development aid."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "minLength": 1, "maxLength": 4096},
        },
        "required": ["message"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.CHAT
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    async def execute(self, *, message: str) -> str:
        return message