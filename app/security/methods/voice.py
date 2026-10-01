"""Voice authentication — Phase 1 stub.

The architecture supports a real voice-authentication method: it would
return ``AuthenticationResult(success=True, assurance=AssuranceLevel.LOW)``
on a speaker-verified sample, and the Authenticator would still refuse to
grant CREATOR mode because creator mode requires HIGH assurance.

This class exists so that:

* the security subsystem boots with a placeholder that can never silently
  grant dangerous permissions,
* the interface it will implement is fixed and testable,
* callers cannot assume a voice matcher is configured.

It reports ``is_available() = False`` and ``verify()`` always returns a
failure with a clear reason. That is honest: Phase 1 does not ship a speaker
verification engine.
"""

from __future__ import annotations

from app.security.base import AuthenticationMethod, AuthenticationResult
from app.security.modes import AssuranceLevel


class VoiceMethod(AuthenticationMethod):
    """Speaker-verification stub. Never available in Phase 1."""

    name = "voice"

    @property
    def assurance(self) -> AssuranceLevel:
        # LOW by design. Even a *working* voice matcher would be LOW, so
        # that voice alone cannot produce a creator Principal.
        return AssuranceLevel.LOW

    def is_available(self) -> bool:
        return False

    def verify(self, **credentials: object) -> AuthenticationResult:
        return AuthenticationResult(
            success=False,
            method=self.name,
            assurance=self.assurance,
            reason="voice authentication is not implemented in Phase 1",
        )