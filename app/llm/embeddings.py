"""Ollama embedding provider.

Wraps ``nomic-embed-text`` (or any Ollama embedding model) behind the
:class:`~app.memory.vectors.base.EmbeddingProvider` interface from
Phase 1. This is the piece that turns semantic memory recall on: the
memory manager checks ``is_available()`` and switches from keyword
search to vector search when this provider responds.

Uses Ollama's **native** embedding endpoint (``/api/embeddings``), not
the OpenAI-compatible one — embeddings are not part of the OpenAI chat
protocol, and Ollama exposes a dedicated, simpler endpoint for them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence

from app.config.logging_setup import get_logger
from app.memory.vectors.base import EmbeddingProvider

logger = get_logger("llm.embeddings")

#: ``nomic-embed-text`` produces 768-dimensional vectors. Kept as a
#: constant so the memory subsystem knows the size before the first call.
NOMIC_EMBED_DIM = 768


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embeddings via a local Ollama daemon."""

    name = "ollama"

    def __init__(
        self,
        *,
        model: str = "nomic-embed-text:latest",
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
        dimension: int = NOMIC_EMBED_DIM,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._dimension = dimension

    # -- introspection -----------------------------------------------------
    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def is_available(self) -> bool:
        """Probe the daemon with a tiny embedding request.

        Cheaper and more reliable than listing models: if the daemon is
        up and the model is pulled, this succeeds; if either is missing,
        it fails. Cached after the first successful probe so
        ``MemoryManager.recall`` does not pay a network round-trip per query.
        """
        if getattr(self, "_verified", False):
            return True
        try:
            vector = self.embed("probe")
        except Exception:  # noqa: BLE001
            return False
        if len(vector) != self._dimension:
            logger.warning(
                "embedding dimension mismatch",
                extra={
                    "event": "EMBED_DIM_MISMATCH",
                    "expected": self._dimension,
                    "actual": len(vector),
                },
            )
            # Trust what the model actually produced.
            self._dimension = len(vector)
        self._verified = True
        return True

    # -- embedding ---------------------------------------------------------
    def embed(self, text: str) -> list[float]:
        payload = {"model": self._model, "prompt": text}
        return self._post(payload).get("embedding", [])

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """One request per text.

        Ollama's ``/api/embeddings`` accepts a single prompt. Batching is
        left to the caller because the memory manager rarely embeds more
        than a handful of texts at once, and serialising keeps error
        handling per-item.
        """
        return [self.embed(text) for text in texts]

    # -- transport ---------------------------------------------------------
    def _post(self, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/api/embeddings",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read())
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"Ollama embeddings unreachable at {self._base_url}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ValueError("Ollama returned malformed JSON for embeddings") from exc