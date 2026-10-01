"""Build the configured wake-word detector from :class:`Settings`.

The factory is the single place that reads ``WAKE_WORD_PROVIDER`` and
``WAKE_WORD_PHRASE``. Adding a new detector is:

1. write the class under ``app/voice/wake/``,
2. add one branch to :func:`build_wake_word_detector`.

Nothing else — the manager, the pipeline, and the brain do not change.
"""

from __future__ import annotations

from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.voice.stt.base import SpeechToTextProvider
from app.voice.wake.base import WakeWordDetector
from app.voice.wake.manual import ManualWakeWordDetector
from app.voice.wake.null import NullWakeWordDetector
from app.voice.wake.transcription import TranscriptionWakeWordDetector

logger = get_logger("voice.wake")


def build_wake_word_detector(
    settings: Settings,
    *,
    stt: SpeechToTextProvider | None = None,
) -> WakeWordDetector:
    """Construct the wake-word detector configured in ``settings``.

    ``stt`` is only consulted when the configured provider is
    ``"transcription"``. When it is omitted, the detector is still
    constructed — it simply reports unavailable, and the voice pipeline
    behaves as if wake-word detection is off.
    """
    provider = (settings.wake_word_provider or "null").strip().lower()
    phrase = (settings.wake_word_phrase or "azmuth").strip() or "azmuth"

    if provider in ("", "null", "none"):
        return NullWakeWordDetector(phrase=phrase)

    if provider == "manual":
        return ManualWakeWordDetector(phrase=phrase)

    if provider == "transcription":
        return TranscriptionWakeWordDetector(stt=stt, phrase=phrase)

    logger.warning(
        "unknown wake word provider; falling back to null",
        extra={
            "event": "WAKE_PROVIDER_UNKNOWN",
            "provider": provider,
        },
    )
    return NullWakeWordDetector(phrase=phrase)