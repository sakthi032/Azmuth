
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
    """Registry using NVIDIA NIM models verified by availability tests."""

    registry = ModelRegistry()

    # ---- CHAT: general conversation -------------------------------------
    registry.register(ModelSpec(
        name="z-ai/glm-5.3",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CHAT}),
        context_window=128_000,
        is_local=False,
        tier=1,
        description="GLM 5.3. Primary chat model.",
    ))

    registry.register(ModelSpec(
        name="deepseek-ai/deepseek-v4.1-flash",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CHAT}),
        context_window=128_000,
        is_local=False,
        tier=2,
        description="DeepSeek V4.1 Flash. Chat fallback.",
    ))

    registry.register(ModelSpec(
        name="nvidia/nemotron-3.5-lightning-30b-a3b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CHAT}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="Nemotron 3.5 Lightning. Fast chat fallback.",
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
        name="deepseek-ai/deepseek-v4.1-flash",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.REASONING}),
        context_window=128_000,
        is_local=False,
        tier=2,
        description="DeepSeek V4.1 Flash. Reasoning fallback.",
    ))

    registry.register(ModelSpec(
        name="z-ai/glm-5.3",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.REASONING}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="GLM 5.3. Reasoning fallback.",
    ))

    # ---- CODING ---------------------------------------------------------
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
        name="z-ai/glm-5.3",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CODING}),
        context_window=128_000,
        is_local=False,
        tier=2,
        description="GLM 5.3. Coding fallback.",
    ))

    registry.register(ModelSpec(
        name="nvidia/nemotron-3-ultra-550b-a55b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.CODING}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="Nemotron 3 Ultra. Complex coding fallback.",
    ))

    # ---- FAST: short lookups, quick questions ---------------------------
    registry.register(ModelSpec(
        name="nvidia/nemotron-3.5-lightning-30b-a3b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.FAST}),
        context_window=128_000,
        is_local=False,
        tier=1,
        description="Nemotron 3.5 Lightning. Primary fast model.",
    ))

    registry.register(ModelSpec(
        name="deepseek-ai/deepseek-v4.1-flash",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.FAST}),
        context_window=128_000,
        is_local=False,
        tier=2,
        description="DeepSeek V4.1 Flash. Fast fallback.",
    ))

    registry.register(ModelSpec(
        name="z-ai/glm-5.3",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.FAST}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="GLM 5.3. Final cloud fallback.",
    ))

    # ---- INDEPENDENT CLOUD FALLBACK: GROQ -------------------------------
    registry.register(ModelSpec(
        name="llama-3.3-70b-versatile",
        provider="groq",
        purposes=frozenset({
            ModelPurpose.CHAT,
            ModelPurpose.REASONING,
        }),
        context_window=128_000,
        is_local=False,
        tier=4,
        description="Groq Llama 3.3. Independent cloud fallback.",
    ))

    # ---- LOCAL OLLAMA: last-resort fallback -----------------------------
    registry.register(ModelSpec(
        name="qwen2.5:7b",
        provider="ollama",
        purposes=frozenset({
            ModelPurpose.CHAT,
            ModelPurpose.REASONING,
            ModelPurpose.CODING,
            ModelPurpose.FAST,
        }),
        context_window=32_768,
        is_local=True,
        tier=5,
        description="Local Qwen fallback when cloud providers fail.",
    ))

    registry.register(ModelSpec(
        name="llama3.2:3b",
        provider="ollama",
        purposes=frozenset({
            ModelPurpose.CHAT,
            ModelPurpose.FAST,
        }),
        context_window=131_072,
        is_local=True,
        tier=5,
        description="Fast local fallback for chat.",
    ))

    return registry
