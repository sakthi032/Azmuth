"""Development-only LLM provider.

The stub echoes the last user message back, prefixed with its own marker. It
exists so the rest of the system can be built, tested and demonstrated
without any external model, network, or API key.

This provider is **not** an LLM and must never be used in production. It is
registered as ``"stub"`` and is the default only because ``LLM_PROVIDER`` is
unset in a fresh development environment.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator

from app.llm.base import LLMProvider
from app.llm.messages import (
    ChatRequest,
    ChatResponse,
    Message,
    ModelInfo,
    Role,
    StreamChunk,
    Usage,
)

_WORD_RE = re.compile(r"\S+\s*")

DEFAULT_STUB_MODEL = "stub-echo-v1"


class StubLLMProvider(LLMProvider):
    """Deterministic echo provider for development and tests."""

    name = "stub"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if not self._model:
            self._model = DEFAULT_STUB_MODEL

    # -- availability ------------------------------------------------------
    def is_available(self) -> bool:
        return True

    # -- core operations ---------------------------------------------------
    async def chat(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        reply = self._reply_for(request)
        return ChatResponse(
            content=reply,
            model=self.model,
            provider=self.name,
            finish_reason="stop",
            usage=Usage(
                prompt_tokens=self._approx_tokens(request),
                completion_tokens=max(1, len(reply.split())),
                total_tokens=self._approx_tokens(request) + max(1, len(reply.split())),
            ),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        reply = self._reply_for(request)
        tokens = _WORD_RE.findall(reply) or ([reply] if reply else [])
        for index, token in enumerate(tokens):
            yield StreamChunk(delta=token, index=index)
        yield StreamChunk(delta="", index=len(tokens), finish_reason="stop")

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self.model,
            context_window=4096,
            supports_streaming=True,
            extra={"kind": "development-stub"},
        )

    # -- internals ---------------------------------------------------------
    def _reply_for(self, request: ChatRequest) -> str:
        last_user = ""
        for message in reversed(request.messages):
            if message.role is Role.USER:
                last_user = message.content
                break
        return f"[stub:{self.model}] {last_user}"

    @staticmethod
    def _approx_tokens(request: ChatRequest) -> int:
        return sum(len(m.content.split()) for m in request.messages) or 1