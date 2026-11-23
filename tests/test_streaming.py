"""Real-time token streaming through the UI bridge."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ui.bridge import UIController
from app.ui.state import Speaker, UIState


def _language_manager() -> Any:
    lm = MagicMock()
    lm.resolve_response_language = lambda text, session_language=None: "en"
    return lm


@pytest.mark.asyncio
async def test_ui_receives_five_deltas_in_order_and_final_text():
    """A responder that emits 5 deltas must deliver them, in order, and
    leave the transcript entry with the joined text."""
    state = UIState()
    chunks = ["Hello", " ", "wor", "ld", "!"]

    async def fake_responder(user_input, conversation_id, confirmer,
                             on_delta=None):
        if on_delta is not None:
            for chunk in chunks:
                await on_delta(chunk)
        return "Hello world!", "sess-1", "stub", "stub", ""

    controller = UIController(
        language=_language_manager(),
        responder=fake_responder,
        state=state,
    )

    seen: list[str] = []

    async def ui_on_delta(delta: str) -> None:
        seen.append(delta)

    reply = await controller.send_text("hi", on_delta=ui_on_delta)

    # 5 deltas, in order
    assert seen == chunks
    # Final stored assistant message equals joined text
    azmuth_entries = [
        e for e in state.conversation if e.speaker == Speaker.AZMUTH
    ]
    assert len(azmuth_entries) == 1
    assert azmuth_entries[0].text == "Hello world!"
    # No streaming entry remains active
    assert state.streaming_entry_id is None
    # Reply passed through unchanged
    assert reply == "Hello world!"


@pytest.mark.asyncio
async def test_responder_without_on_delta_kwarg_falls_back():
    """A legacy 3-arg responder works when on_delta is provided (TypeError
    fallback in UIController)."""
    state = UIState()

    async def legacy_responder(user_input, conversation_id, confirmer):
        return "legacy reply", "sess-x", "stub", "stub", ""

    controller = UIController(
        language=_language_manager(),
        responder=legacy_responder,
        state=state,
    )

    async def ui_on_delta(_delta: str) -> None:
        pass

    reply = await controller.send_text("hi", on_delta=ui_on_delta)
    assert reply == "legacy reply"
    assert state.streaming_entry_id is None