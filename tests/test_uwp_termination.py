"""Tests for UWP termination fix (Phase 1.2).

Verifies that closing a UWP app never kills ApplicationFrameHost.exe
wholesale, and that graceful WM_CLOSE is attempted first.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


class TestUWPTermination:
    """ApplicationController.close() for UWP aliases must never target
    ApplicationFrameHost.exe."""

    def _make_ctrl(self):
        from app.automation.applications import ApplicationController
        ctrl = ApplicationController.__new__(ApplicationController)
        ctrl._policy = MagicMock()
        ctrl._discovery = MagicMock()
        return ctrl

    def test_uwp_process_map_has_no_applicationframehost(self):
        """The specific-process map must not contain ApplicationFrameHost."""
        from app.automation.applications import _UWP_PROCESS_NAMES
        for alias, proc in _UWP_PROCESS_NAMES.items():
            assert "ApplicationFrameHost" not in proc, (
                f"alias {alias!r} maps to {proc!r} which contains "
                "ApplicationFrameHost -- this kills all UWP apps!"
            )

    def test_calculator_maps_to_calculatorapp(self):
        from app.automation.applications import _UWP_PROCESS_NAMES
        assert _UWP_PROCESS_NAMES.get("calculator") == "CalculatorApp.exe"

    def test_settings_maps_to_systemsettings(self):
        from app.automation.applications import _UWP_PROCESS_NAMES
        assert _UWP_PROCESS_NAMES.get("settings") == "SystemSettings.exe"

    @pytest.mark.asyncio
    async def test_close_uwp_sends_wm_close_not_applicationframehost(self):
        """close('calculator') must not invoke taskkill on ApplicationFrameHost."""
        from app.automation.applications import ApplicationController

        ctrl = self._make_ctrl()

        taskkill_calls: list[list[str]] = []

        async def _fake_run_capture(argv, *, timeout, accept_exit_codes=(0, 128)):
            taskkill_calls.append(argv)
            return ""

        with (
            patch("app.automation.applications._find_window_hwnd", return_value=12345),
            patch("app.automation.applications._send_wm_close", return_value=True),
            patch("app.automation.applications._run_capture", _fake_run_capture),
        ):
            # Patch _is_process_running to say still alive (forces taskkill path)
            ctrl._is_process_running = AsyncMock(return_value=True)
            await ctrl.close("calculator")

        # Must NOT have killed ApplicationFrameHost
        for argv in taskkill_calls:
            assert "ApplicationFrameHost.exe" not in argv, (
                f"taskkill was called with ApplicationFrameHost.exe: {argv}"
            )
        # Must have killed CalculatorApp.exe specifically
        killed = [argv for argv in taskkill_calls if "CalculatorApp.exe" in argv]
        assert killed, "CalculatorApp.exe was never targeted by taskkill"

    @pytest.mark.asyncio
    async def test_close_uwp_graceful_no_taskkill(self):
        """If WM_CLOSE works and process exits, taskkill is never called."""
        from app.automation.applications import ApplicationController

        ctrl = self._make_ctrl()
        taskkill_calls: list[list[str]] = []

        async def _fake_run_capture(argv, *, timeout, accept_exit_codes=(0, 128)):
            taskkill_calls.append(argv)
            return ""

        with (
            patch("app.automation.applications._find_window_hwnd", return_value=99999),
            patch("app.automation.applications._send_wm_close", return_value=True),
            patch("app.automation.applications._run_capture", _fake_run_capture),
            patch("asyncio.sleep", new=AsyncMock()),  # skip the 1.5s wait
        ):
            # Process gone after graceful close
            ctrl._is_process_running = AsyncMock(return_value=False)
            count = await ctrl.close("calculator")

        assert count == 1  # WM_CLOSE counted as one termination
        assert not taskkill_calls, (
            f"taskkill should not have been called, got: {taskkill_calls}"
        )

    @pytest.mark.asyncio
    async def test_close_non_uwp_uses_taskkill_normally(self):
        """Non-UWP identifiers use the normal taskkill path."""
        from app.automation.applications import ApplicationController

        ctrl = self._make_ctrl()
        taskkill_calls: list[list[str]] = []

        async def _fake_run_capture(argv, *, timeout, accept_exit_codes=(0, 128)):
            taskkill_calls.append(argv)
            return "SUCCESS: The process notepad.exe has been terminated."

        with patch("app.automation.applications._run_capture", _fake_run_capture):
            ctrl._image_name_for_pid = AsyncMock(return_value=None)
            ctrl._is_process_running = AsyncMock(return_value=False)
            count = await ctrl.close("notepad.exe")

        assert count == 1
        assert any("notepad.exe" in argv for argv in taskkill_calls)
        # ApplicationFrameHost must never appear
        for argv in taskkill_calls:
            assert "ApplicationFrameHost.exe" not in argv

    def test_find_window_hwnd_returns_none_on_non_windows(self):
        """_find_window_hwnd must return None gracefully on non-Windows."""
        with patch("app.automation.applications.sys.platform", "linux"):
            from app.automation.applications import _find_window_hwnd
            result = _find_window_hwnd("Calculator")
        assert result is None

    def test_send_wm_close_returns_false_on_non_windows(self):
        """_send_wm_close must return False gracefully on non-Windows."""
        with patch("app.automation.applications.sys.platform", "linux"):
            from app.automation.applications import _send_wm_close
            result = _send_wm_close(12345)
        assert result is False
