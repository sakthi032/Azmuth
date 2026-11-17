"""ChromaDB-backed vector store.

Persists to a directory under ``data/memory/vectors/``. Uses cosine
similarity (Chroma returns a distance; we convert to a similarity score
in [0, 1] before returning).

The store is thread-safe: Chroma's PersistentClient handles concurrent
access internally.
"""

from __future__ import annotations

import os as _os
_os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
_os.environ.setdefault("CHROMA_TELEMETRY_ENABLED", "False")
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.core.errors import ProviderUnavailableError
from app.memory.vectors.base import VectorStore

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "azmuth_memories"


class ChromaVectorStore(VectorStore):
    """Persistent vector index using ChromaDB."""

    name = "chroma"

    def __init__(self, path: str | Path) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise ProviderUnavailableError(
                "chromadb is not installed. "
                "Install it with: pip install chromadb"
            ) from exc

        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)

        try:
            self._client = chromadb.PersistentClient(path=str(self._path))
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                f"could not initialize Chroma at {self._path}: {exc}"
            ) from exc

    def is_available(self) -> bool:
        return self._collection is not None

    # -- write ----------------------------------------------------------
    def add(
        self,
        key: str,
        vector: Sequence[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Chroma metadata values must be str/int/float/bool — no None.
        clean_meta: dict[str, Any] = {}
        for k, v in (metadata or {}).items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                clean_meta[k] = v
            else:
                clean_meta[k] = str(v)

        try:
            self._collection.upsert(
                ids=[key],
                embeddings=[list(map(float, vector))],
                metadatas=[clean_meta] if clean_meta else None,
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                f"Chroma upsert failed for {key!r}: {exc}"
            ) from exc

    def remove(self, key: str) -> bool:
        try:
            self._collection.delete(ids=[key])
            return True
        except Exception:
            return False

    def clear(self) -> None:
        try:
            # Delete the collection and recreate it — faster than
            # deleting every ID when the count is large.
            self._client.delete_collection(_COLLECTION_NAME)
        except Exception:
            pass
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # -- read -----------------------------------------------------------
    def search(
        self, vector: Sequence[float], *, top_k: int = 5
    ) -> list[tuple[str, float]]:
        try:
            result = self._collection.query(
                query_embeddings=[list(map(float, vector))],
                n_results=top_k,
                include=["distances"],
            )
        except Exception as exc:
            logger.warning(
                "chroma query failed: %s",
                exc,
                extra={"event": "VECTOR_SEARCH_FAILED"},
            )
            return []

        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[tuple[str, float]] = []
        for key, dist in zip(ids, distances):
            # Chroma cosine distance is in [0, 2]; similarity = 1 - distance.
            # Clamp to [0, 1] for consistency with other stores.
            similarity = max(0.0, min(1.0, 1.0 - float(dist)))
            hits.append((str(key), similarity))
        return hits

    # -- introspection --------------------------------------------------
    def count(self) -> int:
        try:
            return int(self._collection.count())
        except Exception:
            return 0

    def close(self) -> None:
        """Persist ChromaDB data to disk without clearing it.

        ChromaDB PersistentClient auto-persists on write, so this is a
        no-op in practice. Explicitly provided so MemoryManager.close()
        can call it safely without risk of data loss.
        """
        # No explicit flush API needed — PersistentClient auto-persists.
        pass


__all__ = ["ChromaVectorStore"]