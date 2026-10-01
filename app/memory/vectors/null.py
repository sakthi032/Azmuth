"""No-op vector and embedding providers.

Used by default so the memory subsystem boots and runs without a real
embedding model. The manager checks :meth:`is_available` on both, and simply
uses keyword search when either returns ``False``.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.memory.vectors.base import EmbeddingProvider, VectorStore


class NullEmbeddingProvider(EmbeddingProvider):
    """Always unavailable. Never produces a vector."""

    name = "null"

    def is_available(self) -> bool:
        return False

    @property
    def dimension(self) -> int:
        return 0

    def embed(self, text: str) -> list[float]:  # noqa: ARG002 - contract
        return []

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:  # noqa: ARG002
        return []


class NullVectorStore(VectorStore):
    """Always unavailable. Discards every write, returns no hits."""

    name = "null"

    def is_available(self) -> bool:
        return False

    def add(self, key, vector, metadata=None) -> None:  # noqa: ANN001, ARG002
        return None

    def search(self, vector, *, top_k=5) -> list[tuple[str, float]]:  # noqa: ANN001, ARG002
        return []

    def remove(self, key) -> bool:  # noqa: ANN001, ARG002
        return False

    def clear(self) -> None:
        return None