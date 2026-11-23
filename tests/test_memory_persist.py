"""Memory persistence and command handling."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config.settings import load_settings
from app.memory.backends.sqlite_backend import SqliteMemoryBackend
from app.memory.manager import MemoryManager
from app.memory.models import MemoryKind


def test_memory_persists_across_backend_restart(tmp_path):
    """Facts written to SQLite survive close/reopen."""
    settings = load_settings()
    db = tmp_path / "memory.db"

    b1 = SqliteMemoryBackend(db)
    m1 = MemoryManager(settings, b1)
    m1.store("My name is Sakthi", user_id="creator",
             kind=MemoryKind.FACT, importance=0.9)
    m1.store("I like dark mode", user_id="creator",
             kind=MemoryKind.PREFERENCE, importance=0.7)
    b1.close()

    b2 = SqliteMemoryBackend(db)
    m2 = MemoryManager(settings, b2)
    records = m2.list_for_user("creator")
    contents = {r.content for r in records}
    assert "My name is Sakthi" in contents
    assert "I like dark mode" in contents
    b2.close()


def test_extractor_stores_name_and_preference(tmp_path):
    """'My name is X and I like Y' produces at least one stored fact."""
    from app.memory.extractor import extract_facts

    settings = load_settings()
    m = MemoryManager(settings, SqliteMemoryBackend(tmp_path / "m.db"))

    n = extract_facts(
        "My name is Sakthi and I like dark mode",
        user_id="creator",
        memory=m,
    )
    assert n >= 1
    records = m.list_for_user("creator")
    assert any("Sakthi" in r.content for r in records)


def test_detect_memory_command_list():
    from app.brain.pipeline import _detect_memory_command
    assert _detect_memory_command("what do you remember about me") == ("list", None)
    assert _detect_memory_command("What have I told you?") == ("list", None)
    assert _detect_memory_command("show my memories") == ("list", None)


def test_detect_memory_command_forget():
    from app.brain.pipeline import _detect_memory_command
    assert _detect_memory_command("forget my age") == ("forget", "my age")
    assert _detect_memory_command("Please forget about that reminder") == (
        "forget", "that reminder",
    )


def test_detect_memory_command_none():
    from app.brain.pipeline import _detect_memory_command
    assert _detect_memory_command("what's the weather") == ("", None)
    assert _detect_memory_command("hi") == ("", None)


@pytest.mark.asyncio
async def test_handle_memory_command_lists(tmp_path):
    """Direct handler returns a formatted list, not an LLM call."""
    from app.brain.pipeline import Brain
    from app.config.settings import load_settings
    from app.memory.conversation.manager import SessionManager

    settings = load_settings()
    backend = SqliteMemoryBackend(tmp_path / "m.db")
    memory = MemoryManager(settings, backend)
    memory.store("My name is Sakthi", user_id="creator",
                 kind=MemoryKind.FACT, importance=0.9)
    memory.store("I like dark mode", user_id="creator",
                 kind=MemoryKind.PREFERENCE, importance=0.7)

    brain = Brain(
        llm=MagicMock(),
        memory=memory,
        sessions=SessionManager(settings),
        language=MagicMock(),
        tools=MagicMock(),
        search=MagicMock(),
        event_bus=None,
    )
    session = brain._sessions.create(user_id="creator")
    result = await brain._handle_memory_command(
        "what do you remember about me",
        user_id="creator",
        session=session,
    )
    assert result is not None
    assert "Sakthi" in result.text
    assert "dark mode" in result.text
    assert result.stopped_reason == "memory_list"
    backend.close()


@pytest.mark.asyncio
async def test_handle_memory_command_forget(tmp_path):
    from app.brain.pipeline import Brain
    from app.config.settings import load_settings
    from app.memory.conversation.manager import SessionManager

    settings = load_settings()
    backend = SqliteMemoryBackend(tmp_path / "m.db")
    memory = MemoryManager(settings, backend)
    memory.store("My age is 30", user_id="creator",
                 kind=MemoryKind.FACT, importance=0.9)

    brain = Brain(
        llm=MagicMock(),
        memory=memory,
        sessions=SessionManager(settings),
        language=MagicMock(),
        tools=MagicMock(),
        search=MagicMock(),
        event_bus=None,
    )
    session = brain._sessions.create(user_id="creator")
    result = await brain._handle_memory_command(
        "forget my age",
        user_id="creator",
        session=session,
    )
    assert result is not None
    assert "Forgotten" in result.text
    assert memory.list_for_user("creator") == []
    backend.close()
    

def test_extract_atomic_facts(tmp_path):
    """Store atomic 'Label: value' pairs, not raw sentences."""
    from app.config.settings import load_settings
    from app.memory.backends.sqlite_backend import SqliteMemoryBackend
    from app.memory.extractor import extract_facts
    from app.memory.manager import MemoryManager

    settings = load_settings()
    m = MemoryManager(settings, SqliteMemoryBackend(tmp_path / "m.db"))

    n = extract_facts(
        "Hi, my name is Sakthi and I like dark mode preference",
        user_id="u1", memory=m,
    )
    assert n >= 2

    contents = {r.content for r in m.list_for_user("u1")}
    assert "Name: Sakthi" in contents
    # "preference" suffix stripped
    assert "Likes: dark mode" in contents
    # Raw sentence is NOT stored
    assert not any("Hi, my name" in c for c in contents)


def test_extract_conflict_updates(tmp_path):
    """New value for the same label replaces the old one."""
    from app.config.settings import load_settings
    from app.memory.backends.sqlite_backend import SqliteMemoryBackend
    from app.memory.extractor import extract_facts
    from app.memory.manager import MemoryManager

    settings = load_settings()
    m = MemoryManager(settings, SqliteMemoryBackend(tmp_path / "m.db"))

    extract_facts("My name is Sakthi", user_id="u1", memory=m)
    extract_facts("My name is Bob", user_id="u1", memory=m)

    names = [
        r.content for r in m.list_for_user("u1")
        if r.content.startswith("Name:")
    ]
    assert names == ["Name: Bob"]


def test_extract_skips_secrets(tmp_path):
    """Password-shaped labels and values are never stored."""
    from app.config.settings import load_settings
    from app.memory.backends.sqlite_backend import SqliteMemoryBackend
    from app.memory.extractor import extract_facts
    from app.memory.manager import MemoryManager

    settings = load_settings()
    m = MemoryManager(settings, SqliteMemoryBackend(tmp_path / "m.db"))

    extract_facts(
        "My password is hunter2 and my api key is "
        "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        user_id="u1", memory=m,
    )
    records = m.list_for_user("u1")
    assert not records, [r.content for r in records]

