"""Multi-model router.

The brain calls :meth:`LLMRouter.for_purpose` with a task type; the
router returns the provider that should handle it. The decision is:

1. Look up models registered for that purpose, ordered by tier and
   local-first.
2. Walk the list; return the first whose provider reports available.
3. If nothing is available, raise :class:`ProviderUnavailableError`.

The router owns **provider instances**, one per provider name. A
provider is constructed lazily when first needed, using the settings for
that provider. That way a user with no NVIDIA API key never pays the
construction cost, and the router's fallback chain automatically skips
unavailable tiers.

The router is deliberately a *selector*, not an *executor*. The brain
still calls ``.chat()`` on the returned provider. This keeps the router
trivial to test and keeps the pipeline's code unaware that routing
exists at all.
"""

from __future__ import annotations

from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.errors import ProviderNotFoundError, ProviderUnavailableError
from app.llm.base import LLMProvider
from app.llm.registry import ModelPurpose, ModelRegistry, ModelSpec
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.nvidia_nim import NvidiaNIMProvider

logger = get_logger("llm.router")

# In app/llm/router.py
from app.llm.providers.groq import GroqProvider
from app.llm.providers.gemini import GeminiProvider

# Add the new classes to the dictionary
_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "nvidia_nim": NvidiaNIMProvider,
    "groq": GroqProvider,
    "gemini": GeminiProvider,
}


class LLMRouter:
    """Select the right provider for a purpose, with fallback."""

    def __init__(
        self,
        settings: Settings,
        registry: ModelRegistry,
        *,
        provider_classes: dict[str, type[LLMProvider]] | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._provider_classes = dict(_PROVIDER_CLASSES)
        if provider_classes:
            self._provider_classes.update(provider_classes)
        self._providers: dict[str, LLMProvider] = {}

    # -- introspection -----------------------------------------------------
    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    def known_purposes(self) -> tuple[ModelPurpose, ...]:
        return tuple(ModelPurpose)

    def describe_routing(self) -> list[dict[str, object]]:
        """Return a human-readable routing table. Used by diagnostics and UI."""
        rows: list[dict[str, object]] = []
        for purpose in ModelPurpose:
            candidates = self._registry.by_purpose(purpose)
            chosen = self._first_available(candidates)
            rows.append({
                "purpose": purpose.value,
                "candidates": [m.name for m in candidates],
                "selected": chosen.name if chosen else None,
                "provider": chosen.provider if chosen else None,
            })
        return rows

    # -- selection ---------------------------------------------------------
    def for_purpose(
        self,
        purpose: ModelPurpose,
        *,
        local_only: bool = False,
    ) -> LLMProvider:
        """Return the provider best suited for ``purpose``.

        Raises :class:`ProviderUnavailableError` when no registered model
        for this purpose has an available provider.
        """
        candidates = self._registry.by_purpose(purpose, local_only=local_only)
        if not candidates:
            raise ProviderUnavailableError(
                f"no model registered for purpose {purpose.value!r}"
            )

        spec = self._first_available(candidates)
        if spec is None:
            names = ", ".join(m.name for m in candidates)
            raise ProviderUnavailableError(
                f"no available provider for purpose {purpose.value!r}; "
                f"tried: {names}"
            )

        provider = self._provider_for(spec)
        logger.info(
            "routed request",
            extra={
                "event": "LLM_ROUTED",
                "purpose": purpose.value,
                "model": spec.name,
                "provider": spec.provider,
                "is_local": spec.is_local,
            },
        )
        return provider

    def for_model(self, model_name: str) -> LLMProvider:
        """Return the provider that serves a specific model, by name."""
        spec = self._registry.by_name(model_name)
        if spec is None:
            raise ProviderNotFoundError(f"model {model_name!r} is not registered")
        provider = self._provider_for(spec)
        if not provider.is_available():
            raise ProviderUnavailableError(
                f"provider {spec.provider!r} for model {model_name!r} is not available"
            )
        return provider

    # -- internals ---------------------------------------------------------
    def _first_available(self, candidates: tuple[ModelSpec, ...]) -> ModelSpec | None:
        for spec in candidates:
            try:
                provider = self._provider_for(spec)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "provider construction failed during routing",
                    extra={
                        "event": "LLM_PROVIDER_BUILD_FAILED",
                        "provider": spec.provider,
                        "reason": str(exc),
                    },
                )
                continue
            if provider.is_available():
                return spec
        return None

    def _provider_for(self, spec: ModelSpec) -> LLMProvider:
        """Return (and cache) the provider instance for ``spec.provider``.

        The provider is constructed with the model name from the spec, so
        each provider instance serves exactly one model. This is simpler
        than a provider that holds many models: the router picks the
        model, and the provider just talks to it.
        """
        cache_key = f"{spec.provider}::{spec.name}"
        if cache_key in self._providers:
            return self._providers[cache_key]

        provider_cls = self._provider_classes.get(spec.provider)
        if provider_cls is None:
            raise ProviderNotFoundError(
                f"unknown provider {spec.provider!r}; "
                f"known: {sorted(self._provider_classes)}"
            )

        provider = self._build_provider(provider_cls, spec)
        self._providers[cache_key] = provider
        return provider

    def _build_provider(
        self, cls: type[LLMProvider], spec: ModelSpec
    ) -> LLMProvider:
        """Construct a provider using settings appropriate to its name."""
        s = self._settings
        if spec.provider == "ollama":
            return cls(
                model=spec.name,
                base_url=s.ollama_base_url,
                api_key="ollama",
                timeout=s.llm_timeout_seconds,
            )
        if spec.provider == "nvidia_nim":
            return cls(
                model=spec.name,
                base_url=s.nvidia_base_url,
                api_key=s.nvidia_api_key,
                timeout=s.llm_timeout_seconds,
            )
        # Unknown provider name handled above, so this is defensive only.
        return cls(model=spec.name)

    def refresh(self) -> None:
        """Forget cached providers and their model lists. Next call re-probes."""
        for provider in self._providers.values():
            refresh = getattr(provider, "refresh_models", None)
            if callable(refresh):
                refresh()
        self._providers.clear()
        
    async def warmup(self) -> dict[str, bool]:
        """Send a minimal request to every registered model.

        NVIDIA NIM's free tier unloads idle models; the first call after
        an unload pays a 30-120s cold start. Warming up at startup moves
        that cost off the user's first question.

        Returns a dict of model name → whether the warmup succeeded. Never
        raises; a failed warmup just means that model stays cold.
        """
        import asyncio

        from app.llm.messages import ChatRequest, Message, Role

        seen: set[str] = set()
        specs = [m for m in self._registry.all() if m.name not in seen and not seen.add(m.name)]

        async def warm_one(spec) -> tuple[str, bool]:
            try:
                provider = self._provider_for(spec)
                request = ChatRequest(
                    messages=(Message(Role.USER, "hi"),),
                    max_tokens=1,
                )
                await provider.chat(request)
                return spec.name, True
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "warmup failed for model",
                    extra={
                        "event": "LLM_WARMUP_FAILED",
                        "model": spec.name,
                        "reason": str(exc),
                    },
                )
                return spec.name, False

        logger.info(
            "warming up %d models", len(specs),
            extra={"event": "LLM_WARMUP_STARTED"},
        )
        results = await asyncio.gather(*(warm_one(s) for s in specs))
        return dict(results)