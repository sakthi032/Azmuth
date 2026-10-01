"""SQLite-backed long-term memory.

Chosen as the default because it:

* ships with Python (no dependency),
* persists to a single file under ``data/memory/``,
* handles a personal assistant's volume with room to spare,
* is trivially inspectable with any SQLite tool during development.

The class is synchronous. SQLite operations complete in microseconds to low
milliseconds; callers that need to avoid blocking the event loop can wrap
calls with :func:`asyncio.to_thread`. The public interface does not change
either way.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config.logging_setup import get_logger
from app.core.errors import MemoryStoreError
from app.memory.backends.base import MemoryBackend
from app.memory.models import MemoryKind, MemoryRecord

logger = get_logger("memory.sqlite")

#: Fields a caller may change via :meth:`update`. Everything else is either
#: immutable (``id``, ``created_at``) or managed by dedicated methods
#: (``access_count`` / ``last_accessed_at`` via :meth:`touch`).
_UPDATABLE_FIELDS = frozenset(
    {"user_id", "kind", "content", "metadata", "importance"}
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id               TEXT PRIMARY KEY,
    user_id          TEXT,
    kind             TEXT NOT NULL,
    content          TEXT NOT NULL,
    metadata         TEXT NOT NULL DEFAULT '{}',
    importance       REAL NOT NULL DEFAULT 0.5,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    last_accessed_at TEXT,
    access_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_user     ON memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_kind     ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_updated  ON memories(updated_at DESC);
"""


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        user_id=row["user_id"],
        kind=MemoryKind(row["kind"]),
        content=row["content"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        importance=row["importance"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        last_accessed_at=(
            _parse_dt(row["last_accessed_at"]) if row["last_accessed_at"] else None
        ),
        access_count=row["access_count"],
    )


class SqliteMemoryBackend(MemoryBackend):
    """Persistent :class:`MemoryBackend` implementation."""

    name = "sqlite"

    def __init__(self, database: str | Path) -> None:
        self._path = Path(database)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # One connection, guarded by a lock. SQLite requires serialised
        # writes; the lock keeps concurrent callers honest without
        # `check_same_thread=False` footguns.
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self._path))
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise MemoryStoreError(
                f"could not open memory database at {self._path}: {exc}"
            ) from exc

        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- CRUD --------------------------------------------------------------
    def store(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO memories (
                        id, user_id, kind, content, metadata, importance,
                        created_at, updated_at, last_accessed_at, access_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.user_id,
                        record.kind.value,
                        record.content,
                        json.dumps(record.metadata, ensure_ascii=False),
                        record.importance,
                        _utc_iso(record.created_at),
                        _utc_iso(record.updated_at),
                        _utc_iso(record.last_accessed_at)
                        if record.last_accessed_at
                        else None,
                        record.access_count,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise MemoryStoreError(
                    f"memory {record.id!r} already exists"
                ) from exc
            except sqlite3.Error as exc:
                raise MemoryStoreError(f"failed to store memory: {exc}") from exc
        return record

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def update(self, memory_id: str, **fields: object) -> MemoryRecord:
        unknown = set(fields) - _UPDATABLE_FIELDS - {"access_count", "last_accessed_at"}
        if unknown:
            raise MemoryStoreError(
                f"cannot update memory field(s): {sorted(unknown)}"
            )
        if not fields:
            existing = self.get(memory_id)
            if existing is None:
                raise MemoryStoreError(f"memory {memory_id!r} not found")
            return existing

        assignments: list[str] = []
        values: list[object] = []
        for key, value in fields.items():
            if key == "kind" and not isinstance(value, MemoryKind):
                value = MemoryKind(value)  # accept plain strings
            if key == "metadata":
                value = json.dumps(value or {}, ensure_ascii=False)
            if key == "last_accessed_at" and value is not None:
                value = _utc_iso(value)  # type: ignore[arg-type]
            assignments.append(f"{key} = ?")
            values.append(value)

        assignments.append("updated_at = ?")
        values.append(_utc_iso(datetime.now(timezone.utc)))
        values.append(memory_id)

        with self._lock:
            cur = self._conn.execute(
                f"UPDATE memories SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            self._conn.commit()
            if cur.rowcount == 0:
                raise MemoryStoreError(f"memory {memory_id!r} not found")

        updated = self.get(memory_id)
        assert updated is not None  # we just updated it
        return updated

    def touch(self, memory_id: str) -> MemoryRecord | None:
        """Increment access stats. Returns the updated record or ``None``."""
        now = _utc_iso(datetime.now(timezone.utc))
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE memories
                   SET last_accessed_at = ?, access_count = access_count + 1
                 WHERE id = ?
                """,
                (now, memory_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
        return self.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    # -- query -------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        limit: int = 20,
        min_importance: float = 0.0,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[object] = []

        if query:
            # Simple substring match, case-insensitive. Sufficient for
            # Phase 1; a future backend may layer FTS5 without any caller
            # changes.
            clauses.append("LOWER(content) LIKE ?")
            params.append(f"%{query.lower()}%")
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if min_importance > 0.0:
            clauses.append("importance >= ?")
            params.append(min_importance)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM memories
            {where}
            ORDER BY importance DESC, updated_at DESC
            LIMIT ?
        """
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def list(
        self,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM memories
            {where}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def count(
        self,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM memories {where}", params
            ).fetchone()
        return int(row["n"])

    # -- lifecycle ---------------------------------------------------------
    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM memories")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]