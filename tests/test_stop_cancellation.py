"""Stop button cancels in-flight turns cleanly."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.ui.bridge import UIController
from app.ui.state import UIState


@pytest.mark.asyncio
async def test_stop_cancels_in_flight_turn():
    """Clicking Stop cancels send_text, adds Stopped marker, frees _current_task."""
    state = UIState()
    lm = MagicMock()
    lm.resolve_response_language = lambda text, session_language=None: "en"

    started = asyncio.Event()

    async def slow_responder(user_input, conversation_id, confirmer):
        started.set()
        try:
            await asyncio.sleep(10.0)
            return "never delivered", "s", "m", "p", ""
        except asyncio.CancelledError:
            raise

    controller = UIController(
        language=lm, responder=slow_responder, state=state,
    )

    task = asyncio.create_task(controller.send_text("hello there"))
    await asyncio.wait_for(started.wait(), timeout=2.0)
    controller.cancel_in_flight()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert controller._current_task is None
    assert controller.is_busy is False      
    # The "Stopped" message is now owned by the UI handler
    # (_handle_stop in app/ui/app.py). The bridge intentionally does
    # not add one — see test_stop_button.py::test_no_duplicate_stopped_marker.

def test_reset_conversation_clears_id():
    """reset_conversation is the primitive logout uses to isolate principals."""
    state = UIState()
    lm = MagicMock()
    controller = UIController(language=lm, state=state)
    controller._conversation_id = "session-of-previous-principal"
    controller.reset_conversation()
    assert controller._conversation_id is None