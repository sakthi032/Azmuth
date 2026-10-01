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

from app.config.logging_setup import get_logger
from app.core.errors import ConfirmationRequiredError, PermissionDeniedError
from app.security.capabilities import DEFAULT_POLICY, Capability, CapabilityPolicy
from app.security.modes import PermissionMode, Principal

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
        self, policy: Mapping[Capability, CapabilityPolicy] | None = None
    ) -> None:
        self._policy: dict[Capability, CapabilityPolicy] = dict(
            policy if policy is not None else DEFAULT_POLICY
        )
        for capability in Capability:
            self._policy.setdefault(capability, _DENY_ALL)

    # -- inspection --------------------------------------------------------
    def policy_for(self, capability: Capability) -> CapabilityPolicy:
        return self._policy.get(capability, _DENY_ALL)

    def requires_confirmation(self, capability: Capability) -> bool:
        return self.policy_for(capability).requires_confirmation

    # -- authorization -----------------------------------------------------
    def check(self, principal: Principal, capability: Capability) -> bool:
        """True if ``principal`` may perform ``capability`` at all."""
        policy = self.policy_for(capability)
        return _mode_satisfies(principal.mode, policy.minimum_mode)

    def require(self, principal: Principal, capability: Capability) -> None:
        """Raise :class:`PermissionDeniedError` unless allowed."""
        if self.check(principal, capability):
            return
        policy = self.policy_for(capability)
        logger.warning(
            "permission denied",
            extra={
                "event": "PERMISSION_DENIED",
                "capability": capability.value,
                "principal_mode": principal.mode.value,
                "required_mode": policy.minimum_mode.value,
            },
        )
        raise PermissionDeniedError(
            f"capability {capability.value!r} requires "
            f"{policy.minimum_mode.value} mode "
            f"(current: {principal.mode.value})"
        )

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