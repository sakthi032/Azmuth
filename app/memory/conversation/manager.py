"""Session lifecycle: create, look up, expire, delete.

Synchronous on purpose:

* Azmuth runs a single event loop, and every session mutation happens there.
* A lock inside this class would be ceremony without benefit.
* If background threads ever need session access, an *async wrapper* goes
  around this class rather than locking inside it.

Sessions are stored in a plain dict. Persistence of conversation archives is
long-term memory (STEP 6), not this.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import timedelta

from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.errors import SessionNotFoundError
from app.memory.conversation.session import Session

logger = get_logger("memory.conversation")


def _new_session_id() -> str:
    """Short, URL-safe, collision-resistant enough for a local assistant."""
    return secrets.token_urlsafe(12)


class SessionManager:
    """In-memory session store keyed by opaque session id."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, Session] = {}
        self._ttl = timedelta(minutes=settings.session_ttl_minutes)

    # -- lifecycle ---------------------------------------------------------
    def create(
        self,
        *,
        user_id: str | None = None,
        language: str | None = None,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> Session:
        sid = session_id or _new_session_id()
        if sid in self._sessions:
            raise ValueError(f"session {sid!r} already exists")

        max_turns = self._settings.session_max_turns
        session = Session(
            id=sid,
            user_id=user_id,
            language=language,
            metadata=dict(metadata or {}),
            max_turns=max_turns,
            history_limit=max(max_turns * 4, 40),
        )
        self._sessions[sid] = session
        logger.info(
            "session created",
            extra={
                "event": "SESSION_CREATED",
                "session_id": sid,
                "user_id": user_id,
            },
        )
        return session

    def get(self, session_id: str) -> Session:
        """Return the session or raise.

        Expired sessions are purged on access — this is the natural moment to
        notice them, and it keeps :meth:`list_ids` honest without a
        background sweeper.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"session {session_id!r} not found")
        if session.is_expired(self._ttl):
            self.delete(session_id)
            raise SessionNotFoundError(
                f"session {session_id!r} expired after "
                f"{self._settings.session_ttl_minutes} minutes of inactivity"
            )
        return session

    def get_or_create(
        self,
        session_id: str | None = None,
        *,
        user_id: str | None = None,
        language: str | None = None,
    ) -> Session:
        if session_id is None:
            return self.create(user_id=user_id, language=language)
        try:
            return self.get(session_id)
        except SessionNotFoundError:
            return self.create(
                session_id=session_id, user_id=user_id, language=language
            )

    def delete(self, session_id: str) -> bool:
        if self._sessions.pop(session_id, None) is None:
            return False
        logger.info(
            "session ended",
            extra={"event": "SESSION_ENDED", "session_id": session_id},
        )
        return True

    def clear(self) -> None:
        self._sessions.clear()

    # -- introspection -----------------------------------------------------
    def count(self) -> int:
        return len(self._sessions)

    def list_ids(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    def sessions(self) -> Iterable[Session]:
        return tuple(self._sessions.values())

    # -- maintenance -------------------------------------------------------
    def purge_expired(self) -> int:
        """Delete every session whose last activity exceeds the TTL.

        Returns the number purged. Safe to call periodically; also safe to
        never call, since :meth:`get` purges lazily.
        """
        expired = [
            sid for sid, session in self._sessions.items()
            if session.is_expired(self._ttl)
        ]
        for sid in expired:
            self.delete(sid)
        if expired:
            logger.info(
                "expired sessions purged",
                extra={"event": "SESSION_PURGED", "count": len(expired)},
            )
        return len(expired)