"""Tests for A2: lock_screen and shutdown_timer require UI confirmation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.brain.pipeline import Brain, ConfirmationRequest
from app.config.settings import load_settings
from app.core.errors import ConfirmationRequiredError
from app.core.registry import Registry
from app.language.manager import LanguageManager
from app.llm.base import LLMProvider
from app.llm.messages import ChatRequest, ChatResponse, ModelInfo, StreamChunk, ToolCallRequest
from app.memory.conversation.manager import SessionManager
from app.memory.manager import build_memory_manager
from app.search.base import SearchProvider
from app.search.manager import SearchManager
from app.search.providers.null import NullSearchProvider
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.tools.builtin.pc_controls import LockScreenTool, PCController, ShutdownTimerTool
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry
from app.voice.input_manager import InputSource, UserInput


class _ToolCallingLLM(LLMProvider):
    name = "stub"

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._args = args
        self._round = 0

    @property
    def model(self) -> str:
        return "stub-model"

    def is_available(self) -> bool:
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self._round += 1
        if self._round == 1:
            return ChatResponse(
                content="",
                model=self.model,
                provider=self.name,
                tool_calls=(
                    ToolCallRequest(
                        id="tc-1",
                        name=self._tool_name,
                        arguments=dict(self._args),
                    ),
                ),
            )
        return ChatResponse(content="Done.", model=self.model, provider=self.name)

    async def stream(self, request: ChatRequest):
        resp = await self.chat(request)
        if resp.tool_calls:
            yield StreamChunk(delta="", tool_calls=resp.tool_calls, finish_reason="tool_calls")
        else:
            yield StreamChunk(delta=resp.content, finish_reason="stop")

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(provider=self.name, model=self.model)


def _creator() -> Principal:
    return Principal(
        user_id="sakthi",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("password",),
        authenticated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_lock_screen_denied_by_confirmer_does_not_run(tmp_path: Path, monkeypatch) -> None:
    called = False

    async def fake_lock(self):
        nonlocal called
        called = True
        raise AssertionError("lock_screen must not execute when confirmation is denied")

    monkeypatch.setattr(PCController, "lock_screen", fake_lock)

    settings = load_settings(
        env_file=tmp_path / "missing.env",
        environ={"AZMUTH_HOME": str(tmp_path)},
    )
    reg = ToolRegistry()
    reg.register(LockScreenTool(PCController()))
    tools = ToolManager(PermissionManager(), reg)

    # 1) Direct ToolManager without confirmed=True must raise ConfirmationRequiredError
    with pytest.raises(ConfirmationRequiredError):
        await tools.invoke("lock_screen", {}, principal=_creator(), confirmed=False)
    assert called is False

    # 2) Brain turn with a mocked confirmer that denies must prompt and not run
    prompts: list[ConfirmationRequest] = []

    async def deny_confirmer(req: ConfirmationRequest) -> bool:
        prompts.append(req)
        return False

    search_reg: Registry[SearchProvider] = Registry("search")
    search_reg.register("null", NullSearchProvider)

    brain = Brain(
        llm=_ToolCallingLLM("lock_screen", {}),
        memory=build_memory_manager(settings),
        sessions=SessionManager(settings),
        language=LanguageManager(settings),
        tools=tools,
        search=SearchManager("null", search_reg),
    )

    await brain.respond(
        UserInput(text="lock my screen", language="en", source=InputSource.TEXT),
        principal=_creator(),
        confirmer=deny_confirmer,
    )

    assert len(prompts) == 1
    assert prompts[0].tool_name == "lock_screen"
    assert called is False


@pytest.mark.asyncio
async def test_shutdown_timer_denied_by_confirmer_does_not_run(tmp_path: Path, monkeypatch) -> None:
    called = False

    async def fake_shutdown(self, action: str, seconds: int = 0):
        nonlocal called
        called = True
        raise AssertionError("shutdown_timer must not execute when confirmation is denied")

    monkeypatch.setattr(PCController, "schedule_shutdown", fake_shutdown)

    settings = load_settings(
        env_file=tmp_path / "missing.env",
        environ={"AZMUTH_HOME": str(tmp_path)},
    )
    reg = ToolRegistry()
    reg.register(ShutdownTimerTool(PCController()))
    tools = ToolManager(PermissionManager(), reg)

    prompts: list[ConfirmationRequest] = []

    async def deny_confirmer(req: ConfirmationRequest) -> bool:
        prompts.append(req)
        return False

    search_reg: Registry[SearchProvider] = Registry("search")
    search_reg.register("null", NullSearchProvider)

    brain = Brain(
        llm=_ToolCallingLLM("shutdown_timer", {"action": "set", "seconds": 60}),
        memory=build_memory_manager(settings),
        sessions=SessionManager(settings),
        language=LanguageManager(settings),
        tools=tools,
        search=SearchManager("null", search_reg),
    )

    await brain.respond(
        UserInput(text="shut down in 60 seconds", language="en", source=InputSource.TEXT),
        principal=_creator(),
        confirmer=deny_confirmer,
    )

    assert len(prompts) == 1
    assert prompts[0].tool_name == "shutdown_timer"
    assert called is False
