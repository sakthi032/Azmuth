"""Device-fingerprint authentication.

The idea: a "creator device" is registered by storing its fingerprint. When
Azmuth runs on that device, proving physical possession of the machine is
equivalent to HIGH assurance.

Phase 1 fingerprint source: :func:`platform.node` (the machine's network
name) by default. This is not a cryptographic device identifier — a
sufficiently motivated attacker can spoof it. It is documented as a Phase 1
approximation; a real implementation would use TPM-backed attestation or a
signed installation secret.
"""

from __future__ import annotations

import platform
from collections.abc import Iterable
from typing import Final

from app.security.base import AuthenticationMethod, AuthenticationResult
from app.security.modes import AssuranceLevel

_DEFAULT_USER_ID: Final[str] = "creator"


def current_device_id() -> str:
    """Best-effort, non-cryptographic device fingerprint for Phase 1."""
    return platform.node().strip()


class DeviceMethod(AuthenticationMethod):
    """Verify that the current device is a registered creator device."""

    name = "device"

    def __init__(
        self,
        *,
        registered_ids: Iterable[str] = (),
        current_id: str | None = None,
        user_id: str = _DEFAULT_USER_ID,
    ) -> None:
        self._registered = frozenset(
            value.strip() for value in registered_ids if value and value.strip()
        )
        self._current_id = current_id if current_id is not None else current_device_id()
        self._user_id = user_id

    @property
    def assurance(self) -> AssuranceLevel:
        return AssuranceLevel.HIGH

    def is_available(self) -> bool:
        return bool(self._registered)

    def verify(self, **credentials: object) -> AuthenticationResult:
        # The credential is *implicit* — the environment the process runs in.
        # Callers may override it with ``device_id`` for testing only.
        supplied = credentials.get("device_id")
        device_id = supplied if isinstance(supplied, str) and supplied else self._current_id

        if not self._registered:
            return AuthenticationResult(
                success=False,
                method=self.name,
                assurance=self.assurance,
                reason="no creator devices are registered",
            )
        if not device_id:
            return AuthenticationResult(
                success=False,
                method=self.name,
                assurance=self.assurance,
                reason="could not determine current device id",
            )
        if device_id not in self._registered:
            return AuthenticationResult(
                success=False,
                method=self.name,
                assurance=self.assurance,
                reason="current device is not a registered creator device",
                details={"current_id": device_id},
            )
        return AuthenticationResult(
            success=True,
            method=self.name,
            assurance=self.assurance,
            user_id=self._user_id,
            details={"current_id": device_id},
        )