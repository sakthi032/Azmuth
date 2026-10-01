"""Data types shared by every LLM provider.

These are plain immutable dataclasses. No provider-specific shape leaks in:
an OpenAI response, an Anthropic response, and a local model's response all
land in the same :class:`ChatResponse`. That is what lets the brain stay
provider-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    """Message author. Subclasses ``str`` so it serialises cleanly."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Message:
    """A single turn in a conversation."""

    role: Role
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            # Accept "user" / "system" / "assistant" strings for convenience.
            object.__setattr__(self, "role", Role(self.role))


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting, when the provider reports it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Provider-independent request.

    ``model`` is an override. When ``None`` the provider uses its configured
    default. Normal call sites should leave it unset.
    """

    messages: tuple[Message, ...]
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    stop: tuple[str, ...] = ()
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Provider-independent response."""

    content: str
    model: str
    provider: str
    finish_reason: str | None = None
    usage: Usage | None = None
    latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One delta from a streaming response.

    Concatenating every ``delta`` in order must reproduce the same text a
    non-streaming ``chat()`` would have produced.
    """

    delta: str
    index: int = 0
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """What a provider can tell us about the model it is serving."""

    provider: str
    model: str
    context_window: int | None = None
    supports_streaming: bool = True
    extra: dict[str, object] = field(default_factory=dict)