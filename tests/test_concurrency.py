"""Concurrency integration tests.

Exercises the properties the spec asks for:

* a background task runs without blocking the conversation,
* the voice subsystem and text input can both be active while work runs,
* the task manager's events flow to the UI state while the pipeline is
  busy,
* one task's failure does not affect another's completion.

Everything runs against the real TaskManager, real UIState, real event
bus, and the real Azmuth pipeline with a scripted LLM. Only the LLM and
search provider are faked.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.events import EventBus, EventName
from app.tasks import TaskManager, TaskStatus
from app.ui import UIController, UIReducer, UIState
from app.voice import InputManager


def _run(coro):  # noqa: ANN001
    return asyncio.run(coro)


async def _wait_terminal(mgr: TaskManager, task_id: str, timeout: float = 2.0) -> Any:
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        t = mgr.get(task_id)
        if t is not None and t.is_terminal:
            return t
        await asyncio.sleep(0)
    raise TimeoutError(f"task {task_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Task manager + pipeline at the same time
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.concurrency
def test_background_task_does_not_block_conversation(
    make_azmuth,
) -> None:
    azmuth = make_azmuth(responses=["first reply", "second reply"])
    inputs = InputManager(azmuth.language)

    async def scenario() -> tuple[str, TaskStatus]:
        # Start a slow task.
        slow = azmuth.tasks.submit(
            lambda _t: asyncio.sleep(0.2),
            name="slow",
        )
        await asyncio.sleep(0)

        # While it runs, ask Azmuth two questions.
        r1 = await azmuth.respond(inputs.from_text("hello"))
        r2 = await azmuth.respond(inputs.from_text("again"))

        assert r1.text == "first reply"
        assert r2.text == "second reply"

        # The task is still running.
        assert slow.status in (TaskStatus.PENDING, TaskStatus.RUNNING)

        # Then it finishes.
        finished = await _wait_terminal(azmuth.tasks, slow.id)
        return r2.text, finished.status

    text, final_status = _run(scenario())
    assert text == "second reply"
    assert final_status is TaskStatus.COMPLETED


@pytest.mark.integration
@pytest.mark.concurrency
def test_two_tasks_run_concurrently_and_independently(
    make_azmuth,
) -> None:
    azmuth = make_azmuth(responses="ignored")

    async def scenario() -> tuple[Any, Any]:
        async def slow(task):
            await asyncio.sleep(0.05)
            return "slow done"

        async def fail(_task):
            await asyncio.sleep(0.02)
            raise RuntimeError("boom")

        a = azmuth.tasks.submit(slow, name="a")
        b = azmuth.tasks.submit(fail, name="b")

        await _wait_terminal(azmuth.tasks, a.id)
        await _wait_terminal(azmuth.tasks, b.id)
        return azmuth.tasks.get(a.id), azmuth.tasks.get(b.id)

    a_final, b_final = _run(scenario())
    assert a_final.status is TaskStatus.COMPLETED
    assert a_final.result == "slow done"
    assert b_final.status is TaskStatus.FAILED
    assert "boom" in (b_final.error or "")


@pytest.mark.integration
@pytest.mark.concurrency
def test_cancelling_one_task_leaves_others_running(
    make_azmuth,
) -> None:
    azmuth = make_azmuth(responses="ignored")

    async def scenario() -> tuple[Any, Any]:
        async def long(task):
            await asyncio.sleep(1.0)
            return "done"

        a = azmuth.tasks.submit(long, name="a")
        b = azmuth.tasks.submit(long, name="b")

        await asyncio.sleep(0.05)
        assert azmuth.tasks.cancel(a.id) is True

        await _wait_terminal(azmuth.tasks, a.id)
        await _wait_terminal(azmuth.tasks, b.id)
        return azmuth.tasks.get(a.id), azmuth.tasks.get(b.id)

    a_final, b_final = _run(scenario())
    assert a_final.status is TaskStatus.CANCELLED
    assert b_final.status is TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Task events flow to UI state while the pipeline is busy
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.concurrency
def test_task_events_update_ui_while_pipeline_runs(
    make_azmuth,
) -> None:
    azmuth = make_azmuth(responses="answer")

    state = UIState()
    reducer = UIReducer(state, azmuth.event_bus)
    inputs = InputManager(azmuth.language)

    async def scenario() -> None:
        async def slow(task):
            await asyncio.sleep(0.05)
            return "done"

        t = azmuth.tasks.submit(slow, name="indexer")
        await asyncio.sleep(0)

        # The reducer has already seen TASK_STARTED.
        assert any(task.id == t.id for task in state.tasks)
        assert state.tasks[0].status == "running"

        # Now ask the pipeline something — it should complete.
        await azmuth.respond(inputs.from_text("hello"))

        # Wait for the task to finish and the reducer to see it.
        await _wait_terminal(azmuth.tasks, t.id)
        await asyncio.sleep(0)

        assert state.tasks[0].status == "completed"

    _run(scenario())
    reducer.detach()


# ---------------------------------------------------------------------------
# Voice + text + tasks
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.concurrency
def test_voice_and_text_inputs_share_the_pipeline_under_load(
    make_azmuth,
) -> None:
    from app.voice import Transcript

    azmuth = make_azmuth(responses=["text reply", "voice reply"])
    inputs = InputManager(azmuth.language)

    async def scenario() -> tuple[str, str, TaskStatus]:
        # Kick off a slow background task first.
        t = azmuth.tasks.submit(
            lambda _x: asyncio.sleep(0.1), name="background"
        )
        await asyncio.sleep(0)

        # Text turn.
        r1 = await azmuth.respond(inputs.from_text("typed message"))

        # Voice turn — this is the seam the spec cares about.
        transcript = Transcript(
            text="spoken message", confidence=0.9, provider="fake"
        )
        voice_input = inputs.from_transcript(transcript)
        r2 = await azmuth.respond(voice_input)

        # Wait for the background task.
        finished = await _wait_terminal(azmuth.tasks, t.id)
        return r1.text, r2.text, finished.status

    text_reply, voice_reply, task_status = _run(scenario())
    assert text_reply == "text reply"
    assert voice_reply == "voice reply"
    assert task_status is TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# UI controller handles concurrent calls
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.concurrency
def test_ui_refuses_concurrent_sends_without_corrupting_state(
    make_azmuth,
) -> None:
    azmuth = make_azmuth(responses="reply")

    async def scenario() -> tuple[str, str]:
        # A responder that pauses, forcing the controller into busy state.
        async def slow(user_input, session_id):
            await asyncio.sleep(0.05)
            return f"reply to {user_input.text}", session_id or "s1"

        from app.ui import UIController
        controller = UIController(
            language=azmuth.language,
            responder=slow,
        )

        first = asyncio.create_task(controller.send_text("first"))
        await asyncio.sleep(0)  # let first lock

        second = await controller.send_text("second")  # must be refused
        first_reply = await first

        return first_reply, second

    first_reply, second_reply = _run(scenario())
    assert first_reply == "reply to first"
    assert second_reply == ""  # refused, not corrupted


# ---------------------------------------------------------------------------
# Event emission does not deadlock the pipeline
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.concurrency
def test_events_during_tool_call_do_not_block_the_turn(
    make_azmuth,
) -> None:
    tool_call = (
        '```tool\n{"name": "echo", "arguments": {"message": "x"}}\n```'
    )
    azmuth = make_azmuth(responses=[tool_call, "final"])
    inputs = InputManager(azmuth.language)

    seen: list[EventName] = []

    def record(event):  # noqa: ANN001
        seen.append(event.name)

    for name in (EventName.TOOL_STARTED, EventName.TOOL_COMPLETED):
        azmuth.event_bus.subscribe(name, record)

    async def scenario() -> str:
        response = await azmuth.respond(inputs.from_text("echo x"))
        await asyncio.sleep(0)
        return response.text

    text = _run(scenario())
    assert text == "final"
    # Both tool events fired during the turn.
    assert EventName.TOOL_STARTED in seen
    assert EventName.TOOL_COMPLETED in seen


# `Any` import for the helper above.
from typing import Any  # noqa: E402