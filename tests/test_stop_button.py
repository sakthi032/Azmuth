"""Reproduces and pins the Stop button scenarios from the spec.

Each test asserts the turn terminates under 2 seconds. All four must
pass for the Stop feature to be considered fixed.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

from app.ui.bridge import UIController
from app.ui.state import UIState


def _make_controller(responder) -> UIController:
    lm = MagicMock()
    lm.resolve_response_language = lambda t, session_language=None: "en"
    return UIController(language=lm, responder=responder, state=UIState())


@pytest.mark.asyncio
async def test_stop_while_streaming_under_2s():
    async def streaming_responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        for _ in range(1000):
            if on_delta is not None:
                await on_delta("x")
            await asyncio.sleep(0.05)
        return "never", "s", "m", "p", ""

    controller = _make_controller(streaming_responder)
    task = asyncio.create_task(controller.send_text("hi"))
    await asyncio.sleep(0.2)
    t0 = time.monotonic()
    controller.cancel_in_flight()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"took {elapsed:.2f}s"
    assert controller.state.streaming_entry_id is None


@pytest.mark.asyncio
async def test_stop_while_waiting_for_first_token_under_2s():
    async def never_yields_responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        await asyncio.sleep(60.0)
        return "never", "s", "m", "p", ""

    controller = _make_controller(never_yields_responder)
    task = asyncio.create_task(controller.send_text("hi"))
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    controller.cancel_in_flight()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_stop_while_sync_chat_blocked_under_2s():
    """Reproduces the sync-blocked provider case.

    The worker thread keeps running, but the caller (send_text) returns
    in <2s. This is the case the spec calls "run sync calls in a thread
    and stop waiting on it."
    """
    sync_started = threading.Event()
    sync_done = threading.Event()

    def blocking_sync():
        sync_started.set()
        sync_done.wait(timeout=10.0)
        return "late"

    async def sync_responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        return await asyncio.to_thread(blocking_sync)

    controller = _make_controller(sync_responder)
    task = asyncio.create_task(controller.send_text("hi"))

    # Yield to the loop in small steps so the task actually starts
    # and reaches to_thread. Do NOT block the loop with .wait() here.
    for _ in range(40):
        if sync_started.is_set():
            break
        await asyncio.sleep(0.05)
    assert sync_started.is_set(), "worker thread never started"

    t0 = time.monotonic()
    controller.cancel_in_flight()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.CancelledError:
        pass
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"took {elapsed:.2f}s"

    # The worker thread is still blocked — release it so pytest exits
    sync_done.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_stop_during_tool_call_under_2s():
    async def tool_blocked_responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        # Simulates a tool round: sleep until cancelled
        await asyncio.sleep(60.0)
        return "", "s", "m", "p", ""

    controller = _make_controller(tool_blocked_responder)
    task = asyncio.create_task(controller.send_text("hi"))
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    controller.cancel_in_flight()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)
    assert time.monotonic() - t0 < 2.0


@pytest.mark.asyncio
async def test_no_duplicate_stopped_marker():
    """cancel_in_flight increments turn_id; the late CancelledError
    handler must not add a second 'Stopped' message."""
    async def slow_responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        await asyncio.sleep(60.0)
        return "", "s", "m", "p", ""

    controller = _make_controller(slow_responder)
    task = asyncio.create_task(controller.send_text("hi"))
    await asyncio.sleep(0.1)
    controller.cancel_in_flight()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    texts = [e.text for e in controller.state.conversation]
    stopped_count = sum(1 for t in texts if "Stopped" in t)
    # Zero is acceptable here — the *UI handler* adds it, not the bridge
    assert stopped_count <= 1, f"found {stopped_count} Stopped markers"

@pytest.mark.asyncio
async def test_stop_during_web_search_under_2s():
    """Bug 10 explicit web-search scenario — Stop during a search_web
    tool round must terminate the turn in <2 s."""
    async def search_round_responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        # Simulates the pipeline blocking on a search_web tool round
        await asyncio.sleep(60.0)
        return "never", "s", "m", "p", ""

    controller = _make_controller(search_round_responder)
    task = asyncio.create_task(
        controller.send_text("search the web for gold price news")
    )
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    controller.cancel_in_flight()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)
    assert time.monotonic() - t0 < 2.0
