"""Vector storage and embedding interfaces.

Both are ABCs. Neither is required for Phase 1: :class:`MemoryManager`
falls back to keyword search when either is unavailable. A future LLM
provider or a small local model can supply embeddings without any change to
the memory manager, the backends, or the brain.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.core.interfaces import Provider


class EmbeddingProvider(Provider):
    """Turns text into vectors."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector length. Must be stable for the provider's lifetime."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return the embedding of ``text``."""

    @abstractmethod
    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embeddings for ``texts``, in the same order."""


class VectorStore(Provider):
    """Stores vectors and returns nearest neighbours.

    Implementations must accept IDs that are opaque strings — the memory
    subsystem uses its own record IDs, never a store-assigned key.
    """

    @abstractmethod
    def add(
        self, key: str, vector: Sequence[float], metadata: dict[str, Any] | None = None
    ) -> None:
        """Insert or replace the vector stored under ``key``."""

    @abstractmethod
    def search(
        self, vector: Sequence[float], *, top_k: int = 5
    ) -> list[tuple[str, float]]:
        """Return ``(key, score)`` pairs, best first. Score is similarity.

        Implementations should return *at most* ``top_k`` entries. An empty
        store returns an empty list.
        """

    @abstractmethod
    def remove(self, key: str) -> bool:
        """Return ``True`` if a vector was removed."""

    @abstractmethod
    def clear(self) -> None:
        """Remove every vector."""