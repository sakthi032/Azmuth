"""Ollama provider — local models via the OpenAI-compatible endpoint.

Ollama exposes ``http://localhost:11434/v1`` and speaks the OpenAI
chat-completions protocol. The API key is required by the OpenAI client
library but ignored by Ollama.

This provider extends :class:`OpenAICompatibleProvider` so the request
and response shaping is identical to the generic OpenAI provider. What it
adds is a health probe specific to Ollama: it asks the daemon which
models are actually pulled, so ``is_available()`` reflects reality rather
than just "the daemon is up".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.config.logging_setup import get_logger
from app.llm.providers.openai_compatible import OpenAICompatibleProvider

logger = get_logger("llm.ollama")


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama's OpenAI-compatible endpoint with model awareness."""

    name = "ollama"
    _last_failed_at: float = 0.0
    _FAILURE_CACHE_SECONDS: float = 30.0

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = "ollama",
        timeout: float = 120.0,
        extra: dict | None = None,
    ) -> None:
        super().__init__(
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            extra=extra,
        )
        self._installed_models: set[str] | None = None

    # -- availability ------------------------------------------------------
    def is_available(self) -> bool:
        """Check whether Ollama is configured and the requested model is pulled."""
        if not (self._base_url and self._model):
            return False
        installed = self._list_installed_models()
        if installed is None:
            return False
        target = self._model
        return (
            target in installed
            or f"{target}:latest" in installed
            or any(m.split(":")[0] == target for m in installed)
        )

    def _list_installed_models(self) -> set[str] | None:
        """Ask the Ollama daemon which models are pulled.

        Uses the native ``/api/tags`` endpoint, not the OpenAI-compatible
        one — model listing is not part of the OpenAI protocol.
        Returns ``None`` on any failure; the caller treats that as
        "unavailable".
        """
        import time
        if self._installed_models is not None:
            return self._installed_models
        if time.monotonic() - OllamaProvider._last_failed_at < OllamaProvider._FAILURE_CACHE_SECONDS:
            return None

        assert self._base_url is not None
        # Strip the trailing /v1 to reach the native API root.
        root = self._base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        url = f"{root}/api/tags"

        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            OllamaProvider._last_failed_at = time.monotonic()
            logger.debug(
                "ollama daemon not reachable",
                extra={"event": "OLLAMA_UNREACHABLE", "url": url, "reason": str(exc)},
            )
            return None

        models = {
            entry.get("name", "")
            for entry in payload.get("models", [])
            if isinstance(entry, dict)
        }
        self._installed_models = models
        return models

    def refresh_models(self) -> None:
        """Forget the cached model list. Next ``is_available`` re-queries."""
        self._installed_models = None
        OllamaProvider._last_failed_at = 0.0