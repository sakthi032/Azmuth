"""B5 (retry cap) and B6 (injection defense for run_python)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brain.pipeline import Brain
from app.core.events import EventBus
from app.llm.messages import ChatResponse
from app.voice.input_manager import InputSource, UserInput


def _tool_call(name: str, args: dict):
    tc = MagicMock()
    tc.id = f"call_{name}"
    tc.name = name
    tc.arguments = args
    return tc


def _make_brain(responses: list, tool_results: list):
    """Build a Brain with a scripted LLM and scripted tool results.

    ``responses`` — list of ChatResponse objects; consumed in order.
    ``tool_results`` — list of (success, output, error) tuples; consumed
    in order of invoke calls.
    """
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=responses)

    tools = MagicMock()
    tools.list_metadata = MagicMock(return_value=())
    tools.metadata = MagicMock(return_value=None)
    tools.requires_confirmation = MagicMock(return_value=False)

    results_iter = iter(tool_results)
    async def invoke_side_effect(*a, **k):
        success, output, error = next(results_iter)
        r = MagicMock()
        r.success = success
        r.output = output
        r.error = error
        return r
    tools.invoke = AsyncMock(side_effect=invoke_side_effect)

    memory = MagicMock()
    memory.recall_hybrid = MagicMock(return_value=[])
    memory.recall = MagicMock(return_value=[])

    sessions = MagicMock()
    session = MagicMock()
    session.id = "s1"
    session.language = "en"
    session.add_user_message = MagicMock()
    session.add_assistant_message = MagicMock()
    sessions.get_or_create = MagicMock(return_value=session)

    language = MagicMock()
    language.resolve_response_language = MagicMock(return_value="en")

    return Brain(
        llm=llm, memory=memory, sessions=sessions, language=language,
        tools=tools, search=MagicMock(), event_bus=EventBus(),
        llm_router=None, planner=MagicMock(),
    )


# ── B5: retry cap ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_python_retry_cap_enforced():
    """After 3 run_python failures, the pipeline refuses the 4th call
    and forces the model to report the real error."""
    fail = (False, {"success": False, "stderr": "syntax error"},
            "syntax error")
    tc1 = _tool_call("run_python", {"code": "x=1+"})
    tc2 = _tool_call("run_python", {"code": "x=1+ "})
    tc3 = _tool_call("run_python", {"code": "x=1+  "})
    tc4 = _tool_call("run_python", {"code": "x=1+   "})

    responses = [
        ChatResponse(content="", tool_calls=(tc1,), model="m", provider="p"),
        ChatResponse(content="", tool_calls=(tc2,), model="m", provider="p"),
        ChatResponse(content="", tool_calls=(tc3,), model="m", provider="p"),
        ChatResponse(content="", tool_calls=(tc4,), model="m", provider="p"),
        ChatResponse(content="I can't fix this after 3 tries. Error: syntax error.",
                     tool_calls=(), model="m", provider="p"),
    ]
    results = [fail, fail, fail]  # only 3 invokes should happen

    brain = _make_brain(responses, results)
    ui = UserInput(text="run this python", language="en",
                   source=InputSource.TEXT)
    resp = await brain.respond(ui)

    # 4th call refused, not invoked
    assert brain._tools.invoke.await_count == 3, (
        f"expected 3 invokes (retry cap), got {brain._tools.invoke.await_count}"
    )
    # Final response must mention the error or the retry limit
    assert "syntax error" in resp.text.lower() or "retry" in resp.text.lower(), (
        f"expected model to surface the error, got: {resp.text!r}"
    )


# ── B6: injection defense ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_python_refused_when_turn_tainted_and_no_code_intent():
    """If a tool result flags injection_risk=true AND the user didn't
    ask for code, refuse run_python."""
    tc_read = _tool_call("ask_document",
                         {"path": "evil.pdf", "question": "what does it say"})
    tc_run = _tool_call("run_python",
                        {"code": "print('pwned by injection')"})

    responses = [
        ChatResponse(content="", tool_calls=(tc_read,), model="m", provider="p"),
        ChatResponse(content="", tool_calls=(tc_run,), model="m", provider="p"),
        ChatResponse(content="The document contained suspicious instructions. "
                             "I did not run them.",
                     tool_calls=(), model="m", provider="p"),
    ]
    results = [
        (True, {"injection_risk": True, "blocks": []}, None),
        # run_python should NOT be invoked
    ]

    brain = _make_brain(responses, results)
    ui = UserInput(text="what does this pdf say", language="en",
                   source=InputSource.TEXT)
    resp = await brain.respond(ui)

    # Only ask_document invoked — run_python refused
    assert brain._tools.invoke.await_count == 1, (
        f"run_python should have been refused; "
        f"invoke count was {brain._tools.invoke.await_count}"
    )


@pytest.mark.asyncio
async def test_run_python_allowed_when_user_explicitly_asks():
    """Even on a tainted turn, explicit user code intent allows run_python."""
    tc_read = _tool_call("ask_document",
                         {"path": "data.csv", "question": "what columns"})
    tc_run = _tool_call("run_python",
                        {"code": "import pandas as pd; print(pd.read_csv('data.csv').head())"})

    responses = [
        ChatResponse(content="", tool_calls=(tc_read,), model="m", provider="p"),
        ChatResponse(content="", tool_calls=(tc_run,), model="m", provider="p"),
        ChatResponse(content="Here are the columns.", tool_calls=(),
                     model="m", provider="p"),
    ]
    results = [
        (True, {"injection_risk": True, "blocks": []}, None),
        (True, {"success": True, "stdout": "a b c", "files": []}, None),
    ]

    # User explicitly says "analyze this csv" — code intent present
    brain = _make_brain(responses, results)
    ui = UserInput(text="analyze this csv and show me the columns",
                   language="en", source=InputSource.TEXT)
    await brain.respond(ui)

    # Both tools invoked
    assert brain._tools.invoke.await_count == 2


@pytest.mark.asyncio
async def test_run_python_allowed_on_clean_turn():
    """If no injection risk was flagged, run_python works normally."""
    tc_run = _tool_call("run_python", {"code": "print(2+2)"})

    responses = [
        ChatResponse(content="", tool_calls=(tc_run,), model="m", provider="p"),
        ChatResponse(content="4", tool_calls=(), model="m", provider="p"),
    ]
    results = [
        (True, {"success": True, "stdout": "4", "files": []}, None),
    ]

    brain = _make_brain(responses, results)
    ui = UserInput(text="what is 2+2", language="en", source=InputSource.TEXT)
    await brain.respond(ui)

    assert brain._tools.invoke.await_count == 1