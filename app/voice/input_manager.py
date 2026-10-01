"""The convergence point for voice and text.

Every user turn — however it arrived — becomes a :class:`UserInput`. The
brain will accept ``UserInput`` objects without caring about the source.
That is what makes the spec's requirement, "only the input layer should
differ," a mechanical property of the code rather than a convention.

Language resolution happens here, once, for both sources. It uses the
STEP 4 :class:`~app.language.manager.LanguageManager` and honors the
session language preference passed by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.language.manager import LanguageManager
from app.voice.transcript import Transcript


class InputSource(str, Enum):
    """Where this input came from. Subclasses ``str`` for serialisation."""

    TEXT = "text"
    VOICE = "voice"


@dataclass(frozen=True, slots=True)
class UserInput:
    """One turn of user input, ready for the brain.

    ``text`` is always populated. ``language`` is the *resolved* response
    language (STEP 4's policy applied), not a raw detection — the brain
    can use it to render responses in the right language without
    re-detecting.
    """

    text: str
    language: str
    source: InputSource
    confidence: float = 1.0
    transcript: Transcript | None = field(default=None, repr=False)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "language": self.language,
            "source": self.source.value,
            "confidence": round(self.confidence, 3),
        }


class InputManager:
    """Turn text or a transcript into a :class:`UserInput`.

    Stateless with respect to any given conversation: the session
    language is a per-call argument, matching how :class:`SessionManager`
    (STEP 5) stores it. The manager holds only the language manager, so
    it can be shared freely across sessions.
    """

    def __init__(self, language: LanguageManager) -> None:
        self._language = language

    # -- inputs ------------------------------------------------------------
    def from_text(
        self, text: str, *, session_language: str | None = None
    ) -> UserInput:
        """Build a :class:`UserInput` from a typed message."""
        resolved = self._language.resolve_response_language(
            text, session_language=session_language
        )
        return UserInput(
            text=text,
            language=resolved,
            source=InputSource.TEXT,
            confidence=1.0,
            transcript=None,
        )

    def from_transcript(
        self,
        transcript: Transcript,
        *,
        session_language: str | None = None,
    ) -> UserInput:
        """Build a :class:`UserInput` from a speech recognition result.

        The transcript's own ``language`` field is *ignored* for the
        language decision — a recognizer's hint cannot be trusted more
        than the detection policy the rest of the app uses. What the
        transcript *does* provide is the text and the confidence score.
        """
        resolved = self._language.resolve_response_language(
            transcript.text, session_language=session_language
        )
        return UserInput(
            text=transcript.text,
            language=resolved,
            source=InputSource.VOICE,
            confidence=transcript.confidence if transcript.confidence > 0 else 1.0,
            transcript=transcript,
        )