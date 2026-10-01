"""Internal event system.

Components communicate through events instead of referencing each other
directly. This keeps the brain decoupled from voice, memory, tools, tasks and
UI while still letting every part observe what the rest is doing.

The bus is intentionally minimal: publish/subscribe, sync or async handlers,
and one guarantee — a failing subscriber is logged and does not prevent other
subscribers from running.
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Union

logger = logging.getLogger("azmuth.core.events")


class EventName(str, Enum):
    """Canonical event names.

    Subclassing ``str`` makes them serialise as plain strings, so log
    records and UI messages show ``"USER_INPUT"`` instead of
    ``"EventName.USER_INPUT"``.
    """

    USER_INPUT = "USER_INPUT"
    VOICE_DETECTED = "VOICE_DETECTED"
    WAKE_WORD_DETECTED = "WAKE_WORD_DETECTED"

    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"

    SEARCH_STARTED = "SEARCH_STARTED"
    SEARCH_COMPLETED = "SEARCH_COMPLETED"

    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"

    AUTHENTICATION_CHANGED = "AUTHENTICATION_CHANGED"
    MODE_CHANGED = "MODE_CHANGED"

    SYSTEM_ERROR = "SYSTEM_ERROR"


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable message published on the bus."""

    name: EventName
    payload: Mapping[str, Any] = field(default_factory=dict)
    source: str | None = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


Handler = Callable[[Event], Union[Awaitable[None], None]]
Unsubscriber = Callable[[], None]


class EventBus:
    """Asynchronous publish/subscribe.

    Handlers may be synchronous or async. Sync handlers are called inline,
    async handlers are awaited in registration order.

    Subscribe returns an unsubscriber callable so the same handler can be
    attached to multiple event names without needing a per-name registry.
    """

    def __init__(self) -> None:
        self._handlers: dict[EventName, list[Handler]] = defaultdict(list)
        self._wildcard: list[Handler] = []

    # -- registration ------------------------------------------------------
    def subscribe(self, name: EventName, handler: Handler) -> Unsubscriber:
        handlers = self._handlers[name]
        handlers.append(handler)
        return lambda: self._remove(handlers, handler)

    def subscribe_all(self, handler: Handler) -> Unsubscriber:
        self._wildcard.append(handler)
        return lambda: self._remove(self._wildcard, handler)

    def subscriber_count(self, name: EventName) -> int:
        return len(self._handlers[name]) + len(self._wildcard)

    def clear(self) -> None:
        self._handlers.clear()
        self._wildcard.clear()

    # -- dispatch ----------------------------------------------------------
    async def publish(self, event: Event) -> None:
        # Snapshot so a handler may safely subscribe/unsubscribe during
        # dispatch without mutating the list we are iterating.
        targets = [*self._handlers.get(event.name, ()), *self._wildcard]
        for handler in targets:
            await self._invoke(handler, event)

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _remove(handlers: list[Handler], handler: Handler) -> None:
        try:
            handlers.remove(handler)
        except ValueError:
            pass  # already unsubscribed — idempotent

    @staticmethod
    async def _invoke(handler: Handler, event: Event) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception(
                "event handler failed",
                extra={"event": event.name.value, "source": event.source},
            )


async def emit(bus: EventBus,name: EventName,*,source: str | None = None,**payload: Any,) -> None:
    """Build an :class:`Event` and publish it."""
    await bus.publish(Event(name=name, payload=payload, source=source))