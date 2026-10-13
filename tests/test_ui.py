"""Headless tests for the UI subsystem.

Deliberately does not import :mod:`app.ui.app` — the tkinter shell is
tested by running it, not by importing it. Everything interesting in the
UI lives in state/reducer/bridge, and all of that is fully testable
without a display.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config.settings import Settings, load_settings
from app.core.events import Event, EventBus, EventName
from app.language import build_language_manager
from app.ui import (
    ConversationEntry,
    LogLevel,
    Speaker,
    StatusSnapshot,
    StubResponder,
    TaskSummary,
    UIController,
    UIReducer,
    UIState,
)
from app.voice import InputManager, UserInput, InputSource, Transcript


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path) -> Settings:
    return load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
        },
        create_directories=False,
    )


@pytest.fixture()
def language(settings: Settings):
    return build_language_manager(settings)


@pytest.fixture()
def state() -> UIState:
    return UIState()


def _run(coro):  # noqa: ANN001
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# ConversationEntry / TaskSummary / StatusSnapshot
# ---------------------------------------------------------------------------
def test_conversation_entry_as_dict_is_json_ready() -> None:
    import json
    entry = ConversationEntry(id=1, speaker=Speaker.USER, text="hi")
    payload = json.loads(json.dumps(entry.as_dict()))
    assert payload["speaker"] == "user"
    assert payload["text"] == "hi"


def test_task_summary_is_active() -> None:
    for status, expected in [
        ("pending", True),
        ("running", True),
        ("completed", False),
        ("failed", False),
        ("cancelled", False),
    ]:
        assert TaskSummary(id="t", name="t", status=status).is_active is expected


def test_status_snapshot_as_dict_shape() -> None:
    payload = StatusSnapshot().as_dict()
    assert set(payload) == {
        "llm", "memory", "voice_input", "tts", "wake_word",
        "search", "task_manager", "security", "ui",
    }


# ---------------------------------------------------------------------------
# UIState — transcript
# ---------------------------------------------------------------------------
def test_state_starts_empty(state: UIState) -> None:
    assert state.conversation == ()
    assert state.tasks == ()
    assert state.active_tasks == ()
    assert state.mode == "restricted"
    assert state.user_id is None
    assert state.last_error is None
    assert state.revision == 0


def test_add_user_message_appends_and_bumps_revision(state: UIState) -> None:
    before = state.revision
    entry = state.add_user_message("hello", language="en")
    assert entry.speaker is Speaker.USER
    assert entry.text == "hello"
    assert entry.language == "en"
    assert state.conversation[-1] is entry
    assert state.revision == before + 1


def test_add_assistant_message(state: UIState) -> None:
    entry = state.add_assistant_message("hi back", language="ta")
    assert entry.speaker is Speaker.AZMUTH
    assert entry.language == "ta"


def test_add_system_message_with_level(state: UIState) -> None:
    entry = state.add_system_message("boom", level=LogLevel.ERROR)
    assert entry.speaker is Speaker.SYSTEM
    assert entry.level is LogLevel.ERROR


def test_conversation_ids_increment(state: UIState) -> None:
    a = state.add_user_message("a")
    b = state.add_assistant_message("b")
    c = state.add_system_message("c")
    assert (a.id, b.id, c.id) == (1, 2, 3)


def test_clear_conversation(state: UIState) -> None:
    state.add_user_message("a")
    state.add_user_message("b")
    before = state.revision
    state.clear_conversation()
    assert state.conversation == ()
    assert state.revision > before


# ---------------------------------------------------------------------------
# UIState — subscription
# ---------------------------------------------------------------------------
def test_subscribe_receives_mutations(state: UIState) -> None:
    seen: list[int] = []
    unsubscribe = state.subscribe(lambda s: seen.append(s.revision))

    state.add_user_message("a")
    state.add_user_message("b")

    assert len(seen) == 2
    unsubscribe()
    state.add_user_message("c")
    assert len(seen) == 2  # no more notifications


def test_unsubscribe_is_idempotent(state: UIState) -> None:
    unsubscribe = state.subscribe(lambda _s: None)
    unsubscribe()
    unsubscribe()  # must not raise


def test_failing_listener_is_dropped(state: UIState) -> None:
    good_calls: list[int] = []

    def bad(_s: UIState) -> None:
        raise RuntimeError("boom")

    def good(s: UIState) -> None:
        good_calls.append(s.revision)

    state.subscribe(bad)
    state.subscribe(good)

    state.add_user_message("a")
    state.add_user_message("b")

    # Good listener called twice; bad listener was dropped after first failure.
    assert len(good_calls) == 2


# ---------------------------------------------------------------------------
# UIState — tasks
# ---------------------------------------------------------------------------
def test_upsert_task_creates_and_updates(state: UIState) -> None:
    state.upsert_task("t1", "indexer", "pending")
    t = state.tasks[0]
    assert t.status == "pending"
    assert t.is_active

    state.upsert_task("t1", "indexer", "running", progress=0.5)
    t2 = state.tasks[0]
    assert t2.status == "running"
    assert t2.progress == 0.5
    assert t2.created_at == t.created_at  # preserved


def test_active_tasks_filters_terminal(state: UIState) -> None:
    state.upsert_task("t1", "a", "running")
    state.upsert_task("t2", "b", "completed")
    state.upsert_task("t3", "c", "pending")
    assert {t.id for t in state.active_tasks} == {"t1", "t3"}


def test_remove_task(state: UIState) -> None:
    state.upsert_task("t1", "a", "completed")
    assert state.remove_task("t1") is True
    assert state.remove_task("t1") is False


def test_clear_finished_tasks(state: UIState) -> None:
    state.upsert_task("t1", "a", "running")
    state.upsert_task("t2", "b", "completed")
    state.upsert_task("t3", "c", "failed")
    removed = state.clear_finished_tasks()
    assert removed == 2
    assert {t.id for t in state.tasks} == {"t1"}


# ---------------------------------------------------------------------------
# UIState — mode / status / errors
# ---------------------------------------------------------------------------
def test_set_mode(state: UIState) -> None:
    state.set_mode("creator", user_id="creator")
    assert state.mode == "creator"
    assert state.user_id == "creator"


def test_set_mode_same_values_is_noop(state: UIState) -> None:
    state.set_mode("creator", user_id="creator")
    before = state.revision
    state.set_mode("creator", user_id="creator")
    assert state.revision == before


def test_set_status_updates_fields(state: UIState) -> None:
    state.set_status(llm="online", search="searching")
    assert state.status.llm == "online"
    assert state.status.search == "searching"
    assert state.status.memory == "unknown"  # unchanged


def test_set_status_ignores_unknown_keys() -> None:
    # ``replace`` will raise for unknown keys — that's the correct
    # behavior for a typed dataclass, and it is what we want: a typo is
    # caught rather than silently swallowed.
    with pytest.raises(TypeError):
        UIState().set_status(unknown_field="x")


def test_set_error(state: UIState) -> None:
    state.set_error("something failed")
    assert state.last_error == "something failed"
    state.set_error(None)
    assert state.last_error is None


# ---------------------------------------------------------------------------
# UIReducer — event handling
# ---------------------------------------------------------------------------
def _reduce(state: UIState, bus: EventBus, event: Event) -> None:
    UIReducer(state, bus)  # subscribes on construction
    _run(bus.publish(event))
    _run(asyncio.sleep(0))


def test_reducer_task_started(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    _run(bus.publish(Event(
        EventName.TASK_STARTED,
        {"task_id": "t1", "task_name": "indexer"},
    )))

    assert state.tasks[0].id == "t1"
    assert state.tasks[0].status == "running"
    reducer.detach()


def test_reducer_task_completed(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    _run(bus.publish(Event(
        EventName.TASK_COMPLETED,
        {"task_id": "t1", "task_name": "indexer"},
    )))

    assert state.tasks[0].status == "completed"
    reducer.detach()


def test_reducer_task_failed(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    _run(bus.publish(Event(
        EventName.TASK_FAILED,
        {"task_id": "t1", "task_name": "indexer",
         "status": "failed", "error": "something blew up"},
    )))

    t = state.tasks[0]
    assert t.status == "failed"
    assert t.message == "something blew up"
    reducer.detach()


def test_reducer_tool_lifecycle(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    _run(bus.publish(Event(
        EventName.TOOL_STARTED, {"tool": "echo"},
    )))
    _run(bus.publish(Event(
        EventName.TOOL_COMPLETED, {"tool": "echo", "success": True},
    )))

    assert state.tasks[0].status == "completed"
    reducer.detach()


def test_reducer_search_updates_status(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    _run(bus.publish(Event(EventName.SEARCH_STARTED)))
    assert state.status.search == "searching"
    _run(bus.publish(Event(EventName.SEARCH_COMPLETED)))
    assert state.status.search == "online"
    reducer.detach()


def test_reducer_wake_word_adds_system_message(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    _run(bus.publish(Event(
        EventName.WAKE_WORD_DETECTED, {"phrase": "azmuth"},
    )))

    assert any("Wake word" in e.text for e in state.conversation)
    reducer.detach()


def test_reducer_authentication_changed(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    _run(bus.publish(Event(
        EventName.AUTHENTICATION_CHANGED,
        {"to_mode": "creator", "user_id": "creator"},
    )))

    assert state.mode == "creator"
    assert state.user_id == "creator"
    reducer.detach()


def test_reducer_system_error(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    _run(bus.publish(Event(
        EventName.SYSTEM_ERROR, {"message": "network down"},
    )))

    assert state.last_error == "network down"
    assert any(e.level is LogLevel.ERROR for e in state.conversation)
    reducer.detach()


def test_reducer_detach_stops_handling(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)
    reducer.detach()

    _run(bus.publish(Event(
        EventName.SEARCH_STARTED,
    )))

    assert state.status.search == "unknown"


def test_reducer_handles_missing_payload_gracefully(state: UIState) -> None:
    bus = EventBus()
    reducer = UIReducer(state, bus)

    # Empty payload — must not raise, must not create a task.
    _run(bus.publish(Event(EventName.TASK_STARTED)))
    assert state.tasks == ()
    reducer.detach()


# ---------------------------------------------------------------------------
# StubResponder
# ---------------------------------------------------------------------------
def test_stub_responder_echoes() -> None:
    responder = StubResponder()
    user_input = UserInput(
        text="hello", language="en", source=InputSource.TEXT,
    )
    reply, session_id, model, provider = _run(
        responder(user_input, None, None)
    )
    assert reply == "[stub] hello"
    assert session_id == "stub-session"
    assert model == "stub"
    assert provider == "stub"


# ---------------------------------------------------------------------------
# UIController — text
# ---------------------------------------------------------------------------
def test_controller_send_text_records_both_messages(language) -> None:
    controller = UIController(language=language)
    reply = _run(controller.send_text("hello world"))

    assert reply == "[stub] hello world"

    conversation = controller.state.conversation
    assert len(conversation) == 2
    assert conversation[0].speaker is Speaker.USER
    assert conversation[0].text == "hello world"
    assert conversation[0].source == "text"
    assert conversation[1].speaker is Speaker.AZMUTH
    assert conversation[1].text == "[stub] hello world"


def test_controller_send_text_empty_returns_empty(language) -> None:
    controller = UIController(language=language)
    assert _run(controller.send_text("   ")) == ""
    assert controller.state.conversation == ()


def test_controller_send_text_while_busy_refuses(language) -> None:
    async def slow(_input, _session, _confirmer):  # noqa: ANN001
        await asyncio.sleep(0.05)
        return "done", "s1"

    controller = UIController(language=language, responder=slow)

    async def run() -> tuple[str, str]:
        first = asyncio.create_task(controller.send_text("first"))
        await asyncio.sleep(0)  # let first lock
        second = await controller.send_text("second")
        return await first, second

    first_reply, second_reply = _run(run())
    assert first_reply == "done"
    assert second_reply == ""
    # Second attempt produced a warning in the transcript.
    assert any(
        e.speaker is Speaker.SYSTEM and "previous request" in e.text.lower()
        for e in controller.state.conversation
    )


def test_controller_responder_failure_is_contained(language) -> None:
    async def broken(_input, _session, _confirmer):  # noqa: ANN001
        raise RuntimeError("model down")

    controller = UIController(language=language, responder=broken)
    reply = _run(controller.send_text("hello"))

    assert reply == ""
    assert controller.state.last_error is not None
    assert "model down" in controller.state.last_error
    assert any(
        e.level is LogLevel.ERROR for e in controller.state.conversation
    )


def test_controller_is_not_busy_after_failure(language) -> None:
    async def broken(_input, _session, _confirmer):  # noqa: ANN001
        raise RuntimeError("x")

    controller = UIController(language=language, responder=broken)
    _run(controller.send_text("a"))
    assert controller.is_busy is False

    async def ok(_input, _session, _confirmer):  # noqa: ANN001
        return "fine", "s1"
    controller._responder = ok
    assert _run(controller.send_text("b")) == "fine"


# ---------------------------------------------------------------------------
# UIController — voice
# ---------------------------------------------------------------------------
def test_controller_add_voice_input(language) -> None:
    controller = UIController(language=language)
    transcript = Transcript(text="open Chrome", confidence=0.92, provider="fake")
    user_input = InputManager(language).from_transcript(transcript)

    reply = _run(controller.add_voice_input(user_input))

    assert reply == "[stub] open Chrome"
    conversation = controller.state.conversation
    assert conversation[0].source == "voice"
    assert conversation[0].speaker is Speaker.USER
    assert conversation[1].source == "voice"
    assert conversation[1].speaker is Speaker.AZMUTH


def test_controller_voice_and_text_share_responder(language) -> None:
    seen: list[str] = []

    async def responder(user_input, _session, _confirmer):  # noqa: ANN001
        seen.append(user_input.source.value)
        return f"reply to {user_input.text}", "s1"

    controller = UIController(language=language, responder=responder)
    _run(controller.send_text("typed"))
    transcript = Transcript(text="spoken", confidence=0.9, provider="fake")
    _run(controller.add_voice_input(InputManager(language).from_transcript(transcript)))

    assert seen == ["text", "voice"]


# ---------------------------------------------------------------------------
# UIController — misc
# ---------------------------------------------------------------------------
def test_controller_clear_conversation(language) -> None:
    controller = UIController(language=language)
    _run(controller.send_text("hello"))
    controller.clear_conversation()
    assert controller.state.conversation == ()


def test_controller_clear_finished_tasks(language) -> None:
    controller = UIController(language=language)
    controller.state.upsert_task("t1", "a", "completed")
    controller.state.upsert_task("t2", "b", "running")
    removed = controller.clear_finished_tasks()
    assert removed == 1


def test_controller_refresh_status(language) -> None:
    controller = UIController(language=language)
    controller.refresh_status(StatusSnapshot(llm="online", memory="online"))
    assert controller.state.status.llm == "online"
    assert controller.state.status.memory == "online"


def test_controller_detach_is_idempotent(language) -> None:
    controller = UIController(language=language)
    controller.detach()
    controller.detach()  # must not raise


def test_controller_wires_reducer_to_bus(language) -> None:
    bus = EventBus()
    controller = UIController(language=language, event_bus=bus)

    _run(bus.publish(Event(
        EventName.AUTHENTICATION_CHANGED,
        {"to_mode": "creator", "user_id": "creator"},
    )))

    assert controller.state.mode == "creator"


# ---------------------------------------------------------------------------
# The app shell is not imported — assert that
# ---------------------------------------------------------------------------
def test_ui_package_does_not_import_tkinter_at_top_level() -> None:
    """Importing ``app.ui`` must not pull tkinter in.

    Run in a fresh subprocess because a full-suite run has already
    imported tkinter transitively (uiautomation, pyautogui), and no
    amount of clearing ``sys.modules`` can isolate that. The subprocess
    starts clean, imports ``app.ui``, and asserts tkinter did not come
    along for the ride.
    """
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import app.ui\n"
        "assert 'tkinter' not in sys.modules, ("
        "'importing app.ui pulled tkinter into sys.modules'\n"
        ")\n"
        "assert hasattr(app.ui, 'UIController')\n"
        "assert hasattr(app.ui, 'UIState')\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout