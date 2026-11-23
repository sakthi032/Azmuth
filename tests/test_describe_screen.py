"""describe_screen — self-guard, capture, redact, downscale, VLM."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    """Install sensible defaults; overrides win."""
    defaults = {
        "is_self_foreground": lambda: False,
        "_get_foreground_hwnd": lambda: 12345,
        "_get_window_rect": lambda _h: (0, 0, 100, 100),
        "capture_window": AsyncMock(return_value=FakeCapture()),
        "capture_full_screen": AsyncMock(return_value=FakeCapture()),
        "describe_image_detailed": AsyncMock(
            return_value=("a terminal window", "")
        ),
    }
    for name, fn in {**defaults, **overrides}.items():
        monkeypatch.setattr(su, name, fn)


@pytest.mark.asyncio
async def test_refuses_when_self_foreground(monkeypatch):
    _install(monkeypatch, is_self_foreground=lambda: True)
    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="what's on my screen")
    assert "error" in result
    assert "Azmuth" in result["error"]
    su.capture_window.assert_not_called()
    su.capture_full_screen.assert_not_called()


@pytest.mark.asyncio
async def test_active_window_capture(monkeypatch):
    _install(monkeypatch)
    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="what's this")
    assert "error" not in result
    assert result["answer"] == "a terminal window"
    assert result["answer_source"] == "vision"
    assert result["region"] == "active_window"
    su.capture_window.assert_called_once_with(12345)
    su.capture_full_screen.assert_not_called()


@pytest.mark.asyncio
async def test_full_region(monkeypatch):
    _install(monkeypatch)
    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="what's on", region="full")
    assert "error" not in result
    su.capture_full_screen.assert_called_once()
    su.capture_window.assert_not_called()


@pytest.mark.asyncio
async def test_selection_region_returns_clear_error(monkeypatch):
    _install(monkeypatch)
    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="x", region="selection")
    assert "error" in result
    assert "selection" in result["error"]
    su.capture_window.assert_not_called()


@pytest.mark.asyncio
async def test_no_foreground_window(monkeypatch):
    _install(monkeypatch, _get_foreground_hwnd=lambda: 0)
    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="x")
    assert "error" in result
    assert "foreground" in result["error"]


@pytest.mark.asyncio
async def test_password_fields_redacted(monkeypatch):
    """Password redaction is called; VLM receives a different byte payload."""
    _install(monkeypatch)
    captured: dict = {}

    async def _fake_describe(b, _q, **_kw):
        captured["bytes"] = b
        return "ok", ""

    monkeypatch.setattr(su, "describe_image_detailed", _fake_describe)

    # UIA stub with one password rect
    uia = MagicMock()
    uia.password_field_rects = AsyncMock(
        return_value=[(10, 10, 50, 30)]
    )

    # PNG byte payload needs to be openable by Pillow to be redacted.
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, format="PNG")
    real_bytes = buf.getvalue()

    monkeypatch.setattr(
        su, "capture_window",
        AsyncMock(return_value=FakeCapture(png_bytes=real_bytes)),
    )
    monkeypatch.setattr(su, "_downscale_if_needed", lambda b: b)

    tool = DescribeScreenTool(uia=uia)
    result = await tool.execute(question="x")
    assert "error" not in result
    uia.password_field_rects.assert_awaited()
    # Bytes sent to VLM differ from raw capture (pixels blacked out)
    assert captured["bytes"] != real_bytes


@pytest.mark.asyncio
async def test_downscale_on_large_capture(monkeypatch):
    """A > 4 MB capture is downscaled before reaching the VLM."""
    _install(monkeypatch)
    from PIL import Image
    import io
    # Big enough to exceed 4 MB when PNG-encoded
    buf = io.BytesIO()
    Image.frombytes(
        "RGB", (3000, 2000), bytes(3000 * 2000 * 3)
    ).save(buf, format="PNG", optimize=False)
    big = buf.getvalue()
    if len(big) <= su._MAX_IMAGE_BYTES:
        pytest.skip("PNG compressed under threshold; skipping")

    monkeypatch.setattr(
        su, "capture_window",
        AsyncMock(return_value=FakeCapture(png_bytes=big)),
    )

    seen: dict = {}

    async def _describe(b, _q, **_kw):
        seen["bytes"] = b
        return "ok", ""

    monkeypatch.setattr(su, "describe_image_detailed", _describe)
    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="x")
    assert "error" not in result
    assert len(seen["bytes"]) < len(big)


@pytest.mark.asyncio
async def test_vision_error_surfaced(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(
        su, "describe_image_detailed",
        AsyncMock(return_value=(
            "", "vision API quota exceeded — try again later",
        )),
    )
    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="x")
    assert "error" in result
    assert "quota" in result["error"].lower()


@pytest.mark.asyncio
async def test_missing_key_surfaced(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(
        su, "describe_image_detailed",
        AsyncMock(return_value=(
            "", "GEMINI_API_KEY is not set — vision unavailable",
        )),
    )
    tool = DescribeScreenTool(uia=None)
    result = await tool.execute(question="x")
    assert "error" in result
    assert "GEMINI_API_KEY" in result["error"]