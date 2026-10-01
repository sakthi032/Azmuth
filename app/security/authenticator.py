"""The Authenticator: turn credentials into a Principal.

The Authenticator owns three things:

* the set of registered :class:`AuthenticationMethod` objects,
* the *current* :class:`Principal` (who is logged in right now, if anyone),
* failure counters per method — a simple lockout that makes brute-forcing a
  PIN from the assistant's own input surface impractical.

Mode is decided by policy, not by method identity:

    required assurance for CREATOR = HIGH
    required assurance for RESTRICTED = LOW

A successful authentication *upgrades* the current Principal. A failed one
changes nothing — a bad PIN on an already-authenticated session does not
downgrade it. ``logout()`` resets to :meth:`Principal.anonymous`.

Phase 1 assumes one user at the keyboard. There is exactly one current
Principal per process; multi-user concurrency is out of scope.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from app.config.logging_setup import get_logger
from app.core.errors import AuthenticationError
from app.security.base import AuthenticationMethod
from app.security.modes import AssuranceLevel, PermissionMode, Principal

logger = get_logger("security.authenticator")

#: Creator mode requires this assurance. Defined here, in one place, so
#: changing the policy is a one-line edit and every method obeys it.
CREATOR_REQUIRED_ASSURANCE: AssuranceLevel = AssuranceLevel.HIGH

#: Rate limiting: after this many failed attempts within the window, a
#: method is locked out until the window slides forward.
_MAX_FAILURES: int = 5
_FAILURE_WINDOW: timedelta = timedelta(minutes=1)


class Authenticator:
    """Registers methods and produces the current Principal."""

    def __init__(self) -> None:
        self._methods: dict[str, AuthenticationMethod] = {}
        self._current: Principal = Principal.anonymous()
        self._failures: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = threading.RLock()

    # -- registration ------------------------------------------------------
    def register(self, method: AuthenticationMethod) -> None:
        with self._lock:
            self._methods[method.name] = method

    def register_all(self, methods: Iterable[AuthenticationMethod]) -> None:
        for method in methods:
            self.register(method)

    def method_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._methods))

    def method(self, name: str) -> AuthenticationMethod:
        try:
            return self._methods[name]
        except KeyError as exc:
            raise AuthenticationError(
                f"authentication method {name!r} is not registered"
            ) from exc

    def available_methods(self) -> tuple[str, ...]:
        return tuple(
            name for name, m in sorted(self._methods.items()) if m.is_available()
        )

    # -- current principal -------------------------------------------------
    @property
    def current_principal(self) -> Principal:
        return self._current

    def logout(self) -> Principal:
        with self._lock:
            previous = self._current
            self._current = Principal.anonymous()
        logger.info(
            "logged out",
            extra={
                "event": "AUTHENTICATION_CHANGED",
                "from_mode": previous.mode.value,
                "to_mode": self._current.mode.value,
            },
        )
        return self._current

    # -- authentication ----------------------------------------------------
    def authenticate(self, method_name: str, **credentials: object) -> Principal:
        """Run ``method_name`` with the supplied credentials.

        On success, returns the (possibly upgraded) current Principal.
        Raises :class:`AuthenticationError` on any failure — unknown method,
        unavailable method, lockout, or bad credentials.
        """
        method = self.method(method_name)

        if self._is_locked_out(method_name):
            raise AuthenticationError(
                f"method {method_name!r} is temporarily locked out after "
                f"{_MAX_FAILURES} failed attempts"
            )
        if not method.is_available():
            raise AuthenticationError(
                f"method {method_name!r} is not available on this system"
            )

        result = method.verify(**credentials)
        if not result.success:
            self._record_failure(method_name)
            raise AuthenticationError(
                result.reason or f"{method_name} authentication failed"
            )

        self._clear_failures(method_name)
        with self._lock:
            upgraded = self._upgrade(self._current, result.method, result.assurance, result.user_id)
            self._current = upgraded

        logger.info(
            "authenticated",
            extra={
                "event": "AUTHENTICATION_CHANGED",
                "method": result.method,
                "assurance": result.assurance.value,
                "from_mode": self._current.mode.value,
                "to_mode": upgraded.mode.value,
            },
        )
        return upgraded

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _mode_for(assurance: AssuranceLevel) -> PermissionMode:
        return (
            PermissionMode.CREATOR
            if assurance.rank >= CREATOR_REQUIRED_ASSURANCE.rank
            else PermissionMode.RESTRICTED
        )

    def _upgrade(
        self,
        current: Principal,
        method_name: str,
        assurance: AssuranceLevel,
        user_id: str | None,
    ) -> Principal:
        # New assurance is the strongest one seen so far in this session.
        new_assurance = (
            assurance
            if assurance.rank > current.assurance.rank
            else current.assurance
        )
        new_mode = self._mode_for(new_assurance)

        # user_id: prefer the one from the highest-assurance method so far.
        new_user = current.user_id
        if assurance.rank >= current.assurance.rank and user_id:
            new_user = user_id

        new_methods = current.methods
        if method_name not in new_methods:
            new_methods = (*new_methods, method_name)

        return Principal(
            user_id=new_user,
            mode=new_mode,
            assurance=new_assurance,
            methods=new_methods,
        )

    def _is_locked_out(self, method_name: str) -> bool:
        cutoff = datetime.now(timezone.utc) - _FAILURE_WINDOW
        failures = self._failures[method_name]
        while failures and failures[0] < cutoff:
            failures.popleft()
        return len(failures) >= _MAX_FAILURES

    def _record_failure(self, method_name: str) -> None:
        self._failures[method_name].append(datetime.now(timezone.utc))

    def _clear_failures(self, method_name: str) -> None:
        self._failures.pop(method_name, None)