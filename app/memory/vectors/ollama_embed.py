"""Ollama-backed embedding provider.

Uses the local Ollama server's batch embedding endpoint. Defaults to
``nomic-embed-text`` (768-dim), which is fast, accurate for retrieval,
and small enough (274 MB) to keep resident without thrashing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import httpx

from app.core.errors import ProviderUnavailableError
from app.memory.vectors.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embed text via a local Ollama instance."""

    name = "ollama"

    def __init__(
        self,
        *,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
        dimension: int = 768,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def is_available(self) -> bool:
        """Cheaply probe: is Ollama reachable? Is the model present?"""
        try:
            with httpx.Client(timeout=2.0) as client:
                r = client.get(f"{self._base_url}/api/tags")
                if r.status_code != 200:
                    return False
                data = r.json()
                names = {m.get("name", "") for m in data.get("models", [])}
                # Ollama reports "nomic-embed-text:latest"; match either
                # the bare name or the tagged form.
                return (
                    self._model in names
                    or f"{self._model}:latest" in names
                )
        except Exception:
            return False

    # -- sync API (required by the ABC) ---------------------------------
    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # Ollama requires non-empty strings; substitute a single space
        # for empties so the batch length is preserved.
        payload_inputs = [t if t.strip() else " " for t in texts]

        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self._base_url}/api/embed",
                    json={"model": self._model, "input": payload_inputs},
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ProviderUnavailableError(
                    f"Ollama model {self._model!r} not found. "
                    f"Pull it with: ollama pull {self._model}"
                ) from exc
            raise ProviderUnavailableError(
                f"Ollama embedding failed: {exc}"
            ) from exc
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            raise ProviderUnavailableError(
                f"Ollama unreachable at {self._base_url}: {exc}"
            ) from exc

        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ProviderUnavailableError(
                f"Ollama returned {len(embeddings or [])} embeddings "
                f"for {len(texts)} inputs"
            )
        return [list(map(float, vec)) for vec in embeddings]


__all__ = ["OllamaEmbeddingProvider"]