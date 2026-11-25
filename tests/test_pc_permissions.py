"""Permission tests for PC controls: creator-only, destructive needs confirm."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.security.capabilities import DEFAULT_POLICY, Capability
from app.security.modes import (
    AssuranceLevel, PermissionMode, Principal,
)
from app.security.permissions import PermissionManager
from app.security.workspace import ProjectWorkspacePolicy


def _principal(mode: PermissionMode, user_id: str | None = None):
    return Principal(
        user_id=user_id,
        mode=mode,
        assurance=(
            AssuranceLevel.HIGH
            if mode is PermissionMode.CREATOR
            else AssuranceLevel.LOW
        ),
        methods=("pin",) if mode is PermissionMode.CREATOR else (),
        authenticated_at=datetime.now(timezone.utc),
    )


def _manager():
    return PermissionManager(
        workspace_policy=ProjectWorkspacePolicy.from_paths([])
    )


def test_control_system_state_creator_only():
    pm = _manager()
    assert pm.check(
        _principal(PermissionMode.RESTRICTED),
        Capability.CONTROL_SYSTEM_STATE,
    ) is False
    assert pm.check(
        _principal(PermissionMode.CREATOR, "creator"),
        Capability.CONTROL_SYSTEM_STATE,
    ) is True


def test_control_power_creator_only():
    pm = _manager()
    assert pm.check(
        _principal(PermissionMode.RESTRICTED),
        Capability.CONTROL_POWER,
    ) is False
    assert pm.check(
        _principal(PermissionMode.CREATOR, "creator"),
        Capability.CONTROL_POWER,
    ) is True


def test_control_power_policy_requires_confirmation():
    policy = DEFAULT_POLICY[Capability.CONTROL_POWER]
    assert policy.requires_confirmation is True
    policy = DEFAULT_POLICY[Capability.CONTROL_SYSTEM_STATE]
    assert policy.requires_confirmation is False