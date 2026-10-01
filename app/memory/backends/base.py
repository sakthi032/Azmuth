"""The persistence interface every memory backend must satisfy."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.memory.models import MemoryKind, MemoryRecord


class MemoryBackend(ABC):
    """Persistent storage for :class:`MemoryRecord` objects.

    Implementations must be safe to call directly from the event loop for
    *fast, local* operations. Backends that do genuinely slow I/O (network,
    large disk scans) should either be wrapped in :func:`asyncio.to_thread`
    by their callers, or the ABC is extended to async in a later step.

    Concrete backends must never raise on *not found* — they return
    ``None`` or ``False``. Genuine storage failures raise
    :class:`~app.core.errors.MemoryStoreError`.
    """

    # -- CRUD --------------------------------------------------------------
    @abstractmethod
    def store(self, record: MemoryRecord) -> MemoryRecord:
        """Insert a new record. Raises if ``record.id`` already exists."""

    @abstractmethod
    def get(self, memory_id: str) -> MemoryRecord | None:
        """Return the record or ``None``. Must not mutate access stats."""

    @abstractmethod
    def update(self, memory_id: str, **fields: object) -> MemoryRecord:
        """Update whitelisted fields on an existing record and return it."""

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """Return ``True`` if a record was removed, ``False`` otherwise."""

    # -- query -------------------------------------------------------------
    @abstractmethod
    def search(
        self,
        query: str,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        limit: int = 20,
        min_importance: float = 0.0,
    ) -> list[MemoryRecord]:
        """Return records matching ``query``.

        Phase 1 uses simple keyword matching. Backends are free to layer
        FTS, trigram similarity, or anything else *behind this same method*
        without changing callers.
        """

    @abstractmethod
    def list(
        self,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """Return records in reverse-chronological order of ``updated_at``."""

    @abstractmethod
    def count(
        self,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
    ) -> int:
        """Count records matching the filters."""

    # -- lifecycle ---------------------------------------------------------
    @abstractmethod
    def clear(self) -> None:
        """Delete every record. Intended for tests and explicit resets."""

    @abstractmethod
    def close(self) -> None:
        """Release resources. Must be idempotent."""