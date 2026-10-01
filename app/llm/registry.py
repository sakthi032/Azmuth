
"""Model registry - what models exist and what they are for.

Cloud models (Groq, Gemini, NVIDIA) are the primary path because they
are fast and free at this tier. Local Ollama models serve two roles:
embeddings (must be local - per-statement and frequently called) and
offline fallback for chat/coding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ModelPurpose(str, Enum):
    CHAT = "chat"
    CODING = "coding"
    REASONING = "reasoning"
    FAST = "fast"
    VISION = "vision"
    EMBEDDING = "embedding"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    provider: str
    purposes: frozenset[ModelPurpose] = field(default_factory=frozenset)
    context_window: int | None = None
    is_local: bool = True
    tier: int = 1
    description: str = ""

    def supports(self, purpose: ModelPurpose) -> bool:
        return purpose in self.purposes

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "provider": self.provider,
            "purposes": sorted(p.value for p in self.purposes),
            "context_window": self.context_window,
            "is_local": self.is_local,
            "tier": self.tier,
            "description": self.description,
        }


class ModelRegistry:
    def __init__(self) -> None:
        self._models: list[ModelSpec] = []

    def register(self, spec: ModelSpec) -> None:
        self._models.append(spec)

    def clear(self) -> None:
        self._models.clear()

    def all(self) -> tuple[ModelSpec, ...]:
        return tuple(self._models)

    def by_purpose(
        self,
        purpose: ModelPurpose,
        *,
        provider: str | None = None,
        local_only: bool = False,
    ) -> tuple[ModelSpec, ...]:
        matches = [
            m for m in self._models
            if m.supports(purpose)
            and (provider is None or m.provider == provider)
            and (not local_only or m.is_local)
        ]
        matches.sort(key=lambda m: (m.tier, 0 if m.is_local else 1, m.name))
        return tuple(matches)

    def by_name(self, name: str) -> ModelSpec | None:
        for m in self._models:
            if m.name == name:
                return m
        return None

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted({m.provider for m in self._models}))

    def __len__(self) -> int:
        return len(self._models)

    def __iter__(self):
        return iter(self._models)



def build_default_registry() -> ModelRegistry:
    """NVIDIA-only registry, using account-verified models.

    Every model listed here was confirmed working by
    scripts/nvidia_availability.py. No model here is retired, and none
    requires local inference - the host machine sees no RAM pressure.
    """

    registry = ModelRegistry()

    # ---- CHAT: general conversation -------------------------------------
    registry.register(ModelSpec(
        name="nvidia/nemotron-3-super-120b-a12b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CHAT}),
        context_window=128_000,
        is_local=False,
        tier=1,
        description="Nemotron 3 Super 120B. Primary chat model.",
    ))
    registry.register(ModelSpec(
        name="z-ai/glm-5.3",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CHAT}),
        context_window=128_000,
        is_local=False,
        tier=2,
        description="GLM 5.3. Chat fallback.",
    ))
    registry.register(ModelSpec(
        name="z-ai/glm-5.3-flash",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CHAT}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="GLM 5.3 Flash. Fast chat fallback.",
    ))

    # ---- REASONING: hard problems, math, planning -----------------------
    registry.register(ModelSpec(
        name="nvidia/nemotron-3-ultra-550b-a55b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.REASONING}),
        context_window=128_000,
        is_local=False,
        tier=1,
        description="Nemotron 3 Ultra 550B. Primary reasoner.",
    ))
    registry.register(ModelSpec(
        name="nvidia/nemotron-3-super-120b-a12b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.REASONING}),
        context_window=128_000,
        is_local=False,
        tier=2,
        description="Nemotron 3 Super 120B. Reasoning fallback.",
    ))
    registry.register(ModelSpec(
        name="deepseek-ai/deepseek-v4.1-flash",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.REASONING}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="DeepSeek V4.1 Flash. Fast reasoning fallback.",
    ))
    
    registry.register(ModelSpec(
    name="llama-3.3-70b-versatile",
    provider="groq",
    purposes=frozenset({ModelPurpose.CHAT, ModelPurpose.REASONING}),
    context_window=128_000,
    is_local=False,
    tier=2,
    description="Groq Llama 3.3. Independent cloud fallback.",
    ))

    # ---- CODING ----------------------------------------------------------
    registry.register(ModelSpec(
        name="deepseek-ai/deepseek-v4.1-flash",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CODING}),
        context_window=128_000,
        is_local=False,
        tier=1,
        description="DeepSeek V4.1 Flash. Primary coding model.",
    ))
    registry.register(ModelSpec(
        name="nvidia/nemotron-3-super-120b-a12b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CODING}),
        context_window=128_000,
        is_local=False,
        tier=2,
        description="Nemotron 3 Super 120B. Generalist coding fallback.",
    ))
    registry.register(ModelSpec(
        name="z-ai/glm-5.3",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CODING}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="GLM 5.3. Coding fallback.",
    ))

    # ---- FAST: short lookups, quick questions ---------------------------
    registry.register(ModelSpec(
        name="nvidia/nemotron-3.5-lightning-30b-a3b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.FAST}),
        context_window=128_000,
        is_local=False,
        tier=1,
        description="Nemotron 3.5 Lightning 30B. Fast model for quick lookups.",
    ))
    registry.register(ModelSpec(
        name="openai/gpt-oss-20b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.FAST}),
        context_window=128_000,
        is_local=False,
        tier=2,
        description="GPT-OSS 20B. Small fast model.",
    ))
    registry.register(ModelSpec(
        name="z-ai/glm-5.3-flash",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.FAST}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="GLM 5.3 Flash. Fast fallback.",
    ))

    return registry
