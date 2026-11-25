
"""Model registry - what models exist and what they are for.

Cloud models (Groq, Gemini, NVIDIA) are the primary path because they
are fast and free at this tier. Local Ollama models serve two roles:
embeddings (must be local - per-statement and frequently called) and
offline fallback for chat/coding.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


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
    purpose_tiers: dict[ModelPurpose, int] = field(default_factory=dict)

    def supports(self, purpose: ModelPurpose) -> bool:
        return purpose in self.purposes

    def tier_for(self, purpose: ModelPurpose) -> int:
        if self.purpose_tiers and purpose in self.purpose_tiers:
            return self.purpose_tiers[purpose]
        return self.tier

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "provider": self.provider,
            "purposes": sorted(p.value for p in self.purposes),
            "context_window": self.context_window,
            "is_local": self.is_local,
            "tier": self.tier,
            "description": self.description,
            "purpose_tiers": (
                {p.value: t for p, t in self.purpose_tiers.items()}
                if self.purpose_tiers
                else {}
            ),
        }


class ModelRegistry:
    def __init__(self) -> None:
        self._models: list[ModelSpec] = []

    def register(self, spec: ModelSpec) -> None:
        """Register a model specification, deduplicating by (provider, name)."""
        for i, existing in enumerate(self._models):
            if existing.provider == spec.provider and existing.name == spec.name:
                merged_purposes = existing.purposes | spec.purposes
                merged_tiers: dict[ModelPurpose, int] = dict(existing.purpose_tiers)
                for p in existing.purposes:
                    merged_tiers.setdefault(p, existing.tier)
                for p in spec.purposes:
                    merged_tiers[p] = spec.tier_for(p)

                min_tier = min(merged_tiers.values()) if merged_tiers else existing.tier

                merged_spec = ModelSpec(
                    name=existing.name,
                    provider=existing.provider,
                    purposes=frozenset(merged_purposes),
                    context_window=existing.context_window or spec.context_window,
                    is_local=existing.is_local and spec.is_local,
                    tier=min_tier,
                    description=existing.description or spec.description,
                    purpose_tiers=merged_tiers,
                )
                self._models[i] = merged_spec
                return

        if not spec.purpose_tiers and spec.purposes:
            tiers = {p: spec.tier for p in spec.purposes}
            spec = replace(spec, purpose_tiers=tiers)
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
            replace(m, tier=m.tier_for(purpose))
            for m in self._models
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




def _has_gemini_key(settings: Any = None) -> bool:
    if settings is not None:
        return bool(getattr(settings, "gemini_api_key", None))
    import os
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return True
    try:
        from pathlib import Path
        from dotenv import dotenv_values
        project_root = Path(__file__).resolve().parents[2]
        env_file = project_root / ".env"
        if env_file.is_file():
            vals = dotenv_values(env_file)
            if vals.get("GEMINI_API_KEY") or vals.get("GOOGLE_API_KEY"):
                return True
    except Exception:
        pass
    return False


def build_default_registry(settings: Any = None) -> ModelRegistry:
    """Registry using NVIDIA NIM, Gemini, Groq, and Ollama models."""

    registry = ModelRegistry()

    # ---- GEMINI: cloud chat & primary vision (when key is present) --------
    if _has_gemini_key(settings):
        registry.register(ModelSpec(
            name="gemini-flash-lite-latest",
            provider="gemini",
            purposes=frozenset({
                ModelPurpose.CHAT,
                ModelPurpose.VISION,
                ModelPurpose.FAST,
            }),
            context_window=1_048_576,
            is_local=False,
            tier=1,
            description="Gemini Flash Lite. Fast chat and primary vision model.",
            purpose_tiers={
                ModelPurpose.VISION: 1,
                ModelPurpose.CHAT: 2,
                ModelPurpose.FAST: 2,
            },
        ))
        registry.register(ModelSpec(
            name="gemini-flash-latest",
            provider="gemini",
            purposes=frozenset({
                ModelPurpose.CHAT,
                ModelPurpose.VISION,
            }),
            context_window=1_048_576,
            is_local=False,
            tier=2,
            description="Gemini Flash. High-capability vision and chat model.",
            purpose_tiers={
                ModelPurpose.VISION: 2,
                ModelPurpose.CHAT: 3,
            },
        ))

    # ---- VISION: candidate chain ----------------------------------------
    # 1. Gemini (registered above with tier=1 / tier=2 when key is present)
    # 2. NVIDIA NIM vision-capable model (tier=3)
    registry.register(ModelSpec(
        name="nvidia/nemotron-3.5-super-vl-120b-a12b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.VISION}),
        context_window=128_000,
        is_local=False,
        tier=3,
        description="Nemotron 3.5 Super VL. NVIDIA NIM vision model.",
    ))
    # 3. Ollama vision fallback (tier=4)
    registry.register(ModelSpec(
        name="llava",
        provider="ollama",
        purposes=frozenset({ModelPurpose.VISION}),
        context_window=32_768,
        is_local=True,
        tier=4,
        description="Local LLaVA vision fallback.",
    ))

    # ---- EMBEDDING: local vector embeddings -----------------------------
    registry.register(ModelSpec(
        name="nomic-embed-text",
        provider="ollama",
        purposes=frozenset({ModelPurpose.EMBEDDING}),
        context_window=8192,
        is_local=True,
        tier=1,
        description="Local Ollama embeddings for memory retrieval.",
    ))

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
