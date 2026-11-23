"""Tests for ToolManager availability cache (Phase 1.3)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.tools.manager import ToolManager, _DEFAULT_LIST_TTL, _DEFAULT_INVOKE_TTL


def _make_manager(ttl: float = _DEFAULT_LIST_TTL) -> ToolManager:
    permissions = MagicMock()
    permissions.workspace_policy.approved_roots = []
    return ToolManager(permissions, availability_cache_ttl=ttl)


def _make_tool(name: str = "dummy", available: bool = True) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.is_available.return_value = available
    tool.capability = MagicMock()
    tool.capability.value = "test"
    return tool


class TestAvailabilityCache:
    def test_cache_hit_skips_second_call(self):
        """is_available() is called once; second check within TTL reuses cache."""
        mgr = _make_manager(ttl=10.0)
        tool = _make_tool()

        result1 = mgr._check_available(tool, ttl=10.0)
        result2 = mgr._check_available(tool, ttl=10.0)

        assert result1 is True
        assert result2 is True
        assert tool.is_available.call_count == 1  # only called once

    def test_cache_miss_after_expiry(self):
        """Cache entry is ignored after TTL expires."""
        mgr = _make_manager(ttl=0.05)
        tool = _make_tool()

        mgr._check_available(tool, ttl=0.05)
        time.sleep(0.07)  # wait longer than TTL
        mgr._check_available(tool, ttl=0.05)

        assert tool.is_available.call_count == 2  # called twice

    def test_unavailable_result_is_cached(self):
        """False availability is also cached."""
        mgr = _make_manager()
        tool = _make_tool(available=False)

        r1 = mgr._check_available(tool, ttl=10.0)
        r2 = mgr._check_available(tool, ttl=10.0)

        assert r1 is False
        assert r2 is False
        assert tool.is_available.call_count == 1

    def test_invalidate_single_tool(self):
        """invalidate_availability(name) forces next check to call is_available()."""
        mgr = _make_manager()
        tool = _make_tool()

        mgr._check_available(tool, ttl=10.0)
        mgr.invalidate_availability("dummy")
        mgr._check_available(tool, ttl=10.0)

        assert tool.is_available.call_count == 2

    def test_invalidate_all(self):
        """invalidate_availability() with no args clears the whole cache."""
        mgr = _make_manager()
        t1 = _make_tool("t1")
        t2 = _make_tool("t2")

        mgr._check_available(t1, ttl=10.0)
        mgr._check_available(t2, ttl=10.0)
        mgr.invalidate_availability()
        mgr._check_available(t1, ttl=10.0)
        mgr._check_available(t2, ttl=10.0)

        assert t1.is_available.call_count == 2
        assert t2.is_available.call_count == 2

    def test_register_invalidates_cache(self):
        """Registering a new tool with the same name invalidates old cache."""
        mgr = _make_manager()
        tool = _make_tool()
        mgr._availability_cache["dummy"] = (True, time.monotonic())

        mgr.register(tool)

        assert "dummy" not in mgr._availability_cache

    def test_unregister_invalidates_cache(self):
        """Unregistering a tool clears its cache entry."""
        mgr = _make_manager()
        mgr._availability_cache["dummy"] = (True, time.monotonic())

        mgr.unregister("dummy")  # may raise ToolNotFoundError -- that's OK
        assert "dummy" not in mgr._availability_cache

    def test_list_metadata_uses_cache(self):
        """list_metadata() calls is_available() through the cache."""
        from app.tools.registry import ToolRegistry
        from app.security.permissions import PermissionManager
        from app.security.workspace import ProjectWorkspacePolicy

        permissions = MagicMock()
        permissions.workspace_policy.approved_roots = []
        permissions.check.return_value = True

        registry = MagicMock(spec=ToolRegistry)
        tool = _make_tool()
        tool.metadata.return_value = MagicMock(name="dummy_meta")
        registry.tools.return_value = [tool]

        mgr = ToolManager(permissions, registry, availability_cache_ttl=10.0)

        mgr.list_metadata()
        mgr.list_metadata()

        # is_available called only once despite two list_metadata calls
        assert tool.is_available.call_count == 1

    def test_settings_field_drives_ttl(self):
        """The new settings field is readable and in-range."""
        from app.config.settings import load_settings
        s = load_settings()
        assert s.tool_availability_cache_ttl >= 0.1
