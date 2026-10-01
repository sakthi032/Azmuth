"""Task model — the state of one submitted background operation.

A :class:`Task` is mutable by design: its *state* evolves over its lifetime.
Status and timestamp mutations are performed exclusively by the scheduler;
progress reporting is the only mutation a submitted factory is expected to
call on the object it receives.
"""

from __future__ import annotations

import traceback as _traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_task_id() -> str:
    """Opaque task id. Short, URL-safe, collision-free in practice."""
    return uuid.uuid4().hex


class TaskStatus(str, Enum):
    """Lifecycle states. ``str`` subclass for clean serialisation."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


@dataclass(slots=True)
class Task:
    """One background operation."""

    id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)
    cancellable: bool = True

    #: Set to ``True`` by :meth:`TaskManager.cancel` *before* the underlying
    #: asyncio task receives ``CancelledError``. A cooperative factory may
    #: inspect this at its own await points and unwind gracefully.
    cancel_requested: bool = False

    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    progress: float | None = None
    progress_message: str | None = None

    result: Any = None
    error: str | None = None
    error_traceback: str | None = None

    # -- queries -----------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def duration_ms(self) -> float | None:
        """Time from :attr:`started_at` to :attr:`finished_at` (or now)."""
        if self.started_at is None:
            return None
        end = self.finished_at or _now()
        return (end - self.started_at).total_seconds() * 1000.0

    @property
    def age_seconds(self) -> float:
        return (_now() - self.created_at).total_seconds()

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly snapshot.

        ``result`` and ``metadata`` are included verbatim; they may not be
        JSON-serialisable depending on what the factory produced. Callers
        that need guaranteed JSON should coerce before serialising.
        """
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "duration_ms": round(self.duration_ms, 3) if self.duration_ms is not None else None,
            "result": self.result,
            "error": self.error,
            "cancellable": self.cancellable,
            "cancel_requested": self.cancel_requested,
            "metadata": dict(self.metadata),
        }

    # -- factory-facing mutation ------------------------------------------
    def report_progress(self, value: float, message: str | None = None) -> None:
        """Publish a progress update in ``[0.0, 1.0]``.

        Intended to be called by the running factory. Safe to skip entirely.
        """
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"progress must be in [0.0, 1.0], got {value}")
        self.progress = value
        self.progress_message = message

    # -- scheduler-facing mutation (internal) ------------------------------
    def _mark_running(self) -> None:
        self.status = TaskStatus.RUNNING
        self.started_at = _now()

    def _mark_completed(self, result: Any) -> None:
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.progress = 1.0
        self.finished_at = _now()

    def _mark_failed(self, error: BaseException) -> None:
        self.status = TaskStatus.FAILED
        self.error = f"{type(error).__name__}: {error}" if not str(error) else str(error)
        self.error_traceback = "".join(
            _traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.finished_at = _now()

    def _mark_cancelled(self) -> None:
        self.status = TaskStatus.CANCELLED
        self.finished_at = _now()