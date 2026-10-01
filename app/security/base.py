"""The authentication method interface."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field

from app.core.interfaces import Provider
from app.security.modes import AssuranceLevel


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """What a method reports back to the Authenticator.

    A method only describes what it observed. It never decides the
    Principal's mode — that decision belongs to the Authenticator, which
    enforces the assurance policy uniformly across every method.
    """

    success: bool
    method: str
    assurance: AssuranceLevel
    user_id: str | None = None
    reason: str | None = None
    details: dict[str, object] = field(default_factory=dict)


class AuthenticationMethod(Provider):
    """Base class for every authentication method.

    Subclasses declare their :attr:`assurance` and implement
    :meth:`verify`. ``verify`` must never raise on an ordinary credential
    failure — return ``AuthenticationResult(success=False, reason=...)``.
    Exceptions are reserved for genuine programming errors.
    """

    @property
    @abstractmethod
    def assurance(self) -> AssuranceLevel:
        """The assurance level this method provides on success."""

    @abstractmethod
    def verify(self, **credentials: object) -> AuthenticationResult:
        """Check the supplied credentials.

        Credential keys are method-specific (``pin``, ``device_id``, …). A
        method must ignore keys it does not understand rather than raising.
        """