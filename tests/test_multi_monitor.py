"""Tests for multi-monitor DPI and input coordinate fixes (Phase 1.1)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metrics(values: dict[int, int]):
    """Return a GetSystemMetrics mock that maps index -> value."""
    def _mock(idx):
        return values.get(idx, 0)
    return _mock


# ---------------------------------------------------------------------------
# CoordinateSpaces / query_spaces
# ---------------------------------------------------------------------------

class TestCoordinateSpaces:
    def test_virtual_origin_stored(self):
        from app.automation.dpi import CoordinateSpaces
        cs = CoordinateSpaces(
            input_width=3840, input_height=1080,
            screenshot_width=3840, screenshot_height=1080,
            virtual_x=-1920, virtual_y=0,
        )
        assert cs.virtual_x == -1920
        assert cs.virtual_y == 0

    def test_as_dict_contains_virtual_origin(self):
        from app.automation.dpi import CoordinateSpaces
        cs = CoordinateSpaces(
            input_width=3840, input_height=1080,
            screenshot_width=3840, screenshot_height=1080,
            virtual_x=-1920, virtual_y=0,
        )
        d = cs.as_dict()
        assert "virtual_origin" in d
        assert d["virtual_origin"] == [-1920, 0]

    def test_scale_uses_virtual_width(self):
        """With two 1920-wide monitors (3840 total), scale stays 1.0."""
        from app.automation.dpi import CoordinateSpaces
        cs = CoordinateSpaces(
            input_width=3840, input_height=1080,
            screenshot_width=3840, screenshot_height=1080,
        )
        assert cs.scale_x == pytest.approx(1.0)
        assert cs.scale_y == pytest.approx(1.0)

    def test_scale_dpi_scaled(self):
        """125% DPI: 1920 physical / 1536 logical."""
        from app.automation.dpi import CoordinateSpaces
        cs = CoordinateSpaces(
            input_width=1536, input_height=864,
            screenshot_width=1920, screenshot_height=1080,
        )
        assert cs.scale_x == pytest.approx(1536 / 1920)
        assert cs.scale_y == pytest.approx(864 / 1080)

    def test_screenshot_to_input_roundtrip(self):
        from app.automation.dpi import CoordinateSpaces
        cs = CoordinateSpaces(
            input_width=3840, input_height=1080,
            screenshot_width=3840, screenshot_height=1080,
        )
        assert cs.screenshot_to_input(960, 540) == (960, 540)

    def test_default_virtual_origin_is_zero(self):
        from app.automation.dpi import CoordinateSpaces
        cs = CoordinateSpaces(
            input_width=1920, input_height=1080,
            screenshot_width=1920, screenshot_height=1080,
        )
        assert cs.virtual_x == 0
        assert cs.virtual_y == 0


class TestQuerySpaces:
    def _patch_metrics(self, values: dict[int, int]):
        """Return a context manager patching ctypes.windll.user32.GetSystemMetrics."""
        mock_user32 = MagicMock()
        mock_user32.GetSystemMetrics.side_effect = _make_metrics(values)
        return patch("app.automation.dpi.ctypes.windll.user32", mock_user32)

    def test_single_monitor_uses_virtual_metrics(self):
        from app.automation.dpi import query_spaces, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN
        metrics = {
            _SM_CXVIRTUALSCREEN: 1920,
            _SM_CYVIRTUALSCREEN: 1080,
            76: 0, 77: 0,  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
        }
        with self._patch_metrics(metrics):
            cs = query_spaces(screenshot_size=(1920, 1080))
        assert cs.input_width == 1920
        assert cs.input_height == 1080

    def test_dual_monitor_width_captured(self):
        from app.automation.dpi import query_spaces, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN
        # Two 1920x1080 monitors side by side
        metrics = {
            76: 0,   # SM_XVIRTUALSCREEN
            77: 0,   # SM_YVIRTUALSCREEN
            _SM_CXVIRTUALSCREEN: 3840,
            _SM_CYVIRTUALSCREEN: 1080,
            0: 1920, 1: 1080,  # primary (should NOT be used)
        }
        with self._patch_metrics(metrics):
            cs = query_spaces(screenshot_size=(3840, 1080))
        assert cs.input_width == 3840
        assert cs.input_height == 1080

    def test_negative_virtual_origin_stored(self):
        from app.automation.dpi import query_spaces, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN
        # Secondary monitor to the LEFT of primary
        metrics = {
            76: -1920,  # SM_XVIRTUALSCREEN
            77: 0,
            _SM_CXVIRTUALSCREEN: 3840,
            _SM_CYVIRTUALSCREEN: 1080,
        }
        with self._patch_metrics(metrics):
            cs = query_spaces(screenshot_size=(3840, 1080))
        assert cs.virtual_x == -1920

    def test_fallback_to_primary_when_virtual_zero(self):
        from app.automation.dpi import query_spaces
        # Virtual desktop metrics return 0 (mocked env)
        metrics = {76: 0, 77: 0, 78: 0, 79: 0, 0: 1920, 1: 1080}
        with self._patch_metrics(metrics):
            cs = query_spaces(screenshot_size=(1920, 1080))
        assert cs.input_width == 1920
        assert cs.input_height == 1080


# ---------------------------------------------------------------------------
# InputController virtual desktop metrics
# ---------------------------------------------------------------------------

class TestInputControllerVirtualScreen:
    """Test that InputController._virtual_screen uses virtual desktop metrics."""

    def _make_controller(self, metrics: dict[int, int]):
        from app.automation.input import InputController
        ctrl = InputController.__new__(InputController)
        mock_u32 = MagicMock()
        mock_u32.GetSystemMetrics.side_effect = _make_metrics(metrics)
        ctrl._user32 = mock_u32
        return ctrl

    def test_virtual_screen_dual_monitor(self):
        ctrl = self._make_controller({
            76: 0, 77: 0, 78: 3840, 79: 1080,
            0: 1920, 1: 1080,
        })
        vx, vy, vw, vh = ctrl._virtual_screen()
        assert vw == 3840
        assert vh == 1080

    def test_virtual_screen_negative_origin(self):
        ctrl = self._make_controller({
            76: -1920, 77: 0, 78: 3840, 79: 1080,
        })
        vx, vy, vw, vh = ctrl._virtual_screen()
        assert vx == -1920

    def test_fallback_primary_when_virtual_zero(self):
        ctrl = self._make_controller({
            76: 0, 77: 0, 78: 0, 79: 0, 0: 1920, 1: 1080,
        })
        vx, vy, vw, vh = ctrl._virtual_screen()
        assert vw == 1920

    def test_move_mouse_uses_virtualdesk_flag(self):
        """The SendInput call must include MOUSEEVENTF_VIRTUALDESK (0x4000)."""
        import asyncio
        from app.automation.input import InputController, _MOUSEEVENTF_VIRTUALDESK

        ctrl = self._make_controller({
            76: 0, 77: 0, 78: 1920, 79: 1080,
        })
        sent_inputs = []
        ctrl._user32.SendInput.side_effect = lambda n, arr, sz: (
            sent_inputs.extend(list(arr)), n
        )[1]

        asyncio.run(ctrl.move_mouse(960, 540))

        assert sent_inputs, "SendInput was not called"
        flags = sent_inputs[0].mi.dwFlags
        assert flags & _MOUSEEVENTF_VIRTUALDESK, (
            f"MOUSEEVENTF_VIRTUALDESK not set in flags 0x{flags:04x}"
        )

    def test_click_sync_uses_virtualdesk_flag(self):
        """click_sync must also include MOUSEEVENTF_VIRTUALDESK."""
        from app.automation.input import InputController, _MOUSEEVENTF_VIRTUALDESK

        ctrl = self._make_controller({
            76: 0, 77: 0, 78: 1920, 79: 1080,
        })
        sent_inputs = []
        ctrl._user32.SendInput.side_effect = lambda n, arr, sz: (
            sent_inputs.extend(list(arr)), n
        )[1]

        ctrl.click_sync(960, 540)

        assert sent_inputs, "SendInput was not called"
        flags = sent_inputs[0].mi.dwFlags
        assert flags & _MOUSEEVENTF_VIRTUALDESK, (
            f"MOUSEEVENTF_VIRTUALDESK not set in flags 0x{flags:04x}"
        )

    def test_coordinate_normalisation_second_monitor(self):
        """A click at x=2880 on a dual monitor (origin=0, width=3840) should
        normalise to roughly 65535*2880/3839 ~ 49151."""
        import asyncio
        from app.automation.input import InputController

        ctrl = self._make_controller({
            76: 0, 77: 0, 78: 3840, 79: 1080,
        })
        sent_inputs = []
        ctrl._user32.SendInput.side_effect = lambda n, arr, sz: (
            sent_inputs.extend(list(arr)), n
        )[1]

        asyncio.run(ctrl.move_mouse(2880, 540))

        ax = sent_inputs[0].mi.dx
        expected = int(2880 * 65535 / (3840 - 1))
        assert abs(ax - expected) <= 1, f"ax={ax} expected~{expected}"
