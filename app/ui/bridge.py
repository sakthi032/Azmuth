
"""The boundary between the UI and the backend."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.brain.pipeline import ConfirmationRequest, Confirmer
from app.config.logging_setup import get_logger
from app.core.events import EventBus
from app.language.manager import LanguageManager
from app.ui.reducer import UIReducer
from app.ui.state import LogLevel, StatusSnapshot, UIState
from app.voice.input_manager import InputManager, UserInput

logger = get_logger("ui.bridge")


#: A responder takes a UserInput, the current conversation id, and an
#: optional confirmer. It returns either ``(reply_text, session_id)``
#: or ``(reply_text, session_id, model, provider)``. Both are accepted;
#: the extended form is what Azmuth.respond_as_text produces.
Responder = Callable[
    [UserInput, "str | None", "Confirmer | None"],
    Awaitable["tuple[Any, ...]"],
]


def _unpack_result(result: tuple[Any, ...]) -> tuple[str, str, str, str]:
    """Return (reply, session_id, model, provider) from a responder result.

    Accepts a 2-tuple (legacy) or a 4-tuple (with model info). Anything
    else is a contract violation and raises ValueError with a clear
    message — the bridge is not the place to guess.
    """
    if len(result) == 2:
        reply, session_id = result
        return reply or "", session_id or "", "", ""
    if len(result) == 4:
        reply, session_id, model, provider = result
        return (
            reply or "",
            session_id or "",
            str(model or ""),
            str(provider or ""),
        )
    raise ValueError(
        f"responder must return 2 or 4 values, got {len(result)}"
    )


class StubResponder:
    """Development placeholder that echoes the user's input."""

    async def __call__(
        self,
        user_input: UserInput,
        conversation_id: str | None = None,
        confirmer: Confirmer | None = None,
    ) -> tuple[str, str, str, str]:
        del confirmer
        text = f"[stub] {user_input.text}"
        return text, conversation_id or "stub-session", "stub", "stub"


class UIController:
    """The API the tkinter shell uses."""

    def __init__(
        self,
        *,
        language: LanguageManager,
        event_bus: EventBus | None = None,
        responder: Responder | None = None,
        confirmer: Confirmer | None = None,
        state: UIState | None = None,
    ) -> None:
        self._state = state or UIState()
        self._language = language
        self._inputs = InputManager(language)
        self._responder: Responder = responder or StubResponder()
        self._confirmer: Confirmer | None = confirmer
        self._reducer = UIReducer(self._state, event_bus)
        self._busy = False
        self._conversation_id: str | None = None

    # -- introspection -----------------------------------------------------
    @property
    def state(self) -> UIState:
        return self._state

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def conversation_id(self) -> str | None:
        return self._conversation_id

    def reset_conversation(self) -> None:
        self._conversation_id = None

    def set_confirmer(self, confirmer: Confirmer | None) -> None:
        self._confirmer = confirmer

    def subscribe(self, listener) -> Callable[[], None]:  # noqa: ANN001
        return self._state.subscribe(listener)

    def detach(self) -> None:
        self._reducer.detach()

    # -- user actions ------------------------------------------------------
    async def send_text(
        self, text: str, *, session_language: str | None = None
    ) -> str:
        text = text.strip()
        if not text:
            return ""
        if self._busy:
            self._state.add_system_message(
                "Still processing the previous request.",
                level=LogLevel.WARNING,
            )
            return ""

        self._busy = True
        try:
            user_input = self._inputs.from_text(
                text, session_language=session_language
            )
            self._state.add_user_message(
                user_input.text,
                language=user_input.language,
                source="text",
            )

            try:
                raw = await self._responder(
                    user_input, self._conversation_id, self._confirmer
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "responder failed",
                    extra={"event": "UI_RESPONDER_FAILED"},
                )
                self._state.set_error(str(exc))
                self._state.add_system_message(
                    f"Error: {exc}", level=LogLevel.ERROR
                )
                return ""

            reply, session_id, model, provider = _unpack_result(raw)

            if session_id:
                self._conversation_id = session_id
            if not reply:
                return ""
            self._state.add_assistant_message(
                reply,
                language=user_input.language,
                source="text",
                model=model,
                provider=provider,
            )
            return reply
        finally:
            self._busy = False

    async def add_voice_input(self, user_input: UserInput) -> str:
        if self._busy:
            return ""
        self._busy = True
        try:
            self._state.add_user_message(
                user_input.text,
                language=user_input.language,
                source="voice",
            )
            try:
                raw = await self._responder(
                    user_input, self._conversation_id, self._confirmer
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "responder failed (voice)",
                    extra={"event": "UI_RESPONDER_FAILED"},
                )
                self._state.set_error(str(exc))
                return ""

            reply, session_id, model, provider = _unpack_result(raw)

            if session_id:
                self._conversation_id = session_id
            if reply:
                self._state.add_assistant_message(
                    reply,
                    language=user_input.language,
                    source="voice",
                    model=model,
                    provider=provider,
                )
            return reply
        finally:
            self._busy = False

    def cancel_task(self, task_id: str) -> None:
        self._state.add_system_message(f"Cancel requested for task {task_id}")

    def clear_conversation(self) -> None:
        self._state.clear_conversation()

    def clear_finished_tasks(self) -> int:
        return self._state.clear_finished_tasks()

    def refresh_status(self, status: StatusSnapshot) -> None:
        self._state.set_status(**status.as_dict())
