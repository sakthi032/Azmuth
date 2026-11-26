"""Memory subsystem façade.

The brain talks only to :class:`MemoryManager`. Its responsibilities:

* turn a plain string into a :class:`MemoryRecord` (with defaults),
* delegate persistence to a :class:`MemoryBackend`,
* maintain the vector index when both an embedding provider and a vector
  store are configured,
* answer two query shapes — ``search()`` (keyword) and ``recall()``
  (relevant, vector-first with a keyword fallback),
* keep access statistics honest without exposing them to callers.

Everything else (SQL, HTTP, similarity metrics) is a backend / provider
concern and never appears here.
"""

from __future__ import annotations

from typing import Any

from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.errors import MemoryStoreError, ProviderUnavailableError
from app.memory.backends.base import MemoryBackend
from app.memory.backends.sqlite_backend import SqliteMemoryBackend
from app.memory.models import MemoryKind, MemoryRecord, new_memory_id
from app.memory.vectors.base import EmbeddingProvider, VectorStore
from app.memory.vectors.null import NullEmbeddingProvider, NullVectorStore

logger = get_logger("memory")

_SINGLE_VALUED_FACT_KEYS: frozenset[str] = frozenset({
    "name",
    "city",
    "location",
    "hometown",
    "country",
    "employer",
    "works at",
    "role",
    "email",
    "phone",
    "age",
    "birthday",
    "timezone",
})


def single_valued_fact_key(content: str) -> str | None:
    """Return the normalized single-valued key if ``content`` is ``Key: Value``."""
    if ":" not in content:
        return None
    key = content.split(":", 1)[0].strip().lower()
    return key if key in _SINGLE_VALUED_FACT_KEYS else None


class MemoryManager:
    """High-level memory operations for the rest of Azmuth."""

    def __init__(
        self,
        settings: Settings,
        backend: MemoryBackend,
        embeddings: EmbeddingProvider | None = None,
        vectors: VectorStore | None = None,
    ) -> None:
        self._settings = settings
        self._backend = backend
        self._embeddings: EmbeddingProvider = embeddings or NullEmbeddingProvider()
        self._vectors: VectorStore = vectors or NullVectorStore()

    # -- introspection -----------------------------------------------------
    @property
    def backend(self) -> MemoryBackend:
        return self._backend

    @property
    def embeddings(self) -> EmbeddingProvider:
        return self._embeddings

    @property
    def vectors(self) -> VectorStore:
        return self._vectors

    def supports_semantic_recall(self) -> bool:
        return self._embeddings.is_available() and self._vectors.is_available()

    # -- write -------------------------------------------------------------
    def store(
        self,
        content: str,
        *,
        user_id: str | None = None,
        kind: MemoryKind = MemoryKind.NOTE,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        fact_key = single_valued_fact_key(content)
        if fact_key is not None and user_id is not None:
            for existing in self._backend.list(user_id=user_id, limit=200):
                if single_valued_fact_key(existing.content) == fact_key:
                    self.delete(existing.id)

        record = MemoryRecord(
            id=memory_id or new_memory_id(),
            user_id=user_id,
            kind=kind,
            content=content,
            metadata=dict(metadata or {}),
            importance=importance,
        )
        self._backend.store(record)
        self._index(record)
        logger.info(
            "memory stored",
            extra={
                "event": "MEMORY_STORED",
                "memory_id": record.id,
                "kind": record.kind.value,
                "user_id": record.user_id,
            },
        )
        return record

    def update(self, memory_id: str, **fields: Any) -> MemoryRecord:
        updated = self._backend.update(memory_id, **fields)
        # Reindex: the content may have changed.
        if "content" in fields and self.supports_semantic_recall():
            self._reindex(updated)
        logger.info(
            "memory updated",
            extra={"event": "MEMORY_UPDATED", "memory_id": updated.id},
        )
        return updated

    def delete(self, memory_id: str) -> bool:
        removed = self._backend.delete(memory_id)
        if removed and self._vectors.is_available():
            self._vectors.remove(memory_id)
        if removed:
            logger.info(
                "memory deleted",
                extra={"event": "MEMORY_DELETED", "memory_id": memory_id},
            )
        return removed

    def clear(self) -> None:
        self._backend.clear()
        if self._vectors.is_available():
            self._vectors.clear()

    # -- read --------------------------------------------------------------
    def get(self, memory_id: str) -> MemoryRecord | None:
        record = self._backend.get(memory_id)
        if record is None:
            return None
        self._touch(memory_id)
        # Reflect the touch in the returned object without a second read.
        record.touch_access()
        return record

    def search(
        self,
        query: str,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        limit: int = 20,
        min_importance: float = 0.0,
    ) -> list[MemoryRecord]:
        """Keyword search. Always available."""
        return self._backend.search(
            query,
            user_id=user_id,
            kind=kind,
            limit=limit,
            min_importance=min_importance,
        )

    def recall(
        self,
        query: str,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        top_k: int = 5,
    ) -> list[MemoryRecord]:
        """Return the most *relevant* memories for ``query``.

        Prefers vector similarity when an embedding provider and vector
        store are configured. Falls back to keyword search otherwise, and
        also falls back if the vector path yields nothing or raises.
        """
        if not self.supports_semantic_recall():
            hits = self.search(
                query, user_id=user_id, kind=kind, limit=top_k,
            )
            if hits or not user_id:
                return hits
            # Generic personal queries ("what do you know about me") do not
            # contain the stored text as a substring. Fall back to the
            # user's most recent memories so store-then-recall works for
            # them. ``not user_id`` above preserves isolation for anonymous
            # callers — they get nothing rather than every user's records.
            return self._backend.list(
                user_id=user_id, kind=kind, limit=top_k,
            )

        try:
            vector = self._embeddings.embed(query)
        except Exception:
            logger.exception(
                "embedding failed; falling back to keyword search",
                extra={"event": "MEMORY_EMBED_FAILED"},
            )
            return self.search(query, user_id=user_id, kind=kind, limit=top_k)

        try:
            hits = self._vectors.search(vector, top_k=top_k * 3)
        except Exception:
            logger.exception(
                "vector search failed; falling back to keyword search",
                extra={"event": "MEMORY_VECTOR_SEARCH_FAILED"},
            )
            return self.search(query, user_id=user_id, kind=kind, limit=top_k)

        records: list[MemoryRecord] = []
        for memory_id, _score in hits:
            record = self._backend.get(memory_id)
            if record is None:
                # Stale entry in the vector index — drop it.
                try:
                    self._vectors.remove(memory_id)
                except Exception:  # noqa: BLE001 - cleanup is best-effort
                    pass
                continue
            if user_id is not None and record.user_id != user_id:
                continue
            if kind is not None and record.kind != kind:
                continue
            record.touch_access()
            records.append(record)
            if len(records) >= top_k:
                break

        if not records:
            return self.search(query, user_id=user_id, kind=kind, limit=top_k)

        # Persist access stats in one pass.
        for record in records:
            self._touch(record.id)
        return records

    def recall_hybrid(
        self,
        query: str,
        *,
        user_id: str | None = None,
        top_k: int = 5,
    ) -> list[MemoryRecord]:
        """Hybrid retrieval combining BM25 keyword matching and vector search with RRF."""
        try:
            from app.memory.retrieval.bm25_retriever import BM25Retriever
            user_records = self._backend.list(user_id=user_id or "creator", limit=100)
            bm25 = BM25Retriever(user_records)
            bm25_hits = bm25.retrieve(query, top_k=top_k * 2)
        except Exception:
            bm25_hits = []

        vector_hits = self.recall(query, user_id=user_id, top_k=top_k * 2)

        scores: dict[str, float] = {}
        records_by_id: dict[str, MemoryRecord] = {}

        for rank, rec in enumerate(bm25_hits):
            scores[rec.id] = scores.get(rec.id, 0.0) + 1.0 / (60 + rank + 1)
            records_by_id[rec.id] = rec

        for rank, rec in enumerate(vector_hits):
            scores[rec.id] = scores.get(rec.id, 0.0) + 1.0 / (60 + rank + 1)
            records_by_id[rec.id] = rec

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [records_by_id[rid] for rid, _ in ranked[:top_k]]

    def list_for_user(
        self,
        user_id: str,
        *,
        kind: MemoryKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        return self._backend.list(
            user_id=user_id, kind=kind, limit=limit, offset=offset
        )

    def deduplicate_user_facts(self, user_id: str) -> int:
        """Remove older duplicate records for single-valued fact keys.

        Keeps the newest record (by ``updated_at DESC``) per single-valued
        key (e.g. ``Name``, ``City``) and deletes older conflicting ones.
        """
        records = self._backend.list(user_id=user_id, limit=200)
        seen_keys: set[str] = set()
        deleted = 0
        for r in records:
            key = single_valued_fact_key(r.content)
            if key is None:
                continue
            if key in seen_keys:
                if self.delete(r.id):
                    deleted += 1
            else:
                seen_keys.add(key)
        return deleted

    def count(
        self,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
    ) -> int:
        return self._backend.count(user_id=user_id, kind=kind)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        try:
            self._backend.close()
        finally:
            if self._vectors.is_available():
                try:
                    # close() persists data without deleting it.
                    # clear() would destroy all vectors — do NOT call that here.
                    self._vectors.close()
                except Exception:  # noqa: BLE001
                    pass

    # -- internals ---------------------------------------------------------
    def _touch(self, memory_id: str) -> None:
        """Best-effort access-stat update. Never lets a stats failure surface."""
        touch = getattr(self._backend, "touch", None)
        try:
            if callable(touch):
                touch(memory_id)
            else:
                # Generic fallback for backends without `touch`.
                record = self._backend.get(memory_id)
                if record is not None:
                    record.touch_access()
                    self._backend.update(
                        memory_id,
                        last_accessed_at=record.last_accessed_at,
                        access_count=record.access_count,
                    )
        except MemoryStoreError:
            logger.exception(
                "failed to update access stats",
                extra={"event": "MEMORY_TOUCH_FAILED", "memory_id": memory_id},
            )

    def _index(self, record: MemoryRecord) -> None:
        if not self.supports_semantic_recall():
            return
        try:
            vector = self._embeddings.embed(record.content)
            self._vectors.add(
                record.id,
                vector,
                {"kind": record.kind.value, "user_id": record.user_id},
            )
        except Exception:
            # Indexing is best-effort; the record is already persisted.
            logger.exception(
                "vector indexing failed",
                extra={"event": "MEMORY_INDEX_FAILED", "memory_id": record.id},
            )

    def _reindex(self, record: MemoryRecord) -> None:
        try:
            self._vectors.remove(record.id)
        except Exception:  # noqa: BLE001 - removal is best-effort
            pass
        self._index(record)

def _build_embeddings(settings: Settings) -> EmbeddingProvider | None:
    """Construct an embedding provider from settings, or None on failure."""
    name = (settings.embedding_provider or "null").lower()
    if name in ("null", "none", ""):
        return None
    if name == "ollama":
        try:
            from app.memory.vectors.ollama_embed import OllamaEmbeddingProvider
            provider = OllamaEmbeddingProvider(
                model=settings.embedding_model,
                base_url=settings.ollama_base_url.replace("/v1", ""),
                dimension=settings.embedding_dimension,
            )
            if not provider.is_available():
                logger.warning(
                    "ollama embedding model not available",
                    extra={
                        "event": "EMBEDDING_UNAVAILABLE",
                        "model": settings.embedding_model,
                    },
                )
                return None
            return provider
        except Exception:
            logger.exception(
                "failed to build ollama embedding provider",
                extra={"event": "EMBEDDING_BUILD_FAILED"},
            )
            return None
    logger.warning(
        "unknown embedding provider %r — using null", name,
        extra={"event": "EMBEDDING_UNKNOWN"},
    )
    return None


def _build_vectors(settings: Settings) -> VectorStore | None:
    """Construct a vector store from settings, or None on failure."""
    name = (settings.vector_store or "null").lower()
    if name in ("null", "none", ""):
        return None
    if name == "chroma":
        try:
            from app.memory.vectors.chroma_store import ChromaVectorStore
            store = ChromaVectorStore(settings.vector_store_path)
            if not store.is_available():
                return None
            return store
        except ProviderUnavailableError as exc:
            logger.warning(
                "chroma vector store unavailable: %s",
                exc,
                extra={"event": "VECTOR_STORE_UNAVAILABLE"},
            )
            return None
        except Exception:
            logger.exception(
                "failed to build chroma vector store",
                extra={"event": "VECTOR_STORE_BUILD_FAILED"},
            )
            return None
    logger.warning(
        "unknown vector store %r — using null", name,
        extra={"event": "VECTOR_STORE_UNKNOWN"},
    )
    return None


def build_memory_manager(
    settings: Settings,
    *,
    backend: MemoryBackend | None = None,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
) -> MemoryManager:
    """Construct a memory manager using the configured backend.

    Backend selection comes from ``MEMORY_BACKEND`` in ``.env``:

    * ``sqlite`` (default) - a local file, no server required.
    * ``postgres`` - a PostgreSQL database. Requires ``POSTGRES_*``
      settings and the ``psycopg[binary,pool]`` package.

    Embeddings and vector store are built from settings when not
    supplied. Both default to null (disabled) so the app works without
    a real embedding model installed.
    """
    if backend is None:
        backend_name = (settings.memory_backend or "sqlite").lower()

        if backend_name == "postgres":
            try:
                from app.memory.backends.postgres_backend import PostgresMemoryBackend

                backend = PostgresMemoryBackend(
                    host=settings.postgres_host,
                    port=settings.postgres_port,
                    database=settings.postgres_database,
                    user=settings.postgres_user,
                    password=settings.postgres_password,
                    schema=settings.postgres_schema,
                )
                logger.info(
                    "memory backend initialised",
                    extra={
                        "event": "MEMORY_BACKEND_READY",
                        "backend": "postgres",
                        "database": settings.postgres_database,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "postgres backend failed (%s); falling back to sqlite",
                    exc,
                    extra={"event": "MEMORY_BACKEND_FALLBACK", "reason": str(exc)},
                )
                backend = SqliteMemoryBackend(settings.memory_database)
                logger.info(
                    "memory backend initialised",
                    extra={
                        "event": "MEMORY_BACKEND_READY",
                        "backend": "sqlite",
                        "path": str(settings.memory_database),
                    },
                )
        elif backend_name == "sqlite":
            backend = SqliteMemoryBackend(settings.memory_database)
            logger.info(
                "memory backend initialised",
                extra={
                    "event": "MEMORY_BACKEND_READY",
                    "backend": "sqlite",
                    "path": str(settings.memory_database),
                },
            )
        else:
            raise MemoryStoreError(
                f"unknown MEMORY_BACKEND {backend_name!r}. "
                f"Supported: 'sqlite', 'postgres'."
            )

    # Build embeddings / vectors if caller didn't supply them.
    emb = embeddings if embeddings is not None else _build_embeddings(settings)
    vec = vectors if vectors is not None else _build_vectors(settings)

    if emb is not None and vec is not None:
        logger.info(
            "semantic recall enabled",
            extra={
                "event": "SEMANTIC_RECALL_ENABLED",
                "embedding_provider": emb.name,
                "vector_store": vec.name,
                "dimension": emb.dimension,
            },
        )
    else:
        logger.info(
            "semantic recall disabled — using keyword search only",
            extra={"event": "SEMANTIC_RECALL_DISABLED"},
        )

    return MemoryManager(
        settings,
        backend,
        embeddings=emb,
        vectors=vec,
    )