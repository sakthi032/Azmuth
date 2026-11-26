"""Routing tests verifying screen/vision and tool-using queries avoid FAST tier."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.brain.pipeline import Brain
from app.brain.purpose import detect_purpose
from app.config.settings import load_settings
from app.language.manager import LanguageManager
from app.llm.base import LLMProvider
from app.llm.messages import ChatRequest, ChatResponse, ModelInfo, StreamChunk
from app.llm.registry import ModelPurpose, ModelRegistry, ModelSpec
from app.llm.router import LLMRouter
from app.memory.conversation.manager import SessionManager
from app.memory.manager import MemoryManager, build_memory_manager
from app.core.registry import Registry
from app.search.manager import SearchManager
from app.search.base import SearchProvider
from app.search.providers.null import NullSearchProvider
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.tools.base import RiskLevel, Tool
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry
from app.security.capabilities import Capability
from app.voice.input_manager import InputSource, UserInput


class _RecordingProvider(LLMProvider):
    name = "stub"

    def __init__(self, model: str, reply: str) -> None:
        self._model = model
        self._reply = reply
        self.calls: list[ChatRequest] = []

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(content=self._reply, model=self.model, provider=self.name)

    async def stream(self, request: ChatRequest):
        yield StreamChunk(delta=self._reply, finish_reason="stop")

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(provider=self.name, model=self.model)


class _DummyDescribeScreenTool(Tool):
    name = "describe_screen"
    description = "Capture and describe the screen."
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    capability = Capability.VIEW_SYSTEM_STATUS
    risk_level = RiskLevel.LOW

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return {"summary": "code editor open"}


class _LogCaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_detect_purpose_routes_screen_queries_to_chat():
    assert detect_purpose("what is on my screen") is ModelPurpose.CHAT
    assert detect_purpose("What's on my screen right now?") is ModelPurpose.CHAT
    assert detect_purpose("describe the screen") is ModelPurpose.CHAT
    assert detect_purpose("what is 2 + 2") is ModelPurpose.FAST


@pytest.mark.asyncio
async def test_what_is_on_my_screen_with_tools_routes_to_chat_not_fast(tmp_path: Path):
    fast_provider = _RecordingProvider("nvidia/nemotron-3.5-lightning-30b-a3b", "fast-answer")
    chat_provider = _RecordingProvider("gemini-flash-lite-latest", "chat-answer")

    reg = ModelRegistry()
    reg.register(ModelSpec(
        name="nvidia/nemotron-3.5-lightning-30b-a3b",
        provider="nvidia_nim",
        purposes=frozenset({ModelPurpose.FAST}),
        purpose_tiers={ModelPurpose.FAST: 1},
    ))
    reg.register(ModelSpec(
        name="gemini-flash-lite-latest",
        provider="gemini",
        purposes=frozenset({ModelPurpose.CHAT}),
        purpose_tiers={ModelPurpose.CHAT: 1},
    ))

    _settings = load_settings(env_file=tmp_path / "missing.env", environ={"AZMUTH_HOME": str(tmp_path)})
    router = LLMRouter(_settings, reg)
    router._providers["nvidia_nim::nvidia/nemotron-3.5-lightning-30b-a3b"] = fast_provider
    router._providers["gemini::gemini-flash-lite-latest"] = chat_provider

    tool_reg = ToolRegistry()
    tool_reg.register(_DummyDescribeScreenTool())
    tools = ToolManager(PermissionManager(), tool_reg)

    search_reg: Registry[SearchProvider] = Registry("search")
    search_reg.register("null", NullSearchProvider)

    brain = Brain(
        llm=fast_provider,
        llm_router=router,
        memory=build_memory_manager(_settings),
        sessions=SessionManager(_settings),
        language=LanguageManager(_settings),
        tools=tools,
        search=SearchManager("null", search_reg),
    )

    from app.brain import pipeline as pipeline_mod

    handler = _LogCaptureHandler()
    pipeline_log = pipeline_mod.logger
    prev_level = pipeline_log.level
    pipeline_log.setLevel(logging.INFO)
    pipeline_log.addHandler(handler)
    try:
        resp = await brain.respond(
            UserInput(text="what is on my screen", language="en", source=InputSource.TEXT),
            principal=Principal(
                user_id="sakthi",
                mode=PermissionMode.CREATOR,
                assurance=AssuranceLevel.HIGH,
                methods=("password",),
                authenticated_at=datetime.now(timezone.utc),
            ),
        )
    finally:
        pipeline_log.removeHandler(handler)
        pipeline_log.setLevel(prev_level)

    assert resp.model == "gemini-flash-lite-latest"
    assert len(chat_provider.calls) == 1
    assert len(fast_provider.calls) == 0

    routed = [r for r in handler.records if getattr(r, "event", None) == "TURN_ROUTED"]
    assert len(routed) == 1
    assert getattr(routed[0], "purpose", None) == "chat"
    assert getattr(routed[0], "model", None) == "gemini-flash-lite-latest"
    assert getattr(routed[0], "reason", None) == "vision"
