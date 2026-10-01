"""Session model — the working memory of one conversation.

A :class:`Session` holds:

* the recent messages, capped two ways (by *user-turn count* when building
  context, and by a hard *history limit* when appending, so memory stays
  bounded even if a caller never builds context),
* the language Azmuth has been replying in (``None`` means "use the default"),
* the bookkeeping needed to expire idle sessions.

It is mutable by design — that is what a session *is*. It is not frozen and
not persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.llm.messages import Message, Role


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class Session:
    """Working memory for one ongoing conversation."""

    id: str
    user_id: str | None = None
    language: str | None = None
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)
    last_activity: datetime = field(default_factory=_now)

    #: How many *user turns* to replay when building LLM context.
    max_turns: int = 20

    #: Hard cap on how many messages are retained in memory at all. Set well
    #: above ``2 * max_turns`` so a burst of messages within a turn does not
    #: evict context we are about to use, while still never being unbounded.
    history_limit: int = 80

    # -- message intake ----------------------------------------------------
    def add_user_message(self, content: str) -> Message:
        return self._add(Role.USER, content)

    def add_assistant_message(self, content: str) -> Message:
        return self._add(Role.ASSISTANT, content)

    def add_message(self, role: Role, content: str) -> Message:
        return self._add(role, content)

    def _add(self, role: Role, content: str) -> Message:
        message = Message(role=role, content=content)
        self.messages.append(message)
        self._enforce_history_limit()
        self.touch()
        return message

    def _enforce_history_limit(self) -> None:
        if self.history_limit <= 0:
            return
        overflow = len(self.messages) - self.history_limit
        if overflow > 0:
            del self.messages[:overflow]

    # -- introspection -----------------------------------------------------
    def touch(self) -> None:
        """Record activity 'now'. Called automatically on every append."""
        self.last_activity = _now()

    def user_turn_count(self) -> int:
        return sum(1 for m in self.messages if m.role is Role.USER)

    def recent_messages(self, max_turns: int | None = None) -> tuple[Message, ...]:
        """Return the most recent messages covering at most ``max_turns`` user turns.

        A *turn* is a user message. Walking backwards from the newest message,
        we keep messages until we have collected ``max_turns`` user messages
        (or have run out). Everything older than that boundary is dropped.

        ``max_turns`` overrides :attr:`max_turns` when provided. A value of
        ``0`` or less yields no messages.
        """
        limit = self.max_turns if max_turns is None else max_turns
        if limit <= 0 or not self.messages:
            return ()

        user_seen = 0
        start_index = 0
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].role is Role.USER:
                user_seen += 1
                if user_seen == limit:
                    start_index = index
                    break
        return tuple(self.messages[start_index:])

    def is_expired(self, ttl: timedelta) -> bool:
        return _now() - self.last_activity > ttl

    # -- lifecycle ---------------------------------------------------------
    def clear(self) -> None:
        """Drop all messages. Does not change the session id or language."""
        self.messages.clear()
        self.touch()