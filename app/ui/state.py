
"""Observable UI state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Speaker(str, Enum):
    USER = "user"
    AZMUTH = "azmuth"
    SYSTEM = "system"


class LogLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ConversationEntry:
    """One line in the transcript."""

    id: int
    speaker: Speaker
    text: str
    language: str | None = None
    source: str = "text"
    level: LogLevel = LogLevel.INFO
    model: str = ""
    provider: str = ""
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not isinstance(self.speaker, Speaker):
            object.__setattr__(self, "speaker", Speaker(self.speaker))
        if not isinstance(self.level, LogLevel):
            object.__setattr__(self, "level", LogLevel(self.level))

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "speaker": self.speaker.value,
            "text": self.text,
            "language": self.language,
            "source": self.source,
            "level": self.level.value,
            "model": self.model,
            "provider": self.provider,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class TaskSummary:
    id: str
    name: str
    status: str
    progress: float | None = None
    message: str | None = None
    created_at: datetime = field(default_factory=_now)

    @property
    def is_active(self) -> bool:
        return self.status in ("pending", "running")

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    llm: str = "unknown"
    memory: str = "unknown"
    voice_input: str = "unknown"
    tts: str = "unknown"
    wake_word: str = "unknown"
    search: str = "unknown"
    task_manager: str = "unknown"
    security: str = "unknown"
    ui: str = "online"

    def as_dict(self) -> dict[str, str]:
        return {
            "llm": self.llm,
            "memory": self.memory,
            "voice_input": self.voice_input,
            "tts": self.tts,
            "wake_word": self.wake_word,
            "search": self.search,
            "task_manager": self.task_manager,
            "security": self.security,
            "ui": self.ui,
        }


Listener = Callable[["UIState"], None]


class UIState:
    """The mutable state the UI observes."""

    def __init__(self) -> None:
        self._conversation: list[ConversationEntry] = []
        self._tasks: dict[str, TaskSummary] = {}
        self._status = StatusSnapshot()
        self._mode = "restricted"
        self._user_id: str | None = None
        self._last_error: str | None = None
        self._revision = 0
        self._next_id = 1
        self._listeners: list[Listener] = []

    # -- read-only views ---------------------------------------------------
    @property
    def conversation(self) -> tuple[ConversationEntry, ...]:
        return tuple(self._conversation)

    @property
    def tasks(self) -> tuple[TaskSummary, ...]:
        return tuple(self._tasks.values())

    @property
    def active_tasks(self) -> tuple[TaskSummary, ...]:
        return tuple(t for t in self._tasks.values() if t.is_active)

    @property
    def status(self) -> StatusSnapshot:
        return self._status

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def user_id(self) -> str | None:
        return self._user_id

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def revision(self) -> int:
        return self._revision

    # -- subscription ------------------------------------------------------
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._remove_listener(listener)

    def _remove_listener(self, listener: Listener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def _touch(self) -> None:
        self._revision += 1
        for listener in tuple(self._listeners):
            try:
                listener(self)
            except Exception:  # noqa: BLE001
                self._listeners.remove(listener)

    # -- transcript --------------------------------------------------------
    def add_user_message(
        self,
        text: str,
        *,
        language: str | None = None,
        source: str = "text",
    ) -> ConversationEntry:
        return self._append(Speaker.USER, text, language=language, source=source)

    def add_assistant_message(
        self,
        text: str,
        *,
        language: str | None = None,
        source: str = "text",
        model: str = "",
        provider: str = "",
    ) -> ConversationEntry:
        return self._append(
            Speaker.AZMUTH, text,
            language=language, source=source,
            model=model, provider=provider,
        )

    def add_system_message(
        self, text: str, *, level: LogLevel = LogLevel.INFO
    ) -> ConversationEntry:
        return self._append(Speaker.SYSTEM, text, level=level)

    def _append(
        self,
        speaker: Speaker,
        text: str,
        *,
        language: str | None = None,
        source: str = "text",
        level: LogLevel = LogLevel.INFO,
        model: str = "",
        provider: str = "",
    ) -> ConversationEntry:
        entry = ConversationEntry(
            id=self._next_id,
            speaker=speaker,
            text=text,
            language=language,
            source=source,
            level=level,
            model=model,
            provider=provider,
        )
        self._next_id += 1
        self._conversation.append(entry)
        self._touch()
        return entry

    def clear_conversation(self) -> None:
        self._conversation.clear()
        self._touch()

    # -- tasks -------------------------------------------------------------
    def upsert_task(
        self,
        task_id: str,
        name: str,
        status: str,
        *,
        progress: float | None = None,
        message: str | None = None,
    ) -> TaskSummary:
        existing = self._tasks.get(task_id)
        summary = TaskSummary(
            id=task_id,
            name=name,
            status=status,
            progress=progress if progress is not None else (existing.progress if existing else None),
            message=message if message is not None else (existing.message if existing else None),
            created_at=existing.created_at if existing else _now(),
        )
        self._tasks[task_id] = summary
        self._touch()
        return summary

    def remove_task(self, task_id: str) -> bool:
        removed = self._tasks.pop(task_id, None) is not None
        if removed:
            self._touch()
        return removed

    def clear_finished_tasks(self) -> int:
        before = len(self._tasks)
        self._tasks = {tid: t for tid, t in self._tasks.items() if t.is_active}
        removed = before - len(self._tasks)
        if removed:
            self._touch()
        return removed

    # -- mode / identity ---------------------------------------------------
    def set_mode(self, mode: str, *, user_id: str | None = None) -> None:
        if mode == self._mode and user_id == self._user_id:
            return
        self._mode = mode
        if user_id is not None:
            self._user_id = user_id
        self._touch()

    # -- status ------------------------------------------------------------
    def set_status(self, **fields: str) -> None:
        self._status = replace(self._status, **fields)
        self._touch()

    # -- errors ------------------------------------------------------------
    def set_error(self, message: str | None) -> None:
        self._last_error = message
        self._touch()
