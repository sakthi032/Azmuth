"""Tool interface and metadata types.

A :class:`Tool` is a small, self-describing unit of action. It declares what
it does, what it needs, how risky it is, and — crucially — *what capability
it requires*. Authorization happens outside the tool, in the
:class:`~app.tools.manager.ToolManager`; a tool never checks permissions
itself. That is what makes the pipeline auditable in one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from app.security.capabilities import Capability


class RiskLevel(str, Enum):
    """How consequential a tool call is.

    Purely descriptive metadata. It does *not* decide whether a tool runs —
    the capability policy does. It exists so the UI, the LLM prompt and
    future policy tables have a coarse signal to reason about.
    """

    LOW = "low"           # read-only or trivially reversible
    MEDIUM = "medium"     # side effects on running apps / non-critical files
    HIGH = "high"         # potentially destructive
    CRITICAL = "critical" # data loss or system-level change possible


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    """A lightweight, live-object-free description of a tool.

    This is what flows to the LLM (for tool selection) and to the UI (for
    the activity panel). It intentionally carries no reference to the tool
    instance, so metadata can be logged and serialised without surprises.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    capability: Capability
    risk_level: RiskLevel
    requires_confirmation: bool
    can_run_async: bool

    @property
    def dangerous(self) -> bool:
        """Convenience flag for prompts and UI badges."""
        return self.requires_confirmation or self.risk_level in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "capability": self.capability.value,
            "risk_level": self.risk_level.value,
            "requires_confirmation": self.requires_confirmation,
            "can_run_async": self.can_run_async,
            "dangerous": self.dangerous,
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outcome of one tool invocation.

    The manager constructs this. Tools themselves return whatever their
    ``execute`` yields — a plain value, a mapping, or a :class:`ToolResult`
    if they want to convey extra metadata. Either way, callers receive a
    :class:`ToolResult`.
    """

    success: bool
    tool: str
    output: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "tool": self.tool,
            "output": self.output,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 3),
            "metadata": dict(self.metadata),
        }


class Tool(ABC):
    """Base class for every Azmuth tool.

    Subclasses declare the class attributes below and implement
    :meth:`execute`. ``execute`` may be a coroutine; the manager awaits it
    either way, so sync tools remain trivial to write.

    Tools must not check permissions, emit events, or touch the session.
    Those are the manager's job.
    """

    # -- required -----------------------------------------------------------
    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability]

    # -- optional -----------------------------------------------------------
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    #: If True, the manager will demand confirmation even when the capability
    #: policy does not. The reverse (a tool *disabling* confirmation for a
    #: destructive capability) is not possible.
    requires_confirmation: ClassVar[bool] = False
    #: Hint for the task manager (STEP 9): is it safe to also submit this
    #: tool for background execution? Read-only / idempotent tools usually
    #: set this to True.
    can_run_async: ClassVar[bool] = True

    # -- lifecycle ----------------------------------------------------------
    def is_available(self) -> bool:
        """Return True if the tool can run in the current environment.

        Default implementation returns True. Tools that depend on optional
        runtime (pyautogui, a browser driver, a specific OS) override this.
        Must not raise.
        """
        return True

    # -- execution ----------------------------------------------------------
    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Perform the action.

        Arguments have already been validated against :attr:`parameters`.
        Raise any exception to signal failure; the manager will wrap it in
        :class:`~app.core.errors.ToolExecutionError`.
        """

    # -- introspection ------------------------------------------------------
    @classmethod
    def metadata(cls) -> ToolMetadata:
        return ToolMetadata(
            name=cls.name,
            description=cls.description,
            parameters=cls.parameters,
            capability=cls.capability,
            risk_level=cls.risk_level,
            requires_confirmation=cls.requires_confirmation,
            can_run_async=cls.can_run_async,
        )