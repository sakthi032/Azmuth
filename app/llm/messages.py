
"""Data types shared by every LLM provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"          # a tool's reply, when native tool calls are used


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str
    tool_call_id: str | None = None   # for Role.TOOL messages

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            object.__setattr__(self, "role", Role(self.role))


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A tool the model may call, in OpenAI's native function schema."""
    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """A tool call the model asked for, parsed from a native response."""
    id: str
    name: str
    arguments: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True, slots=True)
class ChatRequest:
    messages: tuple[Message, ...]
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    stop: tuple[str, ...] = ()
    request_id: str | None = None
    tools: tuple[ToolDefinition, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    model: str
    provider: str
    finish_reason: str | None = None
    usage: Usage | None = None
    latency_ms: float = 0.0
    tool_calls: tuple[ToolCallRequest, ...] = ()


@dataclass(frozen=True, slots=True)
class StreamChunk:
    delta: str
    index: int = 0
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    provider: str
    model: str
    context_window: int | None = None
    supports_streaming: bool = True
    extra: dict[str, Any] = field(default_factory=dict)
