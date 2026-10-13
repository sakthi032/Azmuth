
"""Multi-model router."""

from __future__ import annotations

import time

from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.errors import ProviderNotFoundError, ProviderUnavailableError
from app.llm.base import LLMProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.groq import GroqProvider
from app.llm.providers.nvidia_nim import NvidiaNIMProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.registry import ModelPurpose, ModelRegistry, ModelSpec

logger = get_logger("llm.router")

_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "nvidia_nim": NvidiaNIMProvider,
    "groq": GroqProvider,
    "gemini": GeminiProvider,
}


class LLMRouter:
    """Select the right provider for a purpose, with fallback."""

    #: How long to skip a provider after it fails. An expired token or a
    #: rate limit should not cost every turn the same wasted round-trip.
    FAILURE_COOLDOWN_SECONDS: float = 120.0

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
        self._dead_until: dict[str, float] = {}

    # -- introspection -----------------------------------------------------
    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    def known_purposes(self) -> tuple[ModelPurpose, ...]:
        return tuple(ModelPurpose)

    def describe_routing(self) -> list[dict[str, object]]:
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
        self, purpose: ModelPurpose, *, local_only: bool = False
    ) -> LLMProvider:
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
        spec = self._registry.by_name(model_name)
        if spec is None:
            raise ProviderNotFoundError(f"model {model_name!r} is not registered")
        provider = self._provider_for(spec)
        if not provider.is_available():
            raise ProviderUnavailableError(
                f"provider {spec.provider!r} for model {model_name!r} is not available"
            )
        return provider

    # -- failure tracking --------------------------------------------------
    def record_failure(
        self, spec: ModelSpec, *, cooldown_seconds: float | None = None
    ) -> None:
        """Mark a model as recently failed so the router skips it briefly.

        Called by the pipeline when a provider raises. The cooldown means
        an expired token or a rate-limited provider does not cost every
        turn the same wasted call.
        """
        key = self._cache_key(spec)
        cooldown = (
            cooldown_seconds
            if cooldown_seconds is not None
            else self.FAILURE_COOLDOWN_SECONDS
        )
        self._dead_until[key] = time.monotonic() + cooldown
        logger.info(
            "provider marked unhealthy",
            extra={
                "event": "LLM_PROVIDER_UNHEALTHY",
                "provider": spec.provider,
                "model": spec.name,
                "cooldown_seconds": cooldown,
            },
        )

    def record_success(self, spec: ModelSpec) -> None:
        """Clear any failure record for a model that just answered."""
        self._dead_until.pop(self._cache_key(spec), None)

    def is_healthy(self, spec: ModelSpec) -> bool:
        return self._dead_until.get(self._cache_key(spec), 0.0) <= time.monotonic()

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _cache_key(spec: ModelSpec) -> str:
        return f"{spec.provider}::{spec.name}"

    def _first_available(
        self, candidates: tuple[ModelSpec, ...]
    ) -> ModelSpec | None:
        for spec in candidates:
            if not self.is_healthy(spec):
                continue
            try:
                provider = self._provider_for(spec)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "provider construction failed during routing",
                    extra={
                        "event": "LLM_PROVIDER_BUILD_FAILED",
                        "provider": spec.provider,
                        "model": spec.name,
                        "reason": str(exc),
                    },
                )
                continue
            if provider.is_available():
                return spec
        return None

    def _provider_for(self, spec: ModelSpec) -> LLMProvider:
        cache_key = self._cache_key(spec)
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
        """Construct a provider with the credentials its name implies."""
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

        if spec.provider == "groq":
            return cls(
                model=spec.name,
                base_url=s.groq_base_url,
                api_key=s.groq_api_key,
                timeout=s.llm_timeout_seconds,
            )

        if spec.provider == "gemini":
            return cls(
                model=spec.name,
                base_url=s.gemini_base_url,
                api_key=s.gemini_api_key,
                timeout=s.llm_timeout_seconds,
            )

        # Unknown provider name — the class exists but nothing knows how
        # to configure it. Build with model only; the provider's own
        # is_available() decides whether that is enough.
        logger.warning(
            "router has no credentials branch for provider",
            extra={"event": "LLM_PROVIDER_UNCONFIGURED", "provider": spec.provider},
        )
        return cls(model=spec.name)

    def refresh(self) -> None:
        for provider in self._providers.values():
            refresh = getattr(provider, "refresh_models", None)
            if callable(refresh):
                refresh()
        self._providers.clear()
        self._dead_until.clear()

    # -- warmup ------------------------------------------------------------
    async def warmup(self) -> dict[str, bool]:
        import asyncio

        from app.llm.messages import ChatRequest, Message, Role

        seen: set[str] = set()
        specs = [
            m for m in self._registry.all()
            if m.name not in seen and not seen.add(m.name)
        ]

        async def warm_one(spec: ModelSpec) -> tuple[str, bool]:
            try:
                provider = self._provider_for(spec)
                request = ChatRequest(
                    messages=(Message(Role.USER, "hi"),),
                    max_tokens=1,
                )
                await provider.chat(request)
                self.record_success(spec)
                return spec.name, True
            except Exception as exc:  # noqa: BLE001
                self.record_failure(spec)
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
            "warming up %d models",
            len(specs),
            extra={"event": "LLM_WARMUP_STARTED"},
        )
        results = await asyncio.gather(*(warm_one(s) for s in specs))
        return dict(results)


__all__ = ["LLMRouter"]
