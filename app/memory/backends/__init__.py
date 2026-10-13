
"""Memory backends."""

from app.memory.backends.base import MemoryBackend
from app.memory.backends.sqlite_backend import SqliteMemoryBackend

try:
    from app.memory.backends.postgres_backend import PostgresMemoryBackend
except ImportError:
    # psycopg is optional. The SQLite backend is always available.
    PostgresMemoryBackend = None  # type: ignore[assignment]

__all__ = [
    "MemoryBackend",
    "PostgresMemoryBackend",
    "SqliteMemoryBackend",
]
