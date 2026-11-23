"""Reproduces the 'Azmuth not responding' silent-turn bug.

With the missing `response = await provider.chat(request)` line, the
first candidate raises NameError, is caught as a generic provider
failure, the second raises NameError, and the pipeline falls back to
`self._llm.chat()`. This test asserts the router is actually used for
a normal turn.
"""

from __future__ import annotations
from app.voice.input_manager import InputSource
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brain.pipeline import Brain
from app.llm.messages import ChatResponse


def _make_brain(router_response: ChatResponse):
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=router_response)

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=router_response)
    provider.name = "stub"
    provider.model = "stub-model"

    spec = MagicMock()
    spec.name = "stub"

    router = MagicMock()
    router.registry.by_purpose.return_value = [spec]
    router.for_model.return_value = provider
    router.record_success = MagicMock()
    router.record_failure = MagicMock()
    router.warmup = AsyncMock()

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

    tools = MagicMock()
    tools.list_metadata = MagicMock(return_value=())

    brain = Brain(
        llm=llm,
        memory=memory,
        sessions=sessions,
        language=language,
        tools=tools,
        search=MagicMock(),
        event_bus=None,
        llm_router=router,
        planner=MagicMock(),
    )
    return brain, provider, router, llm


@pytest.mark.asyncio
async def test_router_is_used_not_fallback():
    """If the router's provider.chat succeeds, _llm.chat (fallback) must
    NOT be called. With the NameError bug, the provider is treated as
    failing and the fallback IS called."""
    from app.voice.input_manager import UserInput
    from app.voice.input_manager import InputSource

    resp = ChatResponse(
        content="hello back", tool_calls=(), model="stub-model",
        provider="stub",
    )
    brain, provider, router, llm = _make_brain(resp)

    ui = UserInput(text="hi", language="en", source=InputSource.TEXT)
        
    result = await brain.respond(ui)

    assert result.text == "hello back"
    provider.chat.assert_awaited_once()
    llm.chat.assert_not_awaited()
    router.record_success.assert_called_once()
    router.record_failure.assert_not_called()