"""Memory subsystem.

Phase 1 ships:

* **Short-term** (session) memory — the working set the LLM actually sees,
  bounded by :class:`SessionManager` and :class:`ContextBuilder`.
* **Long-term** memory — persistent storage with a replaceable backend
  (SQLite by default), a replaceable vector layer, and a
  :class:`MemoryManager` façade.

The two are deliberately separate: short-term is *replayed to the LLM* and
bounded; long-term is *retrieved from* and can be arbitrarily large.
"""

from app.memory.conversation import ContextBuilder, Session, SessionManager
from app.memory.manager import MemoryManager
from app.memory.models import MemoryKind, MemoryRecord
from app.memory.vectors import (
    EmbeddingProvider,
    NullEmbeddingProvider,
    NullVectorStore,
    VectorStore,
)
from app.memory.manager import build_memory_manager
from app.memory.models import new_memory_id
from app.memory.backends.sqlite_backend import SqliteMemoryBackend
from app.memory.backends.base import MemoryBackend
__all__ = [
    # short-term
    "ContextBuilder",
    "Session",
    "SessionManager",
    # long-term
    "EmbeddingProvider",
    "MemoryBackend",
    "MemoryKind",
    "MemoryManager",
    "MemoryRecord",
    "NullEmbeddingProvider",
    "NullVectorStore",
    "SqliteMemoryBackend",
    "VectorStore",
    "build_memory_manager",
    "new_memory_id",
]