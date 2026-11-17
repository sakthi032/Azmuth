"""Speech-to-text providers."""

from __future__ import annotations

from app.config.logging_setup import get_logger
from app.voice.stt.base import SpeechToTextProvider
from app.voice.stt.null import NullSpeechToTextProvider

logger = get_logger("voice.stt")


def build_stt_provider(settings) -> SpeechToTextProvider:
    """Construct the STT provider named in settings, or null on failure."""
    name = (getattr(settings, "stt_provider", "null") or "null").lower()
    if name in ("", "null", "none"):
        return NullSpeechToTextProvider()

    if name == "faster_whisper":
        try:
            from app.voice.stt.faster_whisper import (
                FasterWhisperSTTProvider,
            )
            provider = FasterWhisperSTTProvider(
                model_size=getattr(settings, "stt_model", "base"),
                device=getattr(settings, "stt_device", "cpu"),
                compute_type=getattr(settings, "stt_compute_type", "int8"),
                language=(
                    getattr(settings, "stt_language", None) or None
                ),
                beam_size=getattr(settings, "stt_beam_size", 1),
                vad_filter=getattr(settings, "stt_vad_filter", True),
                initial_prompt=getattr(settings, "stt_initial_prompt", None),
            )
            logger.info(
                "STT provider ready",
                extra={
                    "event": "STT_READY",
                    "provider": provider.name,
                    "model": provider.model_size,
                },
            )
            return provider
        except Exception:
            logger.exception(
                "failed to build faster_whisper STT",
                extra={"event": "STT_BUILD_FAILED"},
            )
            return NullSpeechToTextProvider()

    logger.warning(
        "unknown STT provider %r — using null", name,
        extra={"event": "STT_PROVIDER_UNKNOWN"},
    )
    return NullSpeechToTextProvider()


__all__ = [
    "FasterWhisperSTTProvider" if False else "SpeechToTextProvider",
    "NullSpeechToTextProvider",
    "SpeechToTextProvider",
    "build_stt_provider",
]