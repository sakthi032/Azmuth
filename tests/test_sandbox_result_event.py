"""Round 2: SANDBOX_RESULT event routing and inline image plumbing."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.events import Event, EventBus, EventName
from app.brain.pipeline import Brain


def test_sandbox_result_event_exists():
    assert hasattr(EventName, "SANDBOX_RESULT")
    assert EventName.SANDBOX_RESULT.value == "SANDBOX_RESULT"


@pytest.mark.asyncio
async def test_run_python_result_publishes_event():
    """When run_python returns files, the pipeline emits SANDBOX_RESULT."""
    png_b64 = base64.b64encode(b"\x89PNG fake").decode()
    tool_output = {
        "success": True,
        "stdout": "done",
        "stderr": "",
        "timed_out": False,
        "files": [
            {"name": "chart.png", "kind": "image",
             "size": 8, "data_b64": png_b64},
        ],
    }
    from app.llm.messages import ChatResponse
    from app.voice.input_manager import UserInput, InputSource

    # Chat response with one native tool call
    tc = MagicMock()
    tc.id = "call_1"
    tc.name = "run_python"
    tc.arguments = {"code": "print('done')"}
    first = ChatResponse(
        content="", tool_calls=(tc,),
        model="stub", provider="stub",
    )
    second = ChatResponse(
        content="Here is your chart.",
        tool_calls=(), model="stub", provider="stub",
    )

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=[first, second])

    captured_events: list[Event] = []
    bus = EventBus()
    async def capture(ev):
        captured_events.append(ev)
    bus.subscribe(EventName.SANDBOX_RESULT, capture)

    tools = MagicMock()
    tools.list_metadata = MagicMock(return_value=())
    tools.metadata = MagicMock(return_value=None)
    tools.requires_confirmation = MagicMock(return_value=False)
    tools.invoke = AsyncMock(return_value=MagicMock(
        success=True, output=tool_output, error=None,
    ))

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

    brain = Brain(
        llm=llm, memory=memory, sessions=sessions, language=language,
        tools=tools, search=MagicMock(), event_bus=bus, llm_router=None,
        planner=MagicMock(),
    )

    ui = UserInput(text="make a chart", language="en",
                   source=InputSource.TEXT)
    await brain.respond(ui)

    kinds = [e.name for e in captured_events]
    assert EventName.SANDBOX_RESULT in kinds, (
        f"expected SANDBOX_RESULT among {kinds}"
    )
    ev = next(e for e in captured_events if e.name is EventName.SANDBOX_RESULT)
    files = ev.payload.get("files") or []
    assert any(f.get("name") == "chart.png" for f in files)
    assert any(f.get("data_b64") == png_b64 for f in files)