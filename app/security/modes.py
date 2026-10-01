"""Permission modes, assurance levels, and the Principal type.

Three ideas, kept deliberately separate:

* :class:`PermissionMode` — *what* the actor is allowed to do.
* :class:`AssuranceLevel` — *how well* we know who the actor is.
* :class:`Principal` — the immutable snapshot produced by authentication.

Keeping mode and assurance apart is what makes "voice alone cannot grant
creator" expressible as a policy instead of scattered conditionals: the
voice method has LOW assurance, and creator mode requires HIGH.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class PermissionMode(str, Enum):
    """Authorization levels. Subclasses ``str`` for clean serialisation."""

    RESTRICTED = "restricted"
    CREATOR = "creator"


class AssuranceLevel(str, Enum):
    """How strongly the identity behind a Principal was proven.

    * ``LOW``    — a claim, nothing more. Voice, or no authentication at all.
    * ``MEDIUM`` — reserved for future second-factor methods.
    * ``HIGH``   — a per-installation secret (PIN) or proof of physical
      possession (a registered device).

    Creator mode requires ``HIGH``. A LOW-assurance method can never produce
    a creator Principal, regardless of which method name it is.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated actor behind the current request.

    Immutable on purpose: authentication produces a new Principal, it does
    not mutate one. That makes the security state trivial to reason about
    and safe to hand to background tasks.
    """

    user_id: str | None = None
    mode: PermissionMode = PermissionMode.RESTRICTED
    assurance: AssuranceLevel = AssuranceLevel.LOW
    methods: tuple[str, ...] = ()
    authenticated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PermissionMode):
            object.__setattr__(self, "mode", PermissionMode(self.mode))
        if not isinstance(self.assurance, AssuranceLevel):
            object.__setattr__(self, "assurance", AssuranceLevel(self.assurance))

    # -- constructors ------------------------------------------------------
    @classmethod
    def anonymous(cls) -> "Principal":
        """The default: restricted mode, no user, no method."""
        return cls()

    # -- queries -----------------------------------------------------------
    @property
    def is_creator(self) -> bool:
        return self.mode is PermissionMode.CREATOR

    def describe(self) -> dict[str, object]:
        """Log-safe summary. A Principal never carries secrets."""
        return {
            "user_id": self.user_id,
            "mode": self.mode.value,
            "assurance": self.assurance.value,
            "methods": list(self.methods),
            "authenticated_at": self.authenticated_at.isoformat(),
        }