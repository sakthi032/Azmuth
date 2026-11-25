"""PC controls — tools + controller, mocked OS calls.

None of these tests touch the real OS. pycaw, ctypes.windll,
subprocess and webbrowser are all monkeypatched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.automation.pc_controls import PCController
from app.core.errors import AutomationError
from app.tools.builtin.pc_controls import (
    LockScreenTool, MuteTool, OpenFolderTool, OpenWebsiteTool,
    SetBrightnessTool, SetVolumeTool, ShutdownTimerTool,
)


# --- volume ----------------------------------------------------------

@pytest.mark.asyncio
async def test_set_volume_in_range(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.automation.pc_controls._set_volume_sync",
        lambda level: calls.append(level),
    )
    tool = SetVolumeTool(PCController())
    result = await tool.execute(level=50)
    assert result["success"] is True
    assert calls == [50]


@pytest.mark.asyncio
async def test_set_volume_out_of_range_raises():
    tool = SetVolumeTool(PCController())
    with pytest.raises(AutomationError):
        await tool.execute(level=150)
    with pytest.raises(AutomationError):
        await tool.execute(level=-1)


# --- mute ------------------------------------------------------------

@pytest.mark.asyncio
async def test_mute_on_off(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.automation.pc_controls._mute_sync",
        lambda muted: calls.append(muted),
    )
    tool = MuteTool(PCController())
    await tool.execute(muted=True)
    await tool.execute(muted=False)
    assert calls == [True, False]


# --- brightness ------------------------------------------------------

@pytest.mark.asyncio
async def test_set_brightness_delegates_to_settings(monkeypatch):
    from app.automation.settings import SettingResult
    captured = []
    async def fake_set_brightness(self, level):
        captured.append(level)
        return SettingResult(
            setting="brightness", value=level, success=True,
        )
    monkeypatch.setattr(
        "app.automation.settings.SettingsController.set_brightness",
        fake_set_brightness,
    )
    tool = SetBrightnessTool(PCController())
    result = await tool.execute(level=70)
    assert result["success"] is True
    assert captured == [70]


# --- lock screen -----------------------------------------------------

@pytest.mark.asyncio
async def test_lock_screen_calls_win32(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows only")
    called = []
    class FakeUser32:
        def LockWorkStation(self):
            called.append(True)
            return 1
    class FakeWindll:
        user32 = FakeUser32()
    import ctypes
    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    tool = LockScreenTool(PCController())
    result = await tool.execute()
    assert result["success"] is True
    assert called == [True]


# --- shutdown timer --------------------------------------------------

@pytest.mark.asyncio
async def test_shutdown_timer_set(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.automation.pc_controls._run_argv",
        lambda argv: calls.append(argv),
    )
    tool = ShutdownTimerTool(PCController())
    result = await tool.execute(action="set", seconds=300)
    assert result["success"] is True
    assert result["seconds"] == 300
    assert calls == [["shutdown", "/s", "/t", "300"]]


@pytest.mark.asyncio
async def test_shutdown_timer_cancel(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.automation.pc_controls._run_argv",
        lambda argv: calls.append(argv),
    )
    tool = ShutdownTimerTool(PCController())
    result = await tool.execute(action="cancel")
    assert result["success"] is True
    assert calls == [["shutdown", "/a"]]


@pytest.mark.asyncio
async def test_shutdown_timer_rejects_bad_action():
    tool = ShutdownTimerTool(PCController())
    with pytest.raises(ValueError):
        await tool.execute(action="delete")


@pytest.mark.asyncio
async def test_shutdown_timer_out_of_range():
    tool = ShutdownTimerTool(PCController())
    with pytest.raises(AutomationError):
        await tool.execute(action="set", seconds=999_999)


# --- open folder -----------------------------------------------------

@pytest.mark.asyncio
async def test_open_folder_validates_path(tmp_path: Path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows only")
    calls = []
    monkeypatch.setattr(
        "app.automation.pc_controls.subprocess.Popen",
        lambda argv, **kw: (calls.append(argv), MagicMock())[1],
    )
    tool = OpenFolderTool(PCController())
    result = await tool.execute(path=str(tmp_path))
    assert result["success"] is True
    assert any(argv[0] == "explorer.exe" for argv in calls)


@pytest.mark.asyncio
async def test_open_folder_rejects_missing():
    tool = OpenFolderTool(PCController())
    with pytest.raises(AutomationError):
        await tool.execute(path="C:\\nonexistent_dir_xyz_9876")


# --- open website ----------------------------------------------------

@pytest.mark.asyncio
async def test_open_website_rejects_non_http():
    tool = OpenWebsiteTool(PCController())
    with pytest.raises(AutomationError):
        await tool.execute(url="file:///etc/passwd")
    with pytest.raises(AutomationError):
        await tool.execute(url="javascript:alert(1)")


@pytest.mark.asyncio
async def test_open_website_opens_https(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.automation.pc_controls.webbrowser.open",
        lambda url, new: (calls.append(url), True)[1],
    )
    tool = OpenWebsiteTool(PCController())
    result = await tool.execute(url="https://example.com")
    assert result["success"] is True
    assert calls == ["https://example.com"]