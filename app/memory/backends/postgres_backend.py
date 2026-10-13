
"""PostgreSQL memory backend.

Uses ``psycopg`` (psycopg3) with a connection pool. The pool is what
makes this backend safe under the pipeline's threaded execution model:
each worker thread checks out a connection, uses it, and returns it.
No manual locking, no ``check_same_thread=False`` workaround.

Schema is identical in shape to the SQLite backend, so records move
between the two without transformation. Search uses a case-insensitive
LIKE; a future version can add a ``tsvector`` full-text index or
``pgvector`` for semantic recall without changing callers.

Configure via ``.env``:
    MEMORY_BACKEND=postgres
    POSTGRES_HOST=localhost
    POSTGRES_PORT=3207
    POSTGRES_DATABASE=azmuth
    POSTGRES_USER=postgres
    POSTGRES_PASSWORD=solo1925
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app.config.logging_setup import get_logger
from app.core.errors import MemoryStoreError
from app.memory.backends.base import MemoryBackend
from app.memory.models import MemoryKind, MemoryRecord

logger = get_logger("memory.postgres")

_UPDATABLE_FIELDS = frozenset(
    {"user_id", "kind", "content", "metadata", "importance"}
)


def _schema_sql(schema: str) -> str:
    """Return the CREATE TABLE / INDEX statements for the given schema."""
    # The schema name is validated as an identifier before this is called.
    s = schema
    return f"""
    CREATE TABLE IF NOT EXISTS {s}.memories (
        id                TEXT PRIMARY KEY,
        user_id           TEXT,
        kind              TEXT NOT NULL,
        content           TEXT NOT NULL,
        metadata          JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        importance        REAL NOT NULL DEFAULT 0.5,
        created_at        TIMESTAMPTZ NOT NULL,
        updated_at        TIMESTAMPTZ NOT NULL,
        last_accessed_at  TIMESTAMPTZ,
        access_count      INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_memories_user
        ON {s}.memories(user_id);
    CREATE INDEX IF NOT EXISTS idx_memories_kind
        ON {s}.memories(kind);
    CREATE INDEX IF NOT EXISTS idx_memories_updated
        ON {s}.memories(updated_at DESC);
    """


def _validate_identifier(value: str, *, label: str) -> str:
    """Reject anything that is not a bare SQL identifier.

    Schema names are interpolated into DDL, so they must be strictly
    validated. This is not a defence against a hostile user - the value
    comes from the operator's own .env - it is a defence against typos.
    """
    if not value:
        raise MemoryStoreError(f"{label} must not be empty")
    if not value.replace("_", "").isalnum():
        raise MemoryStoreError(
            f"{label} {value!r} must contain only letters, digits, and underscores"
        )
    return value


class PostgresMemoryBackend(MemoryBackend):
    """Persistent :class:`MemoryBackend` backed by PostgreSQL."""

    name = "postgres"

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 3207,
        database: str = "azmuth",
        user: str = "postgres",
        password: str = "solo1925",
        schema: str = "public",
        min_pool_size: int = 1,
        max_pool_size: int = 8,
    ) -> None:
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise MemoryStoreError(
                "the postgres backend requires 'psycopg[binary,pool]'. "
                "Install it with: pip install \"psycopg[binary,pool]\""
            ) from exc

        self._schema = _validate_identifier(schema, label="schema")

        conninfo = (
            f"host={host} port={port} dbname={database} "
            f"user={user} password={password}"
        )
        try:
            self._pool = ConnectionPool(
                conninfo=conninfo,
                min_size=min_pool_size,
                max_size=max_pool_size,
                open=True,
                timeout=10.0,
            )
        except Exception as exc:  # noqa: BLE001 - psycopg raises varied types
            raise MemoryStoreError(
                f"could not create PostgreSQL pool for {database}@{host}: {exc}"
            ) from exc

        try:
            self._ensure_schema()
        except Exception as exc:  # noqa: BLE001
            self._pool.close()
            raise MemoryStoreError(
                f"could not initialise schema {self._schema!r}: {exc}"
            ) from exc

    # -- helpers -----------------------------------------------------------
    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        """Check out a connection and yield a cursor. Returns it on exit."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                yield cur

    def _ensure_schema(self) -> None:
        with self._cursor() as cur:
            cur.execute(_schema_sql(self._schema))
            cur.connection.commit()

    def _table(self) -> str:
        return f"{self._schema}.memories"

    # -- CRUD --------------------------------------------------------------
    def store(self, record: MemoryRecord) -> MemoryRecord:
        sql = f"""
            INSERT INTO {self._table()} (
                id, user_id, kind, content, metadata, importance,
                created_at, updated_at, last_accessed_at, access_count
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            record.id,
            record.user_id,
            record.kind.value,
            record.content,
            json.dumps(record.metadata, ensure_ascii=False),
            record.importance,
            record.created_at,
            record.updated_at,
            record.last_accessed_at,
            record.access_count,
        )
        try:
            with self._cursor() as cur:
                cur.execute(sql, params)
                cur.connection.commit()
        except Exception as exc:  # noqa: BLE001
            # Unique violation is the one case worth naming precisely.
            msg = str(exc).lower()
            if "duplicate key" in msg or "unique constraint" in msg:
                raise MemoryStoreError(
                    f"memory {record.id!r} already exists"
                ) from exc
            raise MemoryStoreError(f"failed to store memory: {exc}") from exc
        return record

    def get(self, memory_id: str) -> MemoryRecord | None:
        sql = f"SELECT * FROM {self._table()} WHERE id = %s"
        try:
            with self._cursor() as cur:
                cur.execute(sql, (memory_id,))
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            raise MemoryStoreError(f"failed to read memory: {exc}") from exc
        return _row_to_record(cur, row) if row else None

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

        columns: list[str] = []
        values: list[object] = []
        for key, value in fields.items():
            if key == "kind" and not isinstance(value, MemoryKind):
                value = MemoryKind(value)
                columns.append(f"{key} = %s")
                values.append(value.value)
                continue
            if key == "metadata":
                columns.append(f"{key} = %s::jsonb")
                values.append(json.dumps(value or {}, ensure_ascii=False))
                continue
            columns.append(f"{key} = %s")
            values.append(value)

        columns.append("updated_at = %s")
        values.append(datetime.now(timezone.utc))
        values.append(memory_id)

        sql = f"UPDATE {self._table()} SET {', '.join(columns)} WHERE id = %s"
        try:
            with self._cursor() as cur:
                cur.execute(sql, values)
                if cur.rowcount == 0:
                    raise MemoryStoreError(f"memory {memory_id!r} not found")
                cur.connection.commit()
        except MemoryStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MemoryStoreError(f"failed to update memory: {exc}") from exc

        updated = self.get(memory_id)
        assert updated is not None
        return updated

    def touch(self, memory_id: str) -> MemoryRecord | None:
        now = datetime.now(timezone.utc)
        sql = f"""
            UPDATE {self._table()}
               SET last_accessed_at = %s,
                   access_count = access_count + 1
             WHERE id = %s
        """
        try:
            with self._cursor() as cur:
                cur.execute(sql, (now, memory_id))
                cur.connection.commit()
                if cur.rowcount == 0:
                    return None
        except Exception as exc:  # noqa: BLE001
            raise MemoryStoreError(f"failed to touch memory: {exc}") from exc
        return self.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        sql = f"DELETE FROM {self._table()} WHERE id = %s"
        try:
            with self._cursor() as cur:
                cur.execute(sql, (memory_id,))
                cur.connection.commit()
                return cur.rowcount > 0
        except Exception as exc:  # noqa: BLE001
            raise MemoryStoreError(f"failed to delete memory: {exc}") from exc

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
        where: list[str] = []
        params: list[object] = []

        if query:
            where.append("LOWER(content) LIKE %s")
            params.append(f"%{query.lower()}%")
        if user_id is not None:
            where.append("user_id = %s")
            params.append(user_id)
        if kind is not None:
            where.append("kind = %s")
            params.append(kind.value)
        if min_importance > 0.0:
            where.append("importance >= %s")
            params.append(min_importance)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT * FROM {self._table()}
            {clause}
            ORDER BY importance DESC, updated_at DESC
            LIMIT %s
        """
        params.append(limit)
        try:
            with self._cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise MemoryStoreError(f"failed to search memories: {exc}") from exc
        return [_row_to_record(cur, r) for r in rows]

    def list(
        self,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        where: list[str] = []
        params: list[object] = []
        if user_id is not None:
            where.append("user_id = %s")
            params.append(user_id)
        if kind is not None:
            where.append("kind = %s")
            params.append(kind.value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT * FROM {self._table()}
            {clause}
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])
        try:
            with self._cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise MemoryStoreError(f"failed to list memories: {exc}") from exc
        return [_row_to_record(cur, r) for r in rows]

    def count(
        self,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
    ) -> int:
        where: list[str] = []
        params: list[object] = []
        if user_id is not None:
            where.append("user_id = %s")
            params.append(user_id)
        if kind is not None:
            where.append("kind = %s")
            params.append(kind.value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT COUNT(*) FROM {self._table()} {clause}"
        try:
            with self._cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            raise MemoryStoreError(f"failed to count memories: {exc}") from exc
        return int(row[0]) if row else 0

    # -- lifecycle ---------------------------------------------------------
    def clear(self) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(f"DELETE FROM {self._table()}")
                cur.connection.commit()
        except Exception as exc:  # noqa: BLE001
            raise MemoryStoreError(f"failed to clear memories: {exc}") from exc

    def close(self) -> None:
        """Release the connection pool.

        Windows process teardown routinely takes longer than the pool's
        default 5-second shutdown budget. Ten seconds is enough in
        practice and the extra wait only happens at exit.
        """
        try:
            self._pool.close(timeout=10.0)
        except TypeError:
            try:
                self._pool.close()
            except Exception:  # noqa: BLE001
                logger.exception("failed to close postgres pool")
        except Exception:  # noqa: BLE001
            logger.exception("failed to close postgres pool")


# --------------------------------------------------------------------------
# Row -> record
# --------------------------------------------------------------------------
def _row_to_record(cur: Any, row: tuple) -> MemoryRecord:
    """Map a SELECT * row to a MemoryRecord.

    Column order must match the schema declared in :func:`_schema_sql`:
        id, user_id, kind, content, metadata, importance,
        created_at, updated_at, last_accessed_at, access_count
    """
    metadata = row[4]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    elif metadata is None:
        metadata = {}

    return MemoryRecord(
        id=row[0],
        user_id=row[1],
        kind=MemoryKind(row[2]),
        content=row[3],
        metadata=metadata,
        importance=float(row[5]),
        created_at=row[6],
        updated_at=row[7],
        last_accessed_at=row[8],
        access_count=int(row[9]),
    )


__all__ = ["PostgresMemoryBackend"]

