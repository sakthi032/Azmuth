"""Reminder delivery — fan-out subscriber for EventName.REMINDER_DUE.

Three channels, each best-effort and independent:
  * chat  — posts via UIState.add_system_message (⏰ prefix)
  * toast — Windows toast notification (winotify, then win11toast)
  * voice — enqueues into the shared SpeechQueue if it exists

Install once after the event bus and UI state are available.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.core.events import Event, EventBus, EventName

logger = logging.getLogger(__name__)


class ReminderNotifier:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        ui_state: Any,
        speech_queue_getter: Callable[[], Any] | None = None,
        toast_factory: Callable[[str], Callable[[], None] | None] | None = None,
        channels: tuple[str, ...] = ("chat", "toast", "voice"),
    ) -> None:
        self._bus = event_bus
        self._ui_state = ui_state
        self._speech_queue_getter = speech_queue_getter
        self._toast_factory = toast_factory or _default_toast_factory
        self._channels = frozenset(channels)
        self._unsub: Callable[[], None] | None = None

    def attach(self) -> None:
        self._unsub = self._bus.subscribe(EventName.REMINDER_DUE, self._on_due)
        logger.info(
            "reminder notifier attached",
            extra={
                "event": "REMINDER_NOTIFIER_READY",
                "channels": sorted(self._channels),
            },
        )

    def detach(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _on_due(self, event: Event) -> None:
        payload = dict(event.payload or {})
        message = str(payload.get("message") or payload.get("prompt") or "").strip()
        if not message:
            logger.warning("REMINDER_DUE with empty message; skipping")
            return

        if "chat" in self._channels:
            self._deliver_chat(message)
        if "toast" in self._channels:
            self._deliver_toast(message)
        if "voice" in self._channels:
            await self._deliver_voice(message)

    def _deliver_chat(self, message: str) -> None:
        try:
            self._ui_state.add_system_message(f"\u23f0 Reminder: {message}")
        except Exception:
            logger.exception("reminder chat delivery failed")

    def _deliver_toast(self, message: str) -> None:
        try:
            show = self._toast_factory(message)
            if show is not None:
                show()
        except Exception:
            logger.warning("reminder toast delivery failed", exc_info=True)

    async def _deliver_voice(self, message: str) -> None:
        if self._speech_queue_getter is None:
            return
        try:
            queue = self._speech_queue_getter()
        except Exception:
            logger.warning("speech queue getter failed", exc_info=True)
            return
        if queue is None:
            return
        try:
            await queue.enqueue(f"Reminder: {message}")
        except Exception:
            logger.warning("reminder voice delivery failed", exc_info=True)


def _default_toast_factory(message: str) -> Callable[[], None] | None:
    """Return a callable that shows a Windows toast, or None if no backend."""
    try:
        from winotify import Notification, audio  # type: ignore[import-untyped]

        n = Notification(
            app_id="Azmuth",
            title="\u23f0 Reminder",
            msg=message,
            duration="short",
        )
        try:
            n.set_audio(audio.Default, loop=False)
        except Exception:
            pass
        return n.show
    except ImportError:
        pass

    try:
        from win11toast import toast  # type: ignore[import-untyped]

        return lambda: toast("\u23f0 Reminder", message)
    except ImportError:
        logger.info("no Windows toast backend available (winotify/win11toast)")
        return None


def install_reminder_notifier(
    *,
    event_bus: EventBus,
    ui_state: Any,
    speech_queue_getter: Callable[[], Any] | None = None,
    channels: tuple[str, ...] = ("chat", "toast", "voice"),
) -> ReminderNotifier:
    notifier = ReminderNotifier(
        event_bus=event_bus,
        ui_state=ui_state,
        speech_queue_getter=speech_queue_getter,
        channels=channels,
    )
    notifier.attach()
    return notifier


__all__ = ["ReminderNotifier", "install_reminder_notifier"]