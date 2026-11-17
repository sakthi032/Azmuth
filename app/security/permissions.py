"""Authorization decisions for tools and automation.

This class is intentionally a pure function of (policy, principal,
capability). It holds no per-request state and never modifies the Principal.
Every tool call will pass through :meth:`require` before executing.

The two-step contract used by the ToolManager (STEP 8):

    permissions.require(principal, capability)             # authorization
    permissions.assert_confirmation(principal, cap, ok)    # confirmation
    tool.execute(...)                                       # execution

Neither step can be bypassed: ``require`` raises on the authorization check
and ``assert_confirmation`` raises on the confirmation check.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from app.config.logging_setup import get_logger
from app.core.errors import ConfirmationRequiredError, PermissionDeniedError
from app.security.capabilities import DEFAULT_POLICY, Capability, CapabilityPolicy
from app.security.modes import PermissionMode, Principal
from app.security.workspace import ProjectWorkspacePolicy

logger = get_logger("security.permissions")

#: Applied to any capability with no policy row. Deny by default: creator
#: mode and explicit confirmation are both required. Safe failure mode.
_DENY_ALL: CapabilityPolicy = CapabilityPolicy(
    minimum_mode=PermissionMode.CREATOR,
    requires_confirmation=True,
    description="unclassified capability — denied by default",
)


class PermissionManager:
    """Answers "may this Principal perform this Capability?"."""

    def __init__(
        self,
        policy: Mapping[Capability, CapabilityPolicy] | None = None,
        workspace_policy: ProjectWorkspacePolicy | None = None,
    ) -> None:
        self._policy: dict[Capability, CapabilityPolicy] = dict(
            policy if policy is not None else DEFAULT_POLICY
        )
        for capability in Capability:
            self._policy.setdefault(capability, _DENY_ALL)
        self._workspace_policy = workspace_policy or ProjectWorkspacePolicy()

    # -- workspace policy --------------------------------------------------
    @property
    def workspace_policy(self) -> ProjectWorkspacePolicy:
        return self._workspace_policy

    def set_workspace_policy(self, policy: ProjectWorkspacePolicy) -> None:
        self._workspace_policy = policy

    def add_approved_workspace(self, path: str | Path) -> None:
        self._workspace_policy = self._workspace_policy.add_root(path)

    # -- inspection --------------------------------------------------------
    def policy_for(self, capability: Capability) -> CapabilityPolicy:
        return self._policy.get(capability, _DENY_ALL)

    def requires_confirmation(self, capability: Capability) -> bool:
        return self.policy_for(capability).requires_confirmation

    # -- authorization -----------------------------------------------------
    def check(
        self,
        principal: Principal,
        capability: Capability,
        *,
        path: str | Path | None = None,
    ) -> bool:
        """True if ``principal`` may perform ``capability`` at all or on ``path``."""
        if path is not None and self._workspace_policy.is_in_workspace(path):
            # Inside approved workspace:
            if capability in (
                Capability.READ_PERSONAL_FILE,
                Capability.LIST_FILES,
                Capability.READ_PUBLIC_FILE,
            ):
                return True
            if capability == Capability.WRITE_FILE:
                # Modifying files in approved workspace is authorized (subject to confirmation check)
                return True
            if capability == Capability.DELETE_FILE:
                # Destructive deletion ALWAYS requires Creator mode even in approved workspace
                return principal.is_creator

        # Outside approved workspace or when no path is provided:
        policy = self.policy_for(capability)
        return _mode_satisfies(principal.mode, policy.minimum_mode)

    def require(
        self,
        principal: Principal,
        capability: Capability,
        *,
        path: str | Path | None = None,
    ) -> None:
        """Raise :class:`PermissionDeniedError` unless allowed."""
        if self.check(principal, capability, path=path):
            return

        policy = self.policy_for(capability)

        if path is not None:
            in_ws = self._workspace_policy.is_in_workspace(path)
            if not in_ws:
                detail = (
                    f"capability {capability.value!r} denied: path '{path}' is outside approved project workspaces. "
                    f"Accessing personal or system files outside approved workspaces requires "
                    f"Creator mode (current: {principal.mode.value}). "
                    f"To authorize: authenticate as Creator (via PIN or registered device) "
                    f"or add the directory to approved workspaces."
                )
            elif capability == Capability.DELETE_FILE:
                detail = (
                    f"capability {capability.value!r} denied: deleting files requires Creator mode "
                    f"(current: {principal.mode.value}). Deletion is a destructive operation that "
                    f"cannot be performed in Restricted mode. "
                    f"To authorize: authenticate as Creator (via PIN or registered device)."
                )
            else:
                detail = (
                    f"capability {capability.value!r} requires {policy.minimum_mode.value} mode "
                    f"(current: {principal.mode.value})"
                )
        else:
            detail = (
                f"capability {capability.value!r} requires {policy.minimum_mode.value} mode "
                f"(current: {principal.mode.value})"
            )

        logger.warning(
            "permission denied",
            extra={
                "event": "PERMISSION_DENIED",
                "capability": capability.value,
                "principal_mode": principal.mode.value,
                "required_mode": policy.minimum_mode.value,
                "path": str(path) if path is not None else None,
            },
        )
        raise PermissionDeniedError(detail)

    # -- confirmation ------------------------------------------------------
    def assert_confirmation(
        self,
        principal: Principal,
        capability: Capability,
        *,
        confirmed: bool,
        required: bool = False,
    ) -> None:
        """Raise :class:`ConfirmationRequiredError` if confirmation is missing.

        The effective requirement is:

            policy.requires_confirmation(capability) OR required

        so ``required=True`` can only *add* a confirmation step. It can never
        disable one the policy table has imposed. This is the property that
        makes it safe for a tool to declare its own confirmation needs.
        """
        needs_confirmation = (
            self.requires_confirmation(capability) or required
        )
        if not needs_confirmation:
            return
        if confirmed:
            return
        logger.info(
            "confirmation required",
            extra={
                "event": "CONFIRMATION_REQUIRED",
                "capability": capability.value,
                "principal_mode": principal.mode.value,
                "source": "tool" if required else "policy",
            },
        )
        raise ConfirmationRequiredError(
            f"capability {capability.value!r} requires explicit confirmation"
        )
        
def _mode_satisfies(actual: PermissionMode, minimum: PermissionMode) -> bool:
    if minimum is PermissionMode.RESTRICTED:
        return True
    return actual is PermissionMode.CREATOR