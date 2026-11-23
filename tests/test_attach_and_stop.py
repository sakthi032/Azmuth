"""Attach flow (session registry) and Stop thread-safety."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.documents.session_files import (
    SessionFileRegistry, get_registry,
)
from app.security.workspace import ProjectWorkspacePolicy
from app.tools.builtin.documents import AskDocumentTool, ReadDocumentTool


@pytest.fixture
def workspace(tmp_path: Path) -> ProjectWorkspacePolicy:
    return ProjectWorkspacePolicy.from_paths([tmp_path])


@pytest.fixture
def registry(monkeypatch) -> SessionFileRegistry:
    """Isolated registry per test so nothing leaks."""
    import app.documents.session_files as sf
    reg = SessionFileRegistry()
    monkeypatch.setattr(sf, "_REGISTRY", reg)
    return reg


# ── SessionFileRegistry ────────────────────────────────────────────────

def test_registry_allow_and_check(tmp_path: Path):
    reg = SessionFileRegistry()
    p = tmp_path / "note.txt"
    p.write_text("hi", encoding="utf-8")
    resolved = reg.allow(p)
    assert resolved == p.resolve()
    assert reg.is_allowed(p)
    assert reg.is_allowed(str(p))
    assert not reg.is_allowed(tmp_path / "other.txt")


def test_registry_clear(tmp_path: Path):
    reg = SessionFileRegistry()
    p = tmp_path / "n.txt"
    p.write_text("x", encoding="utf-8")
    reg.allow(p)
    reg.clear()
    assert not reg.is_allowed(p)


def test_registry_paths_snapshot(tmp_path: Path):
    reg = SessionFileRegistry()
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    reg.allow(a)
    reg.allow(b)
    assert set(reg.paths()) == {a.resolve(), b.resolve()}


# ── Tools honour the session registry ──────────────────────────────────

@pytest.mark.asyncio
async def test_read_document_reads_outside_workspace_when_allowed(
    tmp_path: Path, workspace, registry,
):
    outside = tmp_path.parent / f"__attach_test_{tmp_path.name}.txt"
    outside.write_text("session-scoped text", encoding="utf-8")
    try:
        registry.allow(outside)
        tool = ReadDocumentTool(workspace_policy=workspace)
        result = await tool.execute(path=str(outside))
        assert "error" not in result
        assert "session-scoped text" in result["text"]
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_read_document_still_rejects_outside_workspace(
    tmp_path: Path, workspace, registry,
):
    outside = tmp_path.parent / f"__attach_test_rej_{tmp_path.name}.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        tool = ReadDocumentTool(workspace_policy=workspace)
        result = await tool.execute(path=str(outside))
        assert "error" in result
        assert "outside the workspace" in result["error"]
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_ask_document_reads_outside_when_allowed(
    tmp_path: Path, workspace, registry,
):
    outside = tmp_path.parent / f"__attach_test_q_{tmp_path.name}.md"
    outside.write_text(
        "# Install\nRun pip install.\n\n# Usage\nCall fn().\n",
        encoding="utf-8",
    )
    try:
        registry.allow(outside)
        tool = AskDocumentTool(workspace_policy=workspace)
        result = await tool.execute(
            path=str(outside), question="how to install",
        )
        assert "error" not in result
        assert result["blocks"]
        assert "Install" in result["blocks"][0]["text"]
    finally:
        outside.unlink(missing_ok=True)


# ── Stop thread-safety at the bridge level ────────────────────────────

@pytest.mark.asyncio
async def test_cancel_in_flight_terminates_send_text():
    """cancel_in_flight cancels the current send_text task."""
    from app.ui.bridge import UIController
    from app.ui.state import UIState

    state = UIState()
    lm = MagicMock()
    lm.resolve_response_language = lambda t, session_language=None: "en"

    started = asyncio.Event()
    was_cancelled = False

    async def slow_responder(user_input, conversation_id, confirmer,
                             on_delta=None):
        nonlocal was_cancelled
        started.set()
        try:
            await asyncio.sleep(10.0)
            return "never", "s", "m", "p", ""
        except asyncio.CancelledError:
            was_cancelled = True
            raise

    controller = UIController(
        language=lm, responder=slow_responder, state=state,
    )

    task = asyncio.create_task(controller.send_text("hi there"))
    await asyncio.wait_for(started.wait(), timeout=2.0)
    controller.cancel_in_flight()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert was_cancelled is True
    assert controller._current_task is None
    texts = [e.text for e in state.conversation]
    assert any("Stopped" in t for t in texts)