"""LLM abstraction layer.

Public surface:

    from app.llm import LLMManager, ChatRequest, Message, Role
    from app.llm import LLMRouter, ModelRegistry, ModelPurpose

Never import a concrete provider outside its own package.
"""

from app.llm.base import LLMProvider
from app.llm.manager import LLMManager
from app.llm.messages import (
    ChatRequest,
    ChatResponse,
    Message,
    ModelInfo,
    Role,
    StreamChunk,
    Usage,
)
from app.llm.providers import (
    NvidiaNIMProvider,
    OllamaProvider,
    StubLLMProvider,
    build_llm_registry,
)
from app.llm.registry import (
    ModelPurpose,
    ModelRegistry,
    ModelSpec,
    build_default_registry,
)
from app.llm.router import LLMRouter

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "LLMManager",
    "LLMProvider",
    "LLMRouter",
    "Message",
    "ModelInfo",
    "ModelPurpose",
    "ModelRegistry",
    "ModelSpec",
    "NvidiaNIMProvider",
    "OllamaProvider",
    "Role",
    "StreamChunk",
    "StubLLMProvider",
    "Usage",
    "build_default_registry",
    "build_llm_registry",
]