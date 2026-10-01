"""Long-term memory data types.

A :class:`MemoryRecord` is one persistent entry. Its :class:`MemoryKind`
identifies which of the six buckets from the spec it belongs to (user
preferences, facts, events, documents, conversation summaries, notes).
Anything more specific lives in ``metadata`` — the schema does not grow a
column every time a subsystem invents a new field.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_memory_id() -> str:
    """Opaque memory id: hex UUID4. URL-safe, collision-free in practice."""
    return uuid.uuid4().hex


class MemoryKind(str, Enum):
    """Category of a memory record.

    Kept deliberately small — the six buckets the spec names. Anything more
    specific belongs in :attr:`MemoryRecord.metadata`.
    """

    PREFERENCE = "preference"      # User Preferences
    FACT = "fact"                  # Long-Term Memory (stated facts)
    EVENT = "event"                # Long-Term Memory (time-anchored)
    DOCUMENT = "document"          # Knowledge / Documents
    CONVERSATION = "conversation"  # Conversation History (summaries)
    NOTE = "note"                  # Anything else


@dataclass(slots=True)
class MemoryRecord:
    """A single long-term memory entry.

    ``importance`` is a per-record score in ``[0.0, 1.0]`` used as a ranking
    hint. The backend sorts by it as a secondary key when the primary signal
    (vector similarity or keyword match) ties.
    """

    id: str = field(default_factory=new_memory_id)
    user_id: str | None = None
    kind: MemoryKind = MemoryKind.NOTE
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    last_accessed_at: datetime | None = None
    access_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MemoryKind):
            object.__setattr__(self, "kind", MemoryKind(self.kind))
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError(
                f"importance must be in [0.0, 1.0], got {self.importance}"
            )

    def touch_access(self) -> None:
        """Record that this memory was just retrieved (caller persists it)."""
        self.last_accessed_at = _now()
        self.access_count += 1