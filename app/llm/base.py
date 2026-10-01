"""The provider interface every LLM implementation must satisfy."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator, Mapping
from typing import Any

from app.core.interfaces import Provider
from app.llm.messages import ChatRequest, ChatResponse, ModelInfo, Message, Role, StreamChunk


class LLMProvider(Provider):
    """Common base for every LLM backend.

    Configuration values reach providers uniformly: the manager always passes
    ``model``, ``base_url``, ``api_key``, ``timeout`` and ``extra``. Providers
    pick what they need and ignore the rest — that way adding a new config
    knob never breaks existing providers.

    Concrete providers must implement :meth:`chat`, :meth:`stream` and
    :meth:`get_model_info`. :meth:`generate` is a convenience wrapper around
    :meth:`chat` and does not need overriding.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self._model = model or ""
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._extra: dict[str, Any] = dict(extra or {})

    # -- introspection -----------------------------------------------------
    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str | None:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    # -- abstract surface --------------------------------------------------
    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Run one non-streaming completion."""

    @abstractmethod
    def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        """Run one streaming completion.

        Declared as a plain method (not ``async def``) so implementations can
        return an async iterator directly. Callers must always do::

            async for chunk in provider.stream(request):
                ...
        """

    @abstractmethod
    def get_model_info(self) -> ModelInfo:
        """Return what this provider knows about the active model.

        Must not raise. Providers that cannot determine a field return the
        dataclass default.
        """

    # -- convenience -------------------------------------------------------
    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stop: tuple[str, ...] = (),
    ) -> ChatResponse:
        """Single-shot convenience: ``prompt`` in, response out."""
        messages: list[Message] = []
        if system:
            messages.append(Message(Role.SYSTEM, system))
        messages.append(Message(Role.USER, prompt))
        return await self.chat(
            ChatRequest(
                messages=tuple(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
            )
        )