"""Real tkinter UI integration tests for Stop button / turn lifecycle.

Exercises the actual AzmuthDesktopApp, TranscriptView, InputRow, UIController,
and UIState (with Tk's root.after dispatcher and event loop pumped live).
"""

from __future__ import annotations

import asyncio
import logging
import time
import tkinter as tk
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ui.app import AzmuthDesktopApp
from app.ui.bridge import UIController
from app.ui.state import UIState


def _language_manager() -> Any:
    lm = MagicMock()
    lm.resolve_response_language = lambda text, session_language=None: "en"
    return lm


class _LogCaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _pump_until(app: AzmuthDesktopApp, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app._root.update_idletasks()
        app._root.update()
        if predicate():
            return True
        time.sleep(0.015)
    app._root.update_idletasks()
    app._root.update()
    return bool(predicate())


def _widget_text(app: AzmuthDesktopApp) -> str:
    return app._transcript._text.get("1.0", "end")


def _make_app(responder):
    state = UIState()
    controller = UIController(
        language=_language_manager(),
        responder=responder,
        state=state,
    )
    app = AzmuthDesktopApp(controller, title="Azmuth Stop Test")
    # Pump initial geometry/state messages
    _pump_until(app, lambda: "Azmuth is running" in _widget_text(app), timeout=1.0)
    return app, controller, state


def test_real_tk_stop_during_live_streaming_keeps_partial_footer_and_one_stopped():
    """Slow streaming stub -> Stop button -> partial text kept + footer + 1 Stopped + controls reset < 2s."""
    first_chunks_sent = asyncio.Event()

    async def slow_streaming_responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        chunks = [f"word{i} " for i in range(60)]
        for idx, chunk in enumerate(chunks):
            if on_delta is not None:
                await on_delta(chunk)
            if idx == 4:
                first_chunks_sent.set()
            await asyncio.sleep(0.08)
        return "".join(chunks), "sess-1", "gemini-test", "gemini", ""

    from app.ui import app as ui_app_mod, bridge as ui_bridge_mod

    handler = _LogCaptureHandler()
    ui_app_log = ui_app_mod.logger
    ui_bridge_log = ui_bridge_mod.logger
    prev_app_level = ui_app_log.level
    prev_bridge_level = ui_bridge_log.level
    ui_app_log.setLevel(logging.INFO)
    ui_bridge_log.setLevel(logging.INFO)
    ui_app_log.addHandler(handler)
    ui_bridge_log.addHandler(handler)

    try:
        app, controller, state = _make_app(slow_streaming_responder)
    except tk.TclError as exc:
        pytest.skip(f"tkinter display unavailable: {exc}")
        return

    try:
        app._input_row.set_text("write 500 word story")
        app._input_row._send_btn.invoke()

        assert _pump_until(
            app,
            lambda: (
                "word4 " in _widget_text(app)
                and str(app._input_row._stop_btn.cget("state")) == "normal"
            ),
            timeout=2.0,
        ), f"Timed out waiting for streaming start; widget text={_widget_text(app)!r}"

        # Trigger the real Stop button command handler
        t0 = time.monotonic()
        app._input_row._stop_btn.invoke()

        assert _pump_until(
            app,
            lambda: (
                not controller.is_busy
                and str(app._input_row._send_btn.cget("state")) == "normal"
                and str(app._input_row._entry.cget("state")) == "normal"
                and str(app._input_row._stop_btn.cget("state")) == "disabled"
                and state.streaming_entry_id is None
                and "Stopped" in _widget_text(app)
            ),
            timeout=2.0,
        ), f"UI did not reset after Stop; widget text={_widget_text(app)!r}"

        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"Stop took {elapsed:.2f}s"

        rendered = _widget_text(app)
        # Exactly one Stopped marker in both state and rendered Tk widget
        assert sum(1 for e in state.conversation if "Stopped" in e.text) == 1
        assert rendered.count("Stopped") == 1, f"Rendered text had unexpected Stopped count:\n{rendered}"
        # Partial text kept
        assert "word0" in rendered
        assert "word4" in rendered
        assert "word55" not in rendered
        # Finalized with meta footer (IST timestamp & source)
        assert "IST" in rendered
        assert "text" in rendered
        # Spinner removed
        assert not app._transcript.is_thinking
        assert "Thinking" not in rendered

        # Verify structured log events with turn_id
        events = [getattr(r, "event", None) for r in handler.records]
        assert "STOP_PRESSED" in events
        assert "TURN_CANCEL_REQUESTED" in events
        assert "TURN_CANCELLED" in events
        assert "UI_TURN_FINALIZED" in events
        turn_ids = {
            getattr(r, "turn_id", None)
            for r in handler.records
            if getattr(r, "event", None) in ("TURN_CANCEL_REQUESTED", "TURN_CANCELLED", "UI_TURN_FINALIZED")
        }
        assert len(turn_ids) == 1 and None not in turn_ids

        # Input is usable for a subsequent turn
        app._input_row.set_text("hello again")
        assert app._input_row._entry.get() == "hello again"
    finally:
        ui_app_log.removeHandler(handler)
        ui_bridge_log.removeHandler(handler)
        ui_app_log.setLevel(prev_app_level)
        ui_bridge_log.setLevel(prev_bridge_level)
        app._on_close()


def test_real_tk_stop_during_typewriter_streaming():
    """Stop button stays enabled during TranscriptView typewriter streaming and stops mid-story with footer."""
    async def non_streaming_story_responder(
        user_input, conversation_id, confirmer
    ):
        words = " ".join(f"storyword{i}" for i in range(180))
        return words, "sess-tw", "gemini-tw", "gemini", ""

    try:
        app, controller, state = _make_app(non_streaming_story_responder)
    except tk.TclError as exc:
        pytest.skip(f"tkinter display unavailable: {exc}")
        return

    try:
        app._input_row.set_text("write 500 word story")
        app._input_row._send_btn.invoke()

        assert _pump_until(
            app,
            lambda: (
                app._transcript.is_streaming
                and "storyword5" in _widget_text(app)
                and str(app._input_row._stop_btn.cget("state")) == "normal"
            ),
            timeout=2.0,
        ), f"Typewriter streaming did not start with Stop enabled; state={app._input_row._stop_btn.cget('state')}"

        t0 = time.monotonic()
        app._input_row._stop_btn.invoke()

        assert _pump_until(
            app,
            lambda: (
                not app._transcript.is_streaming
                and str(app._input_row._send_btn.cget("state")) == "normal"
                and str(app._input_row._entry.cget("state")) == "normal"
                and "Stopped" in _widget_text(app)
            ),
            timeout=2.0,
        )
        assert time.monotonic() - t0 < 2.0

        rendered = _widget_text(app)
        assert rendered.count("Stopped") == 1
        assert "storyword0" in rendered
        assert "storyword179" not in rendered
        assert "gemini-tw" in rendered
        assert "IST" in rendered
        assert not app._transcript.is_thinking
    finally:
        app._on_close()


@pytest.mark.parametrize(
    "scenario,prompt_text",
    [
        ("waiting_first_token", "tell me a story"),
        ("during_tool_call", "open notepad and type hi"),
        ("during_web_search", "search the web for latest news"),
    ],
)
def test_real_tk_stop_before_first_token_removes_spinner(scenario: str, prompt_text: str):
    """Stop during wait-for-first-token, tool call, or web search removes spinner & resets UI < 2s."""
    async def blocking_responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        await asyncio.sleep(30.0)
        return "never", "s", "m", "p", ""

    try:
        app, controller, state = _make_app(blocking_responder)
    except tk.TclError as exc:
        pytest.skip(f"tkinter display unavailable: {exc}")
        return

    try:
        app._input_row.set_text(prompt_text)
        app._input_row._send_btn.invoke()

        assert _pump_until(
            app,
            lambda: (
                app._transcript.is_thinking
                and "Thinking" in _widget_text(app)
                and str(app._input_row._stop_btn.cget("state")) == "normal"
            ),
            timeout=2.0,
        ), f"Spinner never appeared for {scenario}"

        t0 = time.monotonic()
        app._input_row._stop_btn.invoke()

        assert _pump_until(
            app,
            lambda: (
                not controller.is_busy
                and not app._transcript.is_thinking
                and "Thinking" not in _widget_text(app)
                and "Stopped" in _widget_text(app)
                and str(app._input_row._send_btn.cget("state")) == "normal"
                and str(app._input_row._entry.cget("state")) == "normal"
            ),
            timeout=2.0,
        ), f"UI failed to clear spinner and reset for {scenario}; text={_widget_text(app)!r}"

        assert time.monotonic() - t0 < 2.0
        assert _widget_text(app).count("Stopped") == 1
        assert sum(1 for e in state.conversation if "Stopped" in e.text) == 1
    finally:
        app._on_close()
