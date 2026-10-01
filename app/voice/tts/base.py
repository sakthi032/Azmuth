"""The text-to-speech interface and its result types.

Two methods, two use cases:

* :meth:`TextToSpeechProvider.synthesize` — produce audio without
  playing it. Used by the UI (to preview a voice, to cache), by tests,
  and by any future pipeline that wants to route audio somewhere other
  than the default speaker.
* :meth:`TextToSpeechProvider.speak` — synthesize and play through the
  system's default audio output. The convenience method the brain uses.

Splitting them is deliberate. A provider that can only do one of the two
is still useful; the manager will synthesize when it can and speak when
it can, and neither method's absence blocks the other.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.interfaces import Provider
from app.voice.frames import AudioFrame


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VoiceGender(str, Enum):
    """Advisory metadata for a voice profile.

    Purely descriptive — providers are free to ignore it. Exists so the
    UI can list available voices in a useful order and so a future
    "match this voice" feature has something to reason about.
    """

    NEUTRAL = "neutral"
    FEMININE = "feminine"
    MASCULINE = "masculine"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """A specific voice a provider can use.

    Custom Azmuth voice support lives entirely at this level: a provider
    that ships a custom model identifies it by :attr:`id`, describes it
    here, and reports it from
    :meth:`TextToSpeechProvider.list_voices`. Nothing else in the
    subsystem changes.
    """

    id: str
    name: str
    language: str
    gender: VoiceGender = VoiceGender.UNKNOWN
    sample_rate: int | None = None
    is_custom: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "gender": self.gender.value,
            "sample_rate": self.sample_rate,
            "is_custom": self.is_custom,
            "extra": dict(self.extra),
        }


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """Outcome of one synthesis call.

    ``audio`` is ``None`` when synthesis failed or the provider chose not
    to return the buffer (streaming-only providers). ``text`` is always
    the input — the manager relies on this to preserve the response text
    even when synthesis did not happen.
    """

    text: str
    audio: AudioFrame | None
    provider: str
    language: str | None = None
    voice_id: str | None = None
    elapsed_ms: float = 0.0
    error: str | None = None
    started_at: datetime = field(default_factory=_now)

    @property
    def is_empty(self) -> bool:
        return self.audio is None or self.audio.is_empty

    @property
    def succeeded(self) -> bool:
        return self.error is None and not self.is_empty

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "language": self.language,
            "voice_id": self.voice_id,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "error": self.error,
            "has_audio": self.audio is not None and not self.audio.is_empty,
        }


class TextToSpeechProvider(Provider):
    """Base class for every text-to-speech backend.

    Implementations must:

    * Never raise from :meth:`is_available`, :meth:`list_voices` or
      :meth:`supports_language`. These are probes; they answer, they do
      not fail.
    * Return a :class:`SynthesisResult` (with ``error`` set) rather than
      raising from :meth:`synthesize`. The manager relies on this to
      preserve the response text even when the provider is broken.
    * Return ``False`` from :meth:`speak` on failure instead of raising.
      Silence is a valid outcome; a crash is not.

    The manager wraps everything anyway, but providers that honor these
    contracts make the failure path trivial to reason about.
    """

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> SynthesisResult:
        """Produce audio for ``text`` without playing it."""

    @abstractmethod
    async def speak(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> bool:
        """Synthesize and play through the default output.

        Returns ``True`` if the audio was delivered to the OS. ``False``
        means the utterance was not heard for any reason — the caller
        must not treat that as a text-handling failure.
        """

    @abstractmethod
    def list_voices(self, language: str | None = None) -> tuple[VoiceProfile, ...]:
        """Voices this provider can use, optionally filtered by language.

        Empty tuple means "unknown or none". Never raises.
        """

    @abstractmethod
    def supports_language(self, language: str) -> bool:
        """Whether this provider can speak the given BCP-47 primary subtag.

        Providers whose language set is open-ended return ``True`` for
        anything non-empty. Never raises.
        """