
"""LLM manager - selects a provider per request.

Phase 1 gave the manager one provider. Phase 2 gives it a router: the
manager asks the router which provider should handle a given purpose and
forwards the request there. If no router is configured (the default in
tests), the manager falls back to the Phase 1 behaviour of one provider
from the registry.

The rest of Azmuth sees the same interface it always did: ``.chat()``,
``.stream()``, ``.generate()``. Only how the provider is chosen has
changed.
"""


from __future__ import annotations

from collections.abc import AsyncIterator
from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.errors import (
    LLMError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)
from app.core.registry import Registry
from app.llm.base import LLMProvider
from app.llm.messages import (
    ChatRequest,
    ChatResponse,
    ModelInfo,
    StreamChunk,
)
from app.llm.registry import ModelPurpose
from app.llm.router import LLMRouter

logger = get_logger("llm")


class LLMManager:
    """Resolve a provider (or route to one) and forward calls."""

    def __init__(
        self,
        settings: Settings,
        registry: Registry[LLMProvider],
        *,
        router: LLMRouter | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._router = router
        self._provider: LLMProvider | None = None
        self._default_purpose = ModelPurpose(
            settings.llm_default_purpose if hasattr(settings, "llm_default_purpose") else "chat"
        )

    # -- access ------------------------------------------------------------
    @property
    def provider_name(self) -> str:
        if self._router is not None:
            return "router"
        return self._settings.llm_provider

    @property
    def router(self) -> LLMRouter | None:
        return self._router

    @property
    def provider(self) -> LLMProvider:
        """The default provider.

        With a router, this resolves the default purpose. Without one, it
        builds the single configured provider (Phase 1 behaviour).
        """
        if self._router is not None:
            return self._router.for_purpose(self._default_purpose)

        if self._provider is None:
            self._provider = self._build_provider()
            logger.info(
                "LLM provider resolved",
                extra={
                    "event": "LLM_PROVIDER_READY",
                    "provider": self._provider.name,
                    "model": self._provider.model or "(unset)",
                },
            )
        return self._provider

    def _build_provider(self) -> LLMProvider:
        """Build a fresh provider from settings.

        Kwargs are passed explicitly so the registry returns a new
        instance instead of the cached singleton. That is what makes
        ``reset()`` observable: after reset, the next access builds a
        new provider configured with the current settings.
        """
        return self._registry.create(
            self._settings.llm_provider,
            model=self._settings.llm_model or None,
            base_url=self._settings.llm_base_url,
            api_key=self._settings.llm_api_key,
            timeout=self._settings.llm_timeout_seconds,
        )

    def is_available(self) -> bool:
        try:
            return self.provider.is_available()
        except ProviderNotFoundError:
            return False
        except Exception:  # noqa: BLE001
            logger.exception(
                "availability check failed",
                extra={"event": "LLM_AVAILABILITY_FAILED"},
            )
            return False

    def reset(self) -> None:
        self._provider = None
        if self._router is not None:
            self._router.refresh()

    # -- routing -----------------------------------------------------------
    def provider_for(self, purpose: ModelPurpose) -> LLMProvider:
        """Return the provider that should handle ``purpose``."""
        if self._router is not None:
            return self._router.for_purpose(purpose)
        # Without a router, one provider serves everything.
        return self.provider

    def routing_table(self) -> list[dict[str, object]]:
        """Describe routing decisions. Empty when no router is configured."""
        if self._router is None:
            return []
        return self._router.describe_routing()

    # -- forwarding --------------------------------------------------------
    async def chat(
        self, request: ChatRequest, *, purpose: ModelPurpose | None = None
    ) -> ChatResponse:
        provider = self.provider_for(purpose or self._default_purpose)
        if not provider.is_available():
            raise ProviderUnavailableError(
                f"LLM provider {provider.name!r} is not available"
            )
        try:
            return await provider.chat(request)
        except (ProviderUnavailableError, ProviderNotFoundError):
            # These are already the right abstraction - re-raise as-is so
            # callers can fall back or report them cleanly.
            raise
        except Exception as exc:  # noqa: BLE001
            # Wrap provider-specific or unexpected failures so callers
            # only ever see LLMError from this layer. The pipeline's
            # fallback logic catches this and tries the next candidate.
            logger.exception(
                "chat failed",
                extra={"event": "LLM_CHAT_FAILED", "provider": provider.name},
            )
            raise LLMError(f"LLM chat failed: {exc}") from exc

    def stream(
        self, request: ChatRequest, *, purpose: ModelPurpose | None = None
    ) -> AsyncIterator[StreamChunk]:
        provider = self.provider_for(purpose or self._default_purpose)
        if not provider.is_available():
            raise ProviderUnavailableError(
                f"LLM provider {provider.name!r} is not available"
            )
        return provider.stream(request)

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stop: tuple[str, ...] = (),
        purpose: ModelPurpose | None = None,
    ) -> ChatResponse:
        provider = self.provider_for(purpose or self._default_purpose)
        return await provider.generate(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
        )

    def get_model_info(self) -> ModelInfo:
        return self.provider.get_model_info()
