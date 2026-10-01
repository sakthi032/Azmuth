from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import Settings, load_settings
from app.core.errors import MemoryStoreError
from app.memory import (
    EmbeddingProvider,
    MemoryKind,
    MemoryManager,
    MemoryRecord,
    NullEmbeddingProvider,
    NullVectorStore,
    VectorStore,
)
from app.memory.manager import build_memory_manager
from app.memory.models import new_memory_id
from app.memory.backends.sqlite_backend import SqliteMemoryBackend
from app.memory.backends.base import MemoryBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
        },
        create_directories=False,
    )


@pytest.fixture()
def backend(tmp_path: Path) -> MemoryBackend:
    db = tmp_path / "test_memory.db"
    b = SqliteMemoryBackend(db)
    yield b
    b.close()


@pytest.fixture()
def manager(settings: Settings, backend: MemoryBackend) -> MemoryManager:
    return MemoryManager(settings, backend)


# ---------------------------------------------------------------------------
# In-memory embedding + vector store, used to exercise the semantic path
# without pulling in a real model.
# ---------------------------------------------------------------------------
class FixedEmbeddingProvider(EmbeddingProvider):
    """Maps texts to fixed 2-D vectors based on a keyword table."""

    name = "fixed"
    _TABLE = {
        "coffee": [1.0, 0.0],
        "tea": [0.9, 0.1],
        "python": [0.0, 1.0],
        "coding": [0.1, 0.9],
    }

    def is_available(self) -> bool:
        return True

    @property
    def dimension(self) -> int:
        return 2

    def _vector(self, text: str) -> list[float]:
        text = text.lower()
        for key, vec in self._TABLE.items():
            if key in text:
                return vec
        return [0.0, 0.0]

    def embed(self, text: str) -> list[float]:
        return self._vector(text)

    def embed_batch(self, texts):  # noqa: ANN001
        return [self._vector(t) for t in texts]


class TinyVectorStore(VectorStore):
    """Brute-force cosine similarity over a dict. Adequate for tests."""

    name = "tiny"

    def __init__(self) -> None:
        self._data: dict[str, tuple[list[float], dict[str, Any]]] = {}

    def is_available(self) -> bool:
        return True

    def add(self, key, vector, metadata=None):  # noqa: ANN001
        self._data[key] = (list(vector), dict(metadata or {}))

    def search(self, vector, *, top_k: int = 5):  # noqa: ANN001
        scored: list[tuple[str, float]] = []
        for key, (other, _) in self._data.items():
            scored.append((key, _cosine(vector, other)))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored[:top_k]

    def remove(self, key) -> bool:
        return self._data.pop(key, None) is not None

    def clear(self) -> None:
        self._data.clear()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------------------
# MemoryRecord model
# ---------------------------------------------------------------------------
def test_record_defaults_and_kind_coercion() -> None:
    r = MemoryRecord(content="hi")
    assert r.id
    assert r.kind is MemoryKind.NOTE
    assert 0.0 <= r.importance <= 1.0
    assert isinstance(r.created_at, datetime)
    assert r.access_count == 0

    r2 = MemoryRecord(content="hi", kind="preference")  # type: ignore[arg-type]
    assert r2.kind is MemoryKind.PREFERENCE


def test_record_rejects_out_of_range_importance() -> None:
    with pytest.raises(ValueError, match="importance"):
        MemoryRecord(content="x", importance=1.5)
    with pytest.raises(ValueError, match="importance"):
        MemoryRecord(content="x", importance=-0.1)


def test_record_touch_access_updates_fields() -> None:
    r = MemoryRecord(content="x")
    before = r.access_count
    r.touch_access()
    assert r.access_count == before + 1
    assert r.last_accessed_at is not None


def test_new_memory_id_is_unique() -> None:
    assert new_memory_id() != new_memory_id()


# ---------------------------------------------------------------------------
# SqliteMemoryBackend — CRUD
# ---------------------------------------------------------------------------
def test_backend_store_and_get_roundtrip(backend: MemoryBackend) -> None:
    r = MemoryRecord(
        content="Sakthi prefers dark mode",
        user_id="creator",
        kind=MemoryKind.PREFERENCE,
        importance=0.9,
        metadata={"tag": "ui"},
    )
    backend.store(r)

    fetched = backend.get(r.id)
    assert fetched is not None
    assert fetched.content == r.content
    assert fetched.user_id == "creator"
    assert fetched.kind is MemoryKind.PREFERENCE
    assert fetched.importance == 0.9
    assert fetched.metadata == {"tag": "ui"}


def test_backend_get_returns_none_for_missing(backend: MemoryBackend) -> None:
    assert backend.get("nope") is None


def test_backend_store_duplicate_id_raises(backend: MemoryBackend) -> None:
    r = MemoryRecord(id="fixed", content="a")
    backend.store(r)
    with pytest.raises(MemoryStoreError, match="already exists"):
        backend.store(MemoryRecord(id="fixed", content="b"))


def test_backend_update_changes_fields_and_bumps_updated_at(
    backend: MemoryBackend,
) -> None:
    r = MemoryRecord(content="old", importance=0.2)
    backend.store(r)
    original_updated = r.updated_at

    updated = backend.update(r.id, content="new", importance=0.8)
    assert updated.content == "new"
    assert updated.importance == 0.8
    assert updated.updated_at >= original_updated


def test_backend_update_rejects_unknown_field(backend: MemoryBackend) -> None:
    r = MemoryRecord(content="x")
    backend.store(r)
    with pytest.raises(MemoryStoreError, match="cannot update"):
        backend.update(r.id, nonexistent="boom")


def test_backend_update_missing_record_raises(backend: MemoryBackend) -> None:
    with pytest.raises(MemoryStoreError, match="not found"):
        backend.update("missing", content="x")


def test_backend_delete_returns_true_then_false(backend: MemoryBackend) -> None:
    r = MemoryRecord(content="x")
    backend.store(r)
    assert backend.delete(r.id) is True
    assert backend.delete(r.id) is False


def test_backend_touch_increments_access_count(backend: MemoryBackend) -> None:
    r = MemoryRecord(content="x")
    backend.store(r)

    touched = backend.touch(r.id)
    assert touched is not None
    assert touched.access_count == 1
    assert touched.last_accessed_at is not None

    touched2 = backend.touch(r.id)
    assert touched2 is not None and touched2.access_count == 2


def test_backend_touch_missing_returns_none(backend: MemoryBackend) -> None:
    assert backend.touch("missing") is None


# ---------------------------------------------------------------------------
# SqliteMemoryBackend — search / list / count
# ---------------------------------------------------------------------------
def _seed(backend: MemoryBackend) -> None:
    backend.store(MemoryRecord(
        content="Sakthi loves coffee", user_id="sakthi",
        kind=MemoryKind.PREFERENCE, importance=0.7,
    ))
    backend.store(MemoryRecord(
        content="Sakthi codes in Python", user_id="sakthi",
        kind=MemoryKind.FACT, importance=0.9,
    ))
    backend.store(MemoryRecord(
        content="Weather was hot yesterday", user_id="sakthi",
        kind=MemoryKind.EVENT, importance=0.3,
    ))
    backend.store(MemoryRecord(
        content="Coffee shops near office", user_id="guest",
        kind=MemoryKind.NOTE, importance=0.5,
    ))


def test_backend_search_by_query(backend: MemoryBackend) -> None:
    _seed(backend)
    hits = backend.search("coffee")
    assert len(hits) == 2
    assert all("coffee" in h.content.lower() for h in hits)


def test_backend_search_is_case_insensitive(backend: MemoryBackend) -> None:
    _seed(backend)
    assert backend.search("COFFEE") == backend.search("coffee")


def test_backend_search_filters_by_user(backend: MemoryBackend) -> None:
    _seed(backend)
    hits = backend.search("coffee", user_id="sakthi")
    assert len(hits) == 1
    assert hits[0].user_id == "sakthi"


def test_backend_search_filters_by_kind(backend: MemoryBackend) -> None:
    _seed(backend)
    hits = backend.search("", kind=MemoryKind.FACT)
    assert len(hits) == 1
    assert hits[0].kind is MemoryKind.FACT


def test_backend_search_respects_min_importance(backend: MemoryBackend) -> None:
    _seed(backend)
    hits = backend.search("", min_importance=0.8)
    assert len(hits) == 1
    assert hits[0].importance == 0.9


def test_backend_search_empty_query_matches_all(backend: MemoryBackend) -> None:
    _seed(backend)
    hits = backend.search("")
    assert len(hits) == 4


def test_backend_search_orders_by_importance(backend: MemoryBackend) -> None:
    _seed(backend)
    hits = backend.search("")
    importances = [h.importance for h in hits]
    assert importances == sorted(importances, reverse=True)


def test_backend_search_limit(backend: MemoryBackend) -> None:
    _seed(backend)
    assert len(backend.search("", limit=2)) == 2


def test_backend_list_pagination(backend: MemoryBackend) -> None:
    _seed(backend)
    page1 = backend.list(limit=2, offset=0)
    page2 = backend.list(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    ids = {r.id for r in page1} | {r.id for r in page2}
    assert len(ids) == 4


def test_backend_list_filters(backend: MemoryBackend) -> None:
    _seed(backend)
    assert len(backend.list(user_id="sakthi")) == 3
    assert len(backend.list(kind=MemoryKind.EVENT)) == 1
    assert len(backend.list(user_id="guest", kind=MemoryKind.NOTE)) == 1


def test_backend_count_with_filters(backend: MemoryBackend) -> None:
    _seed(backend)
    assert backend.count() == 4
    assert backend.count(user_id="sakthi") == 3
    assert backend.count(kind=MemoryKind.NOTE) == 1
    assert backend.count(user_id="guest", kind=MemoryKind.NOTE) == 1


def test_backend_clear_removes_everything(backend: MemoryBackend) -> None:
    _seed(backend)
    backend.clear()
    assert backend.count() == 0


# ---------------------------------------------------------------------------
# SqliteMemoryBackend — persistence across reconnection
# ---------------------------------------------------------------------------
def test_backend_persists_across_connections(tmp_path: Path) -> None:
    db = tmp_path / "persist.db"

    b1 = SqliteMemoryBackend(db)
    b1.store(MemoryRecord(id="keep-me", content="persist this"))
    b1.close()

    b2 = SqliteMemoryBackend(db)
    try:
        fetched = b2.get("keep-me")
        assert fetched is not None and fetched.content == "persist this"
    finally:
        b2.close()


# ---------------------------------------------------------------------------
# Null providers
# ---------------------------------------------------------------------------
def test_null_providers_are_unavailable() -> None:
    assert NullEmbeddingProvider().is_available() is False
    assert NullVectorStore().is_available() is False


def test_null_vector_store_behaves_safely() -> None:
    store = NullVectorStore()
    store.add("k", [1.0, 2.0])
    assert store.search([1.0, 2.0]) == []
    assert store.remove("k") is False
    store.clear()  # must not raise


# ---------------------------------------------------------------------------
# MemoryManager — keyword path
# ---------------------------------------------------------------------------
def test_manager_store_then_get_increments_access(manager: MemoryManager) -> None:
    rec = manager.store("Sakthi likes coffee", user_id="sakthi")
    assert rec.id

    fetched = manager.get(rec.id)
    assert fetched is not None
    assert fetched.access_count == 1

    fetched2 = manager.get(rec.id)
    assert fetched2 is not None and fetched2.access_count == 2


def test_manager_get_missing_returns_none(manager: MemoryManager) -> None:
    assert manager.get("missing") is None


def test_manager_update_reflects_change(manager: MemoryManager) -> None:
    rec = manager.store("old")
    updated = manager.update(rec.id, content="new", importance=0.9)
    assert updated.content == "new"
    assert updated.importance == 0.9


def test_manager_delete_removes_record(manager: MemoryManager) -> None:
    rec = manager.store("bye")
    assert manager.delete(rec.id) is True
    assert manager.get(rec.id) is None


def test_manager_search_delegates_to_backend(manager: MemoryManager) -> None:
    manager.store("coffee time", user_id="sakthi")
    manager.store("python time", user_id="sakthi")
    hits = manager.search("coffee")
    assert len(hits) == 1 and hits[0].content == "coffee time"


def test_manager_list_for_user(manager: MemoryManager) -> None:
    manager.store("a", user_id="u1")
    manager.store("b", user_id="u1")
    manager.store("c", user_id="u2")
    assert len(manager.list_for_user("u1")) == 2


def test_manager_count(manager: MemoryManager) -> None:
    manager.store("a", user_id="u1")
    manager.store("b", user_id="u1")
    manager.store("c", user_id="u2")
    assert manager.count() == 3
    assert manager.count(user_id="u1") == 2


def test_manager_recall_without_vectors_uses_keyword(
    manager: MemoryManager,
) -> None:
    manager.store("Sakthi codes in Python")
    manager.store("Weather is hot")
    assert manager.supports_semantic_recall() is False

    hits = manager.recall("Python")
    assert len(hits) == 1
    assert "Python" in hits[0].content


def test_manager_clear_removes_all(manager: MemoryManager) -> None:
    manager.store("a")
    manager.store("b")
    manager.clear()
    assert manager.count() == 0


# ---------------------------------------------------------------------------
# MemoryManager — semantic path
# ---------------------------------------------------------------------------
def test_manager_uses_semantic_recall_when_available(
    settings: Settings, backend: MemoryBackend
) -> None:
    embeddings = FixedEmbeddingProvider()
    vectors = TinyVectorStore()
    manager = MemoryManager(settings, backend, embeddings=embeddings, vectors=vectors)

    manager.store("Sakthi drinks coffee")
    manager.store("Sakthi writes Python")
    manager.store("Sakthi drinks tea")

    assert manager.supports_semantic_recall() is True

    # "coffee" query vector [1, 0] is closest to the coffee memory.
    hits = manager.recall("coffee", top_k=2)
    assert hits
    assert "coffee" in hits[0].content.lower()


def test_manager_semantic_recall_falls_back_when_empty(
    settings: Settings, backend: MemoryBackend
) -> None:
    # No memory has been indexed, so vector search returns nothing.
    # recall() must then fall back to keyword search.
    manager = MemoryManager(
        settings, backend,
        embeddings=FixedEmbeddingProvider(),
        vectors=TinyVectorStore(),
    )
    # Store directly in the backend, bypassing the manager (so no indexing).
    backend.store(MemoryRecord(content="unindexed coffee"))

    hits = manager.recall("coffee")
    assert any("coffee" in h.content for h in hits)


def test_manager_semantic_recall_handles_embedding_failure(
    settings: Settings, backend: MemoryBackend
) -> None:
    class BrokenEmbeddings(EmbeddingProvider):
        name = "broken"
        def is_available(self) -> bool: return True
        @property
        def dimension(self) -> int: return 2
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("boom")
        def embed_batch(self, texts):  # noqa: ANN001
            raise RuntimeError("boom")

    manager = MemoryManager(
        settings, backend,
        embeddings=BrokenEmbeddings(),
        vectors=TinyVectorStore(),
    )
    manager.store("coffee memory")  # indexing silently fails

    hits = manager.recall("coffee")   # falls back to keyword search
    assert any("coffee" in h.content for h in hits)


def test_manager_delete_removes_from_vector_store(
    settings: Settings, backend: MemoryBackend
) -> None:
    vectors = TinyVectorStore()
    manager = MemoryManager(
        settings, backend,
        embeddings=FixedEmbeddingProvider(),
        vectors=vectors,
    )

    rec = manager.store("coffee")
    assert any(k == rec.id for k, _ in vectors.search([1.0, 0.0], top_k=10))

    manager.delete(rec.id)
    assert not any(k == rec.id for k, _ in vectors.search([1.0, 0.0], top_k=10))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_build_memory_manager_uses_configured_database(settings: Settings) -> None:
    manager = build_memory_manager(settings)
    try:
        assert isinstance(manager.backend, SqliteMemoryBackend)
        assert manager.supports_semantic_recall() is False
    finally:
        manager.close()


def test_manager_close_is_idempotent(settings: Settings, tmp_path: Path) -> None:
    manager = build_memory_manager(settings)
    manager.close()
    manager.close()  # must not raise