"""Tests for A1: single-valued memory fact deduplication and cleanup."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.brain.pipeline import Brain
from app.config.settings import load_settings
from app.core.registry import Registry
from app.language.manager import LanguageManager
from app.llm.base import LLMProvider
from app.llm.messages import ChatRequest, ChatResponse, ModelInfo, StreamChunk
from app.memory.conversation.manager import SessionManager
from app.memory.extractor import extract_facts
from app.memory.manager import build_memory_manager
from app.memory.models import MemoryKind
from app.search.base import SearchProvider
from app.search.manager import SearchManager
from app.search.providers.null import NullSearchProvider
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry
from app.voice.input_manager import InputSource, UserInput


class _NoopLLM(LLMProvider):
    name = "stub"

    @property
    def model(self) -> str:
        return "stub-model"

    def is_available(self) -> bool:
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(content="ok", model=self.model, provider=self.name)

    async def stream(self, request: ChatRequest):
        yield StreamChunk(delta="ok", finish_reason="stop")

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(provider=self.name, model=self.model)


def _make_memory(tmp_path: Path):
    settings = load_settings(
        env_file=tmp_path / "missing.env",
        environ={"AZMUTH_HOME": str(tmp_path)},
    )
    return settings, build_memory_manager(settings)


def test_single_valued_key_dedup_on_store(tmp_path: Path) -> None:
    """Storing a second value for a single-valued key replaces the old one."""
    _, memory = _make_memory(tmp_path)

    memory.store("Name: Sakthi", user_id="u1", kind=MemoryKind.FACT)
    memory.store("Name: Sakthivel", user_id="u1", kind=MemoryKind.FACT)

    records = memory.list_for_user("u1")
    names = [r.content for r in records if r.content.lower().startswith("name:")]
    assert names == ["Name: Sakthivel"]


def test_single_valued_key_dedup_case_insensitive_and_city(tmp_path: Path) -> None:
    """Case-insensitive keys and city facts also deduplicate."""
    _, memory = _make_memory(tmp_path)

    memory.store("name: Sakthi", user_id="u1", kind=MemoryKind.FACT)
    memory.store("Name: Sakthivel", user_id="u1", kind=MemoryKind.FACT)
    extract_facts("I live in Chennai", user_id="u1", memory=memory)
    extract_facts("I live in Bengaluru", user_id="u1", memory=memory)

    records = memory.list_for_user("u1")
    contents = [r.content for r in records]
    assert [c for c in contents if c.lower().startswith("name:")] == ["Name: Sakthivel"]
    assert [c for c in contents if c.lower().startswith("city:")] == ["City: Bengaluru"]


@pytest.mark.asyncio
async def test_what_do_you_remember_cleans_up_duplicate_single_valued_facts(tmp_path: Path) -> None:
    """'what do you remember about me' cleans up any duplicate single-valued facts."""
    settings, memory = _make_memory(tmp_path)
    # Bypass MemoryManager.store by writing directly to backend to simulate legacy duplicates
    from app.memory.models import MemoryRecord
    from datetime import timedelta

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    memory.backend.store(MemoryRecord(
        user_id="sakthi",
        kind=MemoryKind.FACT,
        content="Name: Sakthi",
        created_at=t0,
        updated_at=t0,
    ))
    memory.backend.store(MemoryRecord(
        user_id="sakthi",
        kind=MemoryKind.FACT,
        content="Name: Sakthivel",
        created_at=t0 + timedelta(minutes=5),
        updated_at=t0 + timedelta(minutes=5),
    ))

    search_reg: Registry[SearchProvider] = Registry("search")
    search_reg.register("null", NullSearchProvider)

    brain = Brain(
        llm=_NoopLLM(),
        memory=memory,
        sessions=SessionManager(settings),
        language=LanguageManager(settings),
        tools=ToolManager(PermissionManager(), ToolRegistry()),
        search=SearchManager("null", search_reg),
    )

    principal = Principal(
        user_id="sakthi",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("password",),
        authenticated_at=datetime.now(timezone.utc),
    )

    resp = await brain.respond(
        UserInput(text="what do you remember about me", language="en", source=InputSource.TEXT),
        principal=principal,
    )

    assert "Sakthivel" in resp.text
    assert "Name: Sakthi\n" not in resp.text
    assert not resp.text.endswith("Name: Sakthi *fact*")
    remaining = memory.list_for_user("sakthi")
    assert [r.content for r in remaining] == ["Name: Sakthivel"]
