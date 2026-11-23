"""Integration test: fact told in session 1, recalled in session 2."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.memory.extractor import extract_facts
from app.memory.manager import MemoryManager
from app.memory.backends.sqlite_backend import SqliteMemoryBackend
from app.config.settings import Settings
import tempfile, pathlib


@pytest.fixture
def tmp_memory(tmp_path):
    backend = SqliteMemoryBackend(tmp_path / "mem.db")
    settings = MagicMock(spec=Settings)
    settings.memory_hybrid_recall = True
    mgr = MemoryManager(settings, backend)
    return mgr


def test_fact_extraction_stores_name(tmp_memory):
    count = extract_facts("My name is Sakthi", user_id="u1", memory=tmp_memory)
    assert count >= 1
    results = tmp_memory.search("name", user_id="u1", limit=5)
    assert any("Sakthi" in r.content for r in results)


def test_fact_extraction_stores_preference(tmp_memory):
    count = extract_facts("I like dark mode", user_id="u1", memory=tmp_memory)
    assert count >= 1
    results = tmp_memory.search("dark mode", user_id="u1", limit=5)
    assert any("dark mode" in r.content.lower() for r in results)


def test_recall_after_extraction(tmp_memory):
    extract_facts("My name is Sakthi and I like dark mode", user_id="u1", memory=tmp_memory)
    results = tmp_memory.recall("what do you know about me", user_id="u1", top_k=5)
    assert len(results) > 0


def test_no_duplicate_extraction(tmp_memory):
    extract_facts("My name is Sakthi", user_id="u1", memory=tmp_memory)
    extract_facts("My name is Sakthi", user_id="u1", memory=tmp_memory)
    results = tmp_memory.search("Sakthi", user_id="u1", limit=10)
    # Should not have duplicate entries
    contents = [r.content for r in results]
    assert len(contents) == len(set(contents))
