"""Security subsystem public API.

    from app.security import (
        Authenticator, PermissionManager, Principal, Capability,
    )

Concrete authentication methods live under ``app.security.methods``;
callers reach them only through :class:`Authenticator`.
"""

from app.security.authenticator import CREATOR_REQUIRED_ASSURANCE, Authenticator
from app.security.base import AuthenticationMethod, AuthenticationResult
from app.security.capabilities import DEFAULT_POLICY, Capability, CapabilityPolicy
from app.security.methods import (
    PINMethod,
    CreatorCredentialStore,
    DeviceMethod,
    VoiceMethod,
    build_default_methods,
)
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager

__all__ = [
    "AssuranceLevel",
    "AuthenticationMethod",
    "AuthenticationResult",
    "Authenticator",
    "CREATOR_REQUIRED_ASSURANCE",
    "Capability",
    "CapabilityPolicy",
    "CreatorCredentialStore",
    "DEFAULT_POLICY",
    "DeviceMethod",
    "PINMethod",
    "PermissionManager",
    "PermissionMode",
    "Principal",
    "VoiceMethod",
    "build_default_methods",
]