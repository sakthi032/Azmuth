"""The TTS façade.

Two responsibilities:

* Resolve a provider from settings and expose its availability.
* Enforce the spec's invariant: **a TTS failure never loses the text
  response**. The manager always has the text in hand and returns it
  regardless of what happened during synthesis or playback.

The invariant is structural, not hopeful: :meth:`TTSManager.speak_response`
takes the response text, attempts to speak it, and returns a
:class:`SpeechOutcome` whose ``text`` field is *always* the original
response. Callers cannot accidentally drop the text on a TTS error
because they never receive anything else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.errors import ProviderNotFoundError
from app.core.registry import Registry
from app.voice.tts.base import (
    SynthesisResult,
    TextToSpeechProvider,
    VoiceProfile,
)

logger = get_logger("voice.tts")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SpeechOutcome:
    """Result of attempting to speak a response.

    ``text`` is guaranteed present — that is the entire point of this
    type. ``spoken`` tells the caller whether audio was actually
    delivered; it does not gate access to the text.
    """

    text: str
    spoken: bool
    provider: str
    language: str | None = None
    error: str | None = None
    elapsed_ms: float = 0.0
    attempted_at: datetime = field(default_factory=_now)

    @property
    def is_silent(self) -> bool:
        """True when the caller should treat the response as text-only."""
        return not self.spoken

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "spoken": self.spoken,
            "provider": self.provider,
            "language": self.language,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


class TTSManager:
    """Provider selection and speech scheduling."""

    def __init__(
        self,
        provider_name: str,
        registry: Registry[TextToSpeechProvider],
    ) -> None:
        self._provider_name = provider_name
        self._registry = registry
        self._provider: TextToSpeechProvider | None = None

    # -- introspection -----------------------------------------------------
    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def provider(self) -> TextToSpeechProvider:
        if self._provider is None:
            self._provider = self._registry.create(self._provider_name)
            logger.info(
                "TTS provider resolved",
                extra={
                    "event": "TTS_PROVIDER_READY",
                    "provider": self._provider.name,
                },
            )
        return self._provider

    def is_available(self) -> bool:
        """Never raises. A provider that fails to build is unavailable."""
        try:
            return self.provider.is_available()
        except ProviderNotFoundError:
            return False
        except Exception:  # noqa: BLE001 - availability must never raise
            logger.exception(
                "TTS availability check failed",
                extra={"event": "TTS_AVAILABILITY_FAILED"},
            )
            return False

    def reset(self) -> None:
        """Forget the cached provider. Used by tests and settings reload."""
        self._provider = None

    # -- voice discovery ---------------------------------------------------
    def list_voices(self, language: str | None = None) -> tuple[VoiceProfile, ...]:
        try:
            return self.provider.list_voices(language)
        except Exception:  # noqa: BLE001
            logger.exception(
                "listing voices failed",
                extra={"event": "TTS_LIST_VOICES_FAILED"},
            )
            return ()

    def supports_language(self, language: str) -> bool:
        try:
            return self.provider.supports_language(language)
        except Exception:  # noqa: BLE001
            logger.exception(
                "language support check failed",
                extra={"event": "TTS_LANGUAGE_CHECK_FAILED"},
            )
            return False

    # -- speech ------------------------------------------------------------
    async def speak_response(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> SpeechOutcome:
        """Attempt to speak ``text`` and return an outcome.

        Never raises. Never loses the text. On any failure — provider
        unavailable, synthesis error, playback refused — the returned
        :class:`SpeechOutcome` carries the original ``text`` with
        ``spoken=False`` and a descriptive ``error``.
        """
        if not text or not text.strip():
            return SpeechOutcome(
                text=text,
                spoken=False,
                provider=self._provider_name,
                language=language,
                error="nothing to speak",
            )

        if not self.is_available():
            logger.info(
                "TTS unavailable; returning text only",
                extra={
                    "event": "TTS_UNAVAILABLE",
                    "provider": self._provider_name,
                },
            )
            return SpeechOutcome(
                text=text,
                spoken=False,
                provider=self._provider_name,
                language=language,
                error="text-to-speech is not available",
            )

        started = time.perf_counter()
        try:
            spoken = await self.provider.speak(
                text, language=language, voice_id=voice_id
            )
        except Exception as exc:  # noqa: BLE001 - the invariant must hold
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "TTS speak failed",
                extra={"event": "TTS_SPEAK_FAILED", "provider": self._provider_name},
            )
            return SpeechOutcome(
                text=text,
                spoken=False,
                provider=self._provider_name,
                language=language,
                error=str(exc),
                elapsed_ms=elapsed_ms,
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        outcome = SpeechOutcome(
            text=text,
            spoken=bool(spoken),
            provider=self._provider_name,
            language=language,
            elapsed_ms=elapsed_ms,
            error=None if spoken else "provider reported no audio delivered",
        )
        logger.info(
            "TTS speak completed",
            extra={
                "event": "TTS_SPOKEN",
                "provider": self._provider_name,
                "spoken": outcome.spoken,
                "elapsed_ms": round(elapsed_ms, 3),
            },
        )
        return outcome

    async def synthesize(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> SynthesisResult:
        """Produce audio without playing it.

        Unlike :meth:`speak_response`, this returns a
        :class:`SynthesisResult` which may be empty (no audio). It still
        never raises: callers use ``result.succeeded`` to distinguish.
        """
        if not text or not text.strip():
            return SynthesisResult(
                text=text,
                audio=None,
                provider=self._provider_name,
                language=language,
                error="nothing to synthesize",
            )

        if not self.is_available():
            return SynthesisResult(
                text=text,
                audio=None,
                provider=self._provider_name,
                language=language,
                error="text-to-speech is not available",
            )

        started = time.perf_counter()
        try:
            result = await self.provider.synthesize(
                text, language=language, voice_id=voice_id
            )
        except Exception as exc:  # noqa: BLE001 - never lose the text
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "TTS synthesize failed",
                extra={
                    "event": "TTS_SYNTHESIZE_FAILED",
                    "provider": self._provider_name,
                },
            )
            return SynthesisResult(
                text=text,
                audio=None,
                provider=self._provider_name,
                language=language,
                voice_id=voice_id,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )

        # Defensive: a buggy provider might return a result whose ``text``
        # does not match the input. Rewrite it so the invariant "the
        # result always carries the original text" holds regardless.
        return SynthesisResult(
            text=text,
            audio=result.audio,
            provider=result.provider or self._provider_name,
            language=result.language if result.language is not None else language,
            voice_id=result.voice_id if result.voice_id is not None else voice_id,
            elapsed_ms=result.elapsed_ms or (time.perf_counter() - started) * 1000,
            error=result.error,
            started_at=result.started_at,
        )


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def build_tts_manager(
    settings: Settings,
    *,
    registry: Registry[TextToSpeechProvider] | None = None,
) -> TTSManager:
    """Construct a :class:`TTSManager` using the configured provider.

    Uses the built-in registry (which today contains only the null
    provider). Real providers register on the same registry; the manager
    does not change.
    """
    from app.voice.tts.providers import build_tts_registry

    return TTSManager(
        settings.tts_provider or "null",
        registry if registry is not None else build_tts_registry(),
    )


__all__ = ["SpeechOutcome", "TTSManager", "build_tts_manager"]