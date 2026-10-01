"""LLM provider registry factory.

Every provider that ships with Azmuth is registered here. Third-party
code can register additional providers on the returned registry without
touching this module.
"""

from __future__ import annotations

from app.core.registry import Registry
from app.llm.base import LLMProvider
from app.llm.providers.nvidia_nim import NvidiaNIMProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.providers.stub import StubLLMProvider


def build_llm_registry() -> Registry[LLMProvider]:
    """Return a fresh registry with the built-in providers registered."""
    registry: Registry[LLMProvider] = Registry("LLM")
    registry.register(StubLLMProvider.name, StubLLMProvider)
    registry.register(OpenAICompatibleProvider.name, OpenAICompatibleProvider)
    registry.register(OllamaProvider.name, OllamaProvider)
    registry.register(NvidiaNIMProvider.name, NvidiaNIMProvider)
    return registry


__all__ = [
    "NvidiaNIMProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "StubLLMProvider",
    "build_llm_registry",
]