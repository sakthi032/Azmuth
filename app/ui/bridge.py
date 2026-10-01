
"""The boundary between the UI and the backend.

:class:`UIController` is the only thing the tkinter shell talks to. It
owns:

* a :class:`UIState` (shared with the widgets),
* a :class:`UIReducer` subscribed to the event bus,
* an *responder* - the callable that produces a reply to a user turn,
* the current conversation id, threaded through every turn so short-term
  memory persists across the whole session.

In Phase 1 the responder defaults to :class:`StubResponder`, clearly
labelled as a development placeholder. STEP 17 supplies the real
responder (the brain's pipeline). No UI file changes when that happens.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.config.logging_setup import get_logger
from app.core.events import EventBus
from app.language.manager import LanguageManager
from app.ui.reducer import UIReducer
from app.ui.state import LogLevel, StatusSnapshot, UIState
from app.voice.input_manager import InputManager, UserInput

logger = get_logger("ui.bridge")


#: A responder takes a UserInput plus the current conversation id (which
#: is ``None`` on the first turn) and returns ``(reply_text, session_id)``.
#: The session id is threaded back so short-term memory persists across
#: turns. :meth:`Azmuth.respond_as_text` already matches this signature.
Responder = Callable[
    [UserInput, "str | None"],
    Awaitable["tuple[str, str]"],
]


class StubResponder:
    """Development placeholder that echoes the user's input.

    **Not an LLM.** Produces ``"[stub] <user text>"`` so the UI can be
    exercised end-to-end before the brain is configured. STEP 17 supplies
    the real responder; nothing in the UI changes.
    """

    async def __call__(
        self, user_input: UserInput, conversation_id: str | None = None
    ) -> tuple[str, str]:
        text = f"[stub] {user_input.text}"
        session_id = conversation_id or "stub-session"
        return text, session_id


class UIController:
    """The API the tkinter shell uses. No widget imports here."""

    def __init__(
        self,
        *,
        language: LanguageManager,
        event_bus: EventBus | None = None,
        responder: Responder | None = None,
        state: UIState | None = None,
    ) -> None:
        self._state = state or UIState()
        self._language = language
        self._inputs = InputManager(language)
        self._responder: Responder = responder or StubResponder()
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
        """The current session id, or ``None`` before the first turn."""
        return self._conversation_id

    def reset_conversation(self) -> None:
        """Start a fresh session on the next turn. Does not clear history."""
        self._conversation_id = None

    def subscribe(self, listener) -> Callable[[], None]:  # noqa: ANN001
        return self._state.subscribe(listener)

    def detach(self) -> None:
        """Stop the reducer and drop subscriptions. Idempotent."""
        self._reducer.detach()

    # -- user actions ------------------------------------------------------
    async def send_text(
        self, text: str, *, session_language: str | None = None
    ) -> str:
        """Process a typed message. Returns the assistant reply text.

        The reply is also recorded in :attr:`UIState.conversation` - the
        return value is for callers that want to react programmatically.
        """
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
                reply, session_id = await self._responder(
                    user_input, self._conversation_id
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

            if session_id:
                self._conversation_id = session_id
            if not reply:
                return ""
            self._state.add_assistant_message(
                reply, language=user_input.language, source="text"
            )
            return reply
        finally:
            self._busy = False

    async def add_voice_input(self, user_input: UserInput) -> str:
        """Record a voice input and produce a reply.

        Voice and text converge here - this is the same code path as
        :meth:`send_text`, only the input source differs.
        """
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
                reply, session_id = await self._responder(
                    user_input, self._conversation_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "responder failed (voice)",
                    extra={"event": "UI_RESPONDER_FAILED"},
                )
                self._state.set_error(str(exc))
                return ""
            if session_id:
                self._conversation_id = session_id
            if reply:
                self._state.add_assistant_message(
                    reply, language=user_input.language, source="voice"
                )
            return reply
        finally:
            self._busy = False

    def cancel_task(self, task_id: str) -> None:
        """UI-level cancel request. STEP 17 wires this to the TaskManager."""
        self._state.add_system_message(f"Cancel requested for task {task_id}")

    def clear_conversation(self) -> None:
        self._state.clear_conversation()

    def clear_finished_tasks(self) -> int:
        return self._state.clear_finished_tasks()

    def refresh_status(self, status: StatusSnapshot) -> None:
        self._state.set_status(**status.as_dict())