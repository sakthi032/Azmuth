"""End-to-end tests for the wired Azmuth object.

Uses fakes for the LLM and search provider so the tests are fast and
deterministic. Everything else — memory, sessions, language, tools,
permissions, plugin loading, TTS, voice — is the real subsystem.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.brain import (
    Azmuth,
    Brain,
    BrainResponse,
    ToolCall,
    build_azmuth,
    build_system_prompt,
    parse_tool_calls,
)
from app.config.settings import Settings, load_settings
from app.core.events import Event, EventBus, EventName
from app.llm.base import LLMProvider
from app.llm.messages import (
    ChatRequest,
    ChatResponse,
    Message,
    ModelInfo,
    Role,
    StreamChunk,
)
from app.search.base import SearchProvider
from app.search.models import SearchQuery, SearchResult
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.tools import EchoTool, GetTimeTool, ToolManager
from app.voice import InputManager


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class ScriptedLLM(LLMProvider):
    """An LLM that returns a fixed list of responses in order."""

    name = "scripted"

    def __init__(self, responses: list[str] | str) -> None:
        super().__init__(model="scripted")
        self._script = [responses] if isinstance(responses, str) else list(responses)
        self.calls: list[ChatRequest] = []

    def is_available(self) -> bool:
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        text = self._script.pop(0) if self._script else ""
        return ChatResponse(
            content=text, model="scripted", provider="scripted",
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        response = await self.chat(request)
        yield StreamChunk(delta=response.content, finish_reason="stop")

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(provider="scripted", model="scripted")


class FixedSearch(SearchProvider):
    name = "fixed"

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self._results = list(results or [])
        self.queries: list[SearchQuery] = []

    def is_available(self) -> bool:
        return True

    async def search(self, query: SearchQuery) -> list[SearchResult]:
        self.queries.append(query)
        return list(self._results)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
        },
        create_directories=True,
    )


@pytest.fixture()
def creator() -> Principal:
    return Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("pin",),
    )


def _run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def _make_azmuth(
    settings: Settings,
    *,
    responses: list[str] | str,
    search_results: list[SearchResult] | None = None,
    principal: Principal | None = None,
) -> tuple[Azmuth, ScriptedLLM, FixedSearch]:
    llm = ScriptedLLM(responses)
    search_provider = FixedSearch(search_results)
    from app.search.manager import SearchManager
    from app.core.registry import Registry
    registry: Registry[SearchProvider] = Registry("search")
    registry.register("fixed", lambda: search_provider)
    search = SearchManager("fixed", registry)

    azmuth = build_azmuth(
        settings,
        llm_provider=llm,
        search=search,
    )
    return azmuth, llm, search_provider


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
def test_system_prompt_mentions_reference_blocks_and_tools() -> None:
    prompt = build_system_prompt(language="en", tools=())
    assert "reference" in prompt.lower()
    assert "not instructions" in prompt.lower() or "untrusted" in prompt.lower()
    assert "azmuth" in prompt.lower()
    assert "English" in prompt


def test_system_prompt_lists_tools() -> None:
    tm = ToolManager(PermissionManager())
    tm.register(EchoTool())
    tools = tm.list_metadata()
    prompt = build_system_prompt(language="en", tools=tools)
    assert "echo" in prompt
    assert "message" in prompt


def test_system_prompt_marks_confirmation_tools() -> None:
    from app.tools.base import RiskLevel, Tool
    from app.security.capabilities import Capability
    from typing import ClassVar

    class DangerousTool(Tool):
        name: ClassVar[str] = "danger"
        description: ClassVar[str] = "dangerous"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability: ClassVar[Capability] = Capability.RUN_SYSTEM_COMMAND
        risk_level: ClassVar[RiskLevel] = RiskLevel.CRITICAL
        requires_confirmation: ClassVar[bool] = True

        async def execute(self) -> None:
            return None

    tm = ToolManager(PermissionManager())
    tm.register(DangerousTool())
    prompt = build_system_prompt(language="en", tools=tm.list_metadata())
    assert "REQUIRES CONFIRMATION" in prompt


def test_system_prompt_tamil_language_name() -> None:
    prompt = build_system_prompt(language="ta", tools=())
    assert "Tamil" in prompt


# ---------------------------------------------------------------------------
# Tool-call parsing
# ---------------------------------------------------------------------------
def test_parse_tool_calls_empty_text() -> None:
    calls, cleaned = parse_tool_calls("")
    assert calls == () and cleaned == ""


def test_parse_tool_calls_single_block() -> None:
    text = 'Sure!\n\n```tool\n{"name": "echo", "arguments": {"message": "hi"}}\n```\n'
    calls, cleaned = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "echo"
    assert calls[0].arguments == {"message": "hi"}
    assert cleaned == "Sure!"


def test_parse_tool_calls_no_arguments() -> None:
    text = '```tool\n{"name": "get_time"}\n```'
    calls, _ = parse_tool_calls(text)
    assert calls == (ToolCall(name="get_time", arguments={}),)


def test_parse_tool_calls_multiple_blocks() -> None:
    text = (
        "Working on it:\n"
        "```tool\n{\"name\": \"a\", \"arguments\": {}}\n```\n"
        "and\n"
        "```tool\n{\"name\": \"b\", \"arguments\": {\"x\": 1}}\n```\n"
    )
    calls, cleaned = parse_tool_calls(text)
    assert [c.name for c in calls] == ["a", "b"]
    assert "Working on it" in cleaned
    assert "and" in cleaned
    assert "```tool" not in cleaned


def test_parse_tool_calls_array_payload() -> None:
    text = '```tool\n[{"name": "a"}, {"name": "b", "arguments": {"x": 1}}]\n```'
    calls, _ = parse_tool_calls(text)
    assert [c.name for c in calls] == ["a", "b"]


def test_parse_tool_calls_malformed_json_is_skipped() -> None:
    text = "```tool\nnot json\n```\n\n```tool\n{\"name\": \"good\"}\n```"
    calls, _ = parse_tool_calls(text)
    assert [c.name for c in calls] == ["good"]


def test_parse_tool_calls_missing_name_is_skipped() -> None:
    text = '```tool\n{"arguments": {}}\n```'
    calls, _ = parse_tool_calls(text)
    assert calls == ()


def test_parse_tool_calls_returns_remaining_text_trimmed() -> None:
    text = "   \n```tool\n{\"name\": \"x\"}\n```\n   "
    _, cleaned = parse_tool_calls(text)
    assert cleaned == ""


# ---------------------------------------------------------------------------
# Simple text turn
# ---------------------------------------------------------------------------
def test_text_turn_returns_llm_text(settings: Settings) -> None:
    azmuth, llm, _ = _make_azmuth(settings, responses="Hello from the brain.")
    inputs = InputManager(azmuth.language)

    response = _run(azmuth.respond(inputs.from_text("hi")))

    assert isinstance(response, BrainResponse)
    assert response.text == "Hello from the brain."
    assert response.language == "en"
    assert response.stopped_reason == "final"
    assert len(llm.calls) == 1
    assert response.session_id  # assigned


def test_session_persists_across_turns(settings: Settings) -> None:
    azmuth, llm, _ = _make_azmuth(
        settings, responses=["first reply", "second reply"]
    )
    inputs = InputManager(azmuth.language)

    first = _run(azmuth.respond(inputs.from_text("hello")))
    second = _run(
        azmuth.respond(
            inputs.from_text("again"), conversation_id=first.session_id
        )
    )

    assert second.session_id == first.session_id
    # Second LLM call saw at least: user "hello", assistant "first reply",
    # user "again".
    contents = [m.content for m in llm.calls[1].messages]
    assert "hello" in contents
    assert "first reply" in contents
    assert "again" in contents


def test_session_language_preserved_across_turns(settings: Settings) -> None:
    azmuth, _, _ = _make_azmuth(
        settings, responses=["pதில்", "sari"]
    )
    inputs = InputManager(azmuth.language)

    first = _run(azmuth.respond(inputs.from_text("இன்று வானிலை என்ன?")))
    assert first.language == "ta"

    # Next turn is Tanglish; the session language (Tamil) should stick.
    second = _run(
        azmuth.respond(
            inputs.from_text("Chrome open pannu"),
            conversation_id=first.session_id,
        )
    )
    assert second.language == "ta"


# ---------------------------------------------------------------------------
# Tool call turn
# ---------------------------------------------------------------------------
def test_tool_call_is_executed_and_result_is_returned(settings: Settings) -> None:
    tool_call = (
        'Calling echo.\n'
        '```tool\n'
        '{"name": "echo", "arguments": {"message": "hi there"}}\n'
        '```'
    )
    final = "The tool said: hi there"
    azmuth, llm, _ = _make_azmuth(settings, responses=[tool_call, final])
    inputs = InputManager(azmuth.language)

    response = _run(azmuth.respond(inputs.from_text("please echo")))

    assert response.text == final
    assert len(response.tool_calls) == 1
    record = response.tool_calls[0]
    assert record.name == "echo"
    assert record.success is True
    assert record.output == "hi there"
    assert response.iterations == 2
    assert response.stopped_reason == "final"

    # Second call's messages include the tool result.
    second_call_messages = llm.calls[1].messages
    tool_result_msg = next(
        m for m in second_call_messages
        if "[TOOL_RESULT name=echo" in m.content
    )
    assert "hi there" in tool_result_msg.content
    assert tool_result_msg.role is Role.SYSTEM


def test_multiple_tool_calls_in_one_message(settings: Settings) -> None:
    tool_calls = (
        '```tool\n{"name": "echo", "arguments": {"message": "a"}}\n```\n'
        '```tool\n{"name": "get_time", "arguments": {}}\n```'
    )
    azmuth, _, _ = _make_azmuth(settings, responses=[tool_calls, "done"])
    inputs = InputManager(azmuth.language)

    response = _run(azmuth.respond(inputs.from_text("do two things")))
    assert [r.name for r in response.tool_calls] == ["echo", "get_time"]
    assert all(r.success for r in response.tool_calls)


def test_tool_call_refused_for_restricted_principal(settings: Settings) -> None:
    """Restricted principal asks for a creator-only tool; pipeline contains the error."""
    from app.security.capabilities import Capability
    from app.tools.base import RiskLevel, Tool
    from typing import ClassVar

    class DeleteTool(Tool):
        name: ClassVar[str] = "delete_file"
        description: ClassVar[str] = "delete a file"
        parameters: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }
        capability: ClassVar[Capability] = Capability.DELETE_FILE
        risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
        requires_confirmation: ClassVar[bool] = True

        async def execute(self, *, path: str) -> str:
            return f"deleted {path}"

    tool_call = '```tool\n{"name": "delete_file", "arguments": {"path": "/x"}}\n```'
    llm = ScriptedLLM([tool_call, "I could not delete that."])

    # Build a custom tool manager that includes the delete tool.
    perms = PermissionManager()
    tools = ToolManager(perms)
    tools.register(EchoTool())
    tools.register(DeleteTool())

    from app.search.manager import SearchManager
    from app.core.registry import Registry
    from app.search.base import SearchProvider
    from app.search.providers.null import NullSearchProvider

    search_registry: Registry[SearchProvider] = Registry("search")
    search_registry.register("null", NullSearchProvider)
    search = SearchManager("null", search_registry)

    azmuth = build_azmuth(
        settings,
        llm_provider=llm,
        tools=tools,
        search=search,
    )
    inputs = InputManager(azmuth.language)

    # Anonymous restricted principal.
    response = _run(
        azmuth.respond(
            inputs.from_text("please delete x"),
            principal=Principal.anonymous(),
        )
    )

    assert len(response.tool_calls) == 1
    record = response.tool_calls[0]
    assert record.name == "delete_file"
    assert record.success is False
    assert record.error and "requires creator" in record.error.lower()


def test_tool_call_requires_confirmation_for_creator(settings: Settings) -> None:
    """Even a creator gets confirmation required for destructive tools."""
    from app.security.capabilities import Capability
    from app.tools.base import RiskLevel, Tool
    from typing import ClassVar

    class DeleteTool(Tool):
        name: ClassVar[str] = "delete_file"
        description: ClassVar[str] = "delete a file"
        parameters: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }
        capability: ClassVar[Capability] = Capability.DELETE_FILE
        risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
        requires_confirmation: ClassVar[bool] = True

        async def execute(self, *, path: str) -> str:
            return f"deleted {path}"

    tool_call = '```tool\n{"name": "delete_file", "arguments": {"path": "/x"}}\n```'
    llm = ScriptedLLM([tool_call, "Confirmation is needed."])

    perms = PermissionManager()
    tools = ToolManager(perms)
    tools.register(DeleteTool())

    from app.search.manager import SearchManager
    from app.core.registry import Registry
    from app.search.base import SearchProvider
    from app.search.providers.null import NullSearchProvider
    search_registry: Registry[SearchProvider] = Registry("search")
    search_registry.register("null", NullSearchProvider)
    search = SearchManager("null", search_registry)

    azmuth = build_azmuth(
        settings, llm_provider=llm, tools=tools, search=search,
    )
    inputs = InputManager(azmuth.language)

    creator = Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("pin",),
    )
    response = _run(
        azmuth.respond(inputs.from_text("delete x"), principal=creator)
    )

    assert response.tool_calls[0].success is False
    assert "confirmation" in (response.tool_calls[0].error or "").lower()


def test_tool_loop_bounded_by_max_iterations(settings: Settings) -> None:
    # LLM always emits a tool call — loop must terminate.
    always_tool = '```tool\n{"name": "echo", "arguments": {"message": "x"}}\n```'
    llm = ScriptedLLM([always_tool] * 20)

    perms = PermissionManager()
    tools = ToolManager(perms)
    tools.register(EchoTool())

    from app.search.manager import SearchManager
    from app.core.registry import Registry
    from app.search.base import SearchProvider
    from app.search.providers.null import NullSearchProvider
    search_registry: Registry[SearchProvider] = Registry("search")
    search_registry.register("null", NullSearchProvider)
    search = SearchManager("null", search_registry)

    azmuth = build_azmuth(
        settings, llm_provider=llm, tools=tools, search=search,
    )
    inputs = InputManager(azmuth.language)

    response = _run(azmuth.respond(inputs.from_text("loop forever")))
    assert response.stopped_reason == "max_iterations"
    assert response.iterations >= 1


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def test_search_triggered_by_live_query(settings: Settings) -> None:
    from app.search.models import SearchResult

    results = [
        SearchResult(
            title="Weather today",
            url="https://example.com/weather",
            snippet="It will be sunny.",
            source="fixed",
        )
    ]
    azmuth, llm, provider = _make_azmuth(
        settings,
        responses="Here is the weather: sunny.",
        search_results=results,
    )
    inputs = InputManager(azmuth.language)

    response = _run(azmuth.respond(inputs.from_text("What's the weather today?")))

    assert response.used_search is True
    assert len(provider.queries) == 1
    # The reference block must be present in the LLM's context.
    messages = llm.calls[0].messages
    reference_message = next(
        (m for m in messages if m.content.startswith("[reference]")),
        None,
    )
    assert reference_message is not None
    assert reference_message.role is Role.SYSTEM
    assert "It will be sunny" in reference_message.content


def test_search_not_triggered_for_general_question(settings: Settings) -> None:
    azmuth, _, provider = _make_azmuth(
        settings, responses="The capital is Paris."
    )
    inputs = InputManager(azmuth.language)

    response = _run(azmuth.respond(inputs.from_text("What is the capital of France?")))

    assert response.used_search is False
    assert provider.queries == []


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
def test_memory_recall_scoped_to_authenticated_principal(
    settings: Settings, creator: Principal
) -> None:
    azmuth, llm, _ = _make_azmuth(settings, responses="ok")

    # Store a memory under "creator".
    azmuth.memory.store(
        "Creator prefers dark mode",
        user_id="creator",
        kind=__import__("app.memory", fromlist=["MemoryKind"]).MemoryKind.PREFERENCE,
    )

    inputs = InputManager(azmuth.language)

    # Anonymous → no recall.
    anon_response = _run(azmuth.respond(inputs.from_text("dark mode")))
    assert anon_response.used_memory is False

    # Creator → recall happens.
    creator_response = _run(
        azmuth.respond(inputs.from_text("dark mode"), principal=creator)
    )
    assert creator_response.used_memory is True
    # The recalled memory appears in the reference block.
    messages = llm.calls[-1].messages
    reference = next((m for m in messages if "[MEMORY]" in m.content), None)
    assert reference is not None
    assert "dark mode" in reference.content


# ---------------------------------------------------------------------------
# Voice → pipeline
# ---------------------------------------------------------------------------
def test_voice_input_flows_through_same_pipeline(settings: Settings) -> None:
    from app.voice import Transcript

    azmuth, llm, _ = _make_azmuth(settings, responses="Done.")
    inputs = InputManager(azmuth.language)

    transcript = Transcript(
        text="open Chrome", confidence=0.9, provider="fake"
    )
    user_input = inputs.from_transcript(transcript)

    assert user_input.source.value == "voice"
    response = _run(azmuth.respond(user_input))
    assert response.text == "Done."
    # Same pipeline — the LLM saw the voice text.
    contents = [m.content for m in llm.calls[0].messages]
    assert "open Chrome" in contents


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def test_status_reports_subsystems(settings: Settings) -> None:
    azmuth, _, _ = _make_azmuth(settings, responses="ok")
    status = azmuth.status()
    snapshot = status.as_dict()

    assert snapshot["llm"] == "online"        # ScriptedLLM always available
    assert snapshot["memory"] == "online"
    assert snapshot["task_manager"] == "online"
    assert snapshot["security"] == "online"
    assert snapshot["ui"] == "online"
    # Default providers are null → offline.
    assert snapshot["tts"] == "offline"
    assert snapshot["wake_word"] == "offline"
    assert snapshot["voice_input"] == "offline"
    # Null search is technically "available" (returns empty).
    assert snapshot["search"] == "online"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def test_startup_is_idempotent(settings: Settings) -> None:
    azmuth, _, _ = _make_azmuth(settings, responses="ok")
    _run(azmuth.startup())
    _run(azmuth.startup())  # must not raise
    _run(azmuth.shutdown())
    _run(azmuth.shutdown())  # must not raise


def test_events_emitted_on_turn(settings: Settings) -> None:
    azmuth, _, _ = _make_azmuth(settings, responses="ok")
    inputs = InputManager(azmuth.language)

    seen: list[Event] = []
    azmuth.event_bus.subscribe(EventName.USER_INPUT, seen.append)

    _run(azmuth.respond(inputs.from_text("hello")))
    _run(asyncio.sleep(0))

    assert any(e.name is EventName.USER_INPUT for e in seen)


# ---------------------------------------------------------------------------
# UI round-trip
# ---------------------------------------------------------------------------
def test_ui_controller_with_real_brain(settings: Settings) -> None:
    from app.ui import UIController

    azmuth, _, _ = _make_azmuth(settings, responses="Hello from Azmuth.")
    controller = UIController(
        language=azmuth.language,
        responder=azmuth.respond_as_text,
    )

    reply = _run(controller.send_text("hello"))
    assert reply == "Hello from Azmuth."

    conversation = controller.state.conversation
    assert len(conversation) == 2
    assert conversation[0].text == "hello"
    assert conversation[1].text == "Hello from Azmuth."


def test_ui_voice_input_with_real_brain(settings: Settings) -> None:
    from app.ui import UIController
    from app.voice import Transcript

    azmuth, _, _ = _make_azmuth(settings, responses="Opened.")
    controller = UIController(
        language=azmuth.language,
        responder=azmuth.respond_as_text,
    )

    transcript = Transcript(text="open Chrome", confidence=0.9, provider="fake")
    user_input = InputManager(azmuth.language).from_transcript(transcript)

    reply = _run(controller.add_voice_input(user_input))
    assert reply == "Opened."
    # The UI records the source correctly.
    assert controller.state.conversation[0].source == "voice"


# ---------------------------------------------------------------------------
# Plugin integration
# ---------------------------------------------------------------------------
def test_builtin_plugin_tools_are_registered(settings: Settings) -> None:
    azmuth, _, _ = _make_azmuth(settings, responses="ok")
    assert "uppercase" in azmuth.tools.names()
    assert "echo" in azmuth.tools.names()
    assert "get_time" in azmuth.tools.names()


def test_builtin_plugin_tool_invocable_via_pipeline(settings: Settings) -> None:
    call = '```tool\n{"name": "uppercase", "arguments": {"text": "hi"}}\n```'
    azmuth, _, _ = _make_azmuth(settings, responses=[call, "HI"])
    inputs = InputManager(azmuth.language)

    response = _run(azmuth.respond(inputs.from_text("uppercase hi")))
    assert response.tool_calls[0].output == "HI"
    
    


def test_ui_controller_preserves_session_across_turns(settings: Settings) -> None:
    """Two consecutive UI turns must share one session.

    Regression test for the bug where every UI turn created a fresh
    session, breaking short-term memory end-to-end.
    """
    from app.ui import UIController

    azmuth, llm, _ = _make_azmuth(
        settings, responses=["first reply", "second reply"]
    )
    controller = UIController(
        language=azmuth.language,
        responder=azmuth.respond_as_text,
    )

    _run(controller.send_text("first message"))
    assert controller.conversation_id is not None
    first_id = controller.conversation_id

    _run(controller.send_text("second message"))
    assert controller.conversation_id == first_id

    # The second LLM call's messages must include the first exchange.
    second_call_contents = [m.content for m in llm.calls[1].messages]
    assert "first message" in second_call_contents
    assert "first reply" in second_call_contents
    assert "second message" in second_call_contents


def test_ui_reset_conversation_starts_new_session(settings: Settings) -> None:
    from app.ui import UIController

    azmuth, _, _ = _make_azmuth(settings, responses=["a", "b"])
    controller = UIController(
        language=azmuth.language,
        responder=azmuth.respond_as_text,
    )

    _run(controller.send_text("turn one"))
    first = controller.conversation_id

    controller.reset_conversation()
    assert controller.conversation_id is None

    _run(controller.send_text("turn two"))
    assert controller.conversation_id != first