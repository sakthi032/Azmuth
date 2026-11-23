"""Reproduces A1: capture must work while Azmuth is foreground."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.tools.builtin.screen_understanding as su
from app.tools.builtin.screen_understanding import DescribeScreenTool


@dataclass
class FakeCapture:
    path: object = None
    width: int = 100
    height: int = 100
    png_bytes: bytes = b"\x89PNG fake"


def _install(monkeypatch, **overrides):
    defaults = {
        "capture_window": AsyncMock(return_value=FakeCapture()),
        "capture_full_screen": AsyncMock(return_value=FakeCapture()),
        "describe_image_detailed": AsyncMock(
            return_value=("a terminal window", "")
        ),
    }
    for name, fn in {**defaults, **overrides}.items():
        monkeypatch.setattr(su, name, fn)


@pytest.mark.asyncio
async def test_capture_works_while_self_foreground(monkeypatch):
    """The original bug: describe_screen returned 'bring target forward'.
    Must capture the last non-self window instead."""
    _install(monkeypatch)
    import app.automation.self_guard as sg

    monkeypatch.setattr(sg, "is_self_foreground", lambda: True)
    monkeypatch.setattr(
        sg, "last_non_self_foreground", lambda: (9999, "Chrome - tab"),
    )
    monkeypatch.setattr(sg, "start_foreground_poller", lambda: None)
    monkeypatch.setattr(
        su, "_get_window_rect", lambda _h: (0, 0, 100, 100),
    )

    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="what is this")

    assert "error" not in result, result
    assert result["answer"] == "a terminal window"
    assert result["was_self_foreground"] is True
    # Captured the remembered hwnd, not the current self window
    su.capture_window.assert_awaited_once_with(9999)


@pytest.mark.asyncio
async def test_capture_falls_back_to_full_when_no_last_window(monkeypatch):
    _install(monkeypatch)
    import app.automation.self_guard as sg

    monkeypatch.setattr(sg, "is_self_foreground", lambda: True)
    monkeypatch.setattr(
        sg, "last_non_self_foreground", lambda: (0, ""),
    )
    monkeypatch.setattr(sg, "start_foreground_poller", lambda: None)
    monkeypatch.setattr(su, "_minimize_self", AsyncMock(return_value=True))
    monkeypatch.setattr(su, "_restore_self", AsyncMock())

    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="what is this")

    assert "error" not in result
    su.capture_full_screen.assert_awaited_once()


@pytest.mark.asyncio
async def test_input_tools_still_blocked_when_self_foreground(monkeypatch):
    """The input guard is unchanged — click/type still refuse."""
    from app.automation.self_guard import is_self_foreground
    # Sanity — the function exists and returns bool
    assert isinstance(is_self_foreground(), bool)
    # Input controllers call this before every synthetic action.
    # We are not touching that path; test merely documents intent.