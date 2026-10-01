"""Translate event-bus events into UI state changes.

A single subscription point for the whole UI. The reducer does not know
about widgets; it mutates :class:`UIState` and the widgets observe the
state. That separation is what keeps the UI testable without a display.

Every handler is defensive: a malformed event payload produces a warning
in the transcript rather than an exception that would disconnect the
reducer from the bus.
"""

from __future__ import annotations

from app.config.logging_setup import get_logger
from app.core.events import Event, EventBus, EventName
from app.ui.state import LogLevel, UIState

logger = get_logger("ui.reducer")


class UIReducer:
    """Subscribe to the bus and keep ``UIState`` in sync."""

    def __init__(self, state: UIState, bus: EventBus | None) -> None:
        self._state = state
        self._bus = bus
        self._unsubscribers: list = []
        if bus is not None:
            self._subscribe(bus)

    # -- lifecycle ---------------------------------------------------------
    def detach(self) -> None:
        """Stop listening. Safe to call more than once."""
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        self._unsubscribers.clear()

    # -- wiring ------------------------------------------------------------
    def _subscribe(self, bus: EventBus) -> None:
        handlers = {
            EventName.TASK_STARTED: self._on_task_started,
            EventName.TASK_COMPLETED: self._on_task_finished,
            EventName.TASK_FAILED: self._on_task_failed,
            EventName.TOOL_STARTED: self._on_tool_started,
            EventName.TOOL_COMPLETED: self._on_tool_finished,
            EventName.SEARCH_STARTED: self._on_search_started,
            EventName.SEARCH_COMPLETED: self._on_search_finished,
            EventName.WAKE_WORD_DETECTED: self._on_wake_word,
            EventName.VOICE_DETECTED: self._on_voice_detected,
            EventName.AUTHENTICATION_CHANGED: self._on_auth_changed,
            EventName.MODE_CHANGED: self._on_mode_changed,
            EventName.SYSTEM_ERROR: self._on_system_error,
        }
        for name, handler in handlers.items():
            self._unsubscribers.append(bus.subscribe(name, handler))

    # -- handlers ----------------------------------------------------------
    async def _on_task_started(self, event: Event) -> None:
        task_id = str(event.payload.get("task_id", ""))
        if not task_id:
            return
        self._state.upsert_task(
            task_id,
            str(event.payload.get("task_name", task_id)),
            "running",
        )

    async def _on_task_finished(self, event: Event) -> None:
        task_id = str(event.payload.get("task_id", ""))
        if not task_id:
            return
        self._state.upsert_task(
            task_id,
            str(event.payload.get("task_name", task_id)),
            "completed",
            progress=1.0,
        )

    async def _on_task_failed(self, event: Event) -> None:
        task_id = str(event.payload.get("task_id", ""))
        if not task_id:
            return
        status = str(event.payload.get("status", "failed"))
        error = event.payload.get("error")
        self._state.upsert_task(
            task_id,
            str(event.payload.get("task_name", task_id)),
            status,
            message=str(error) if error else None,
        )

    async def _on_tool_started(self, event: Event) -> None:
        tool = str(event.payload.get("tool", "tool"))
        task_id = f"tool:{tool}"
        self._state.upsert_task(task_id, f"Tool: {tool}", "running")

    async def _on_tool_finished(self, event: Event) -> None:
        tool = str(event.payload.get("tool", "tool"))
        task_id = f"tool:{tool}"
        success = bool(event.payload.get("success", True))
        self._state.upsert_task(
            task_id,
            f"Tool: {tool}",
            "completed" if success else "failed",
            progress=1.0,
            message=str(event.payload.get("error")) if not success and event.payload.get("error") else None,
        )

    async def _on_search_started(self, event: Event) -> None:
        self._state.set_status(search="searching")

    async def _on_search_finished(self, event: Event) -> None:
        self._state.set_status(search="online")

    async def _on_wake_word(self, event: Event) -> None:
        phrase = str(event.payload.get("phrase", "azmuth"))
        self._state.set_status(wake_word="triggered")
        self._state.add_system_message(f"Wake word detected: {phrase}")

    async def _on_voice_detected(self, event: Event) -> None:
        self._state.set_status(voice_input="captured")

    async def _on_auth_changed(self, event: Event) -> None:
        mode = str(event.payload.get("to_mode", "restricted"))
        user_id = event.payload.get("user_id")
        self._state.set_mode(mode, user_id=str(user_id) if user_id else None)

    async def _on_mode_changed(self, event: Event) -> None:
        mode = str(event.payload.get("mode", "restricted"))
        self._state.set_mode(mode)

    async def _on_system_error(self, event: Event) -> None:
        message = str(event.payload.get("message", "unknown error"))
        self._state.set_error(message)
        self._state.add_system_message(message, level=LogLevel.ERROR)