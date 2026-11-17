"""Audio sources."""

from __future__ import annotations

from app.config.logging_setup import get_logger
from app.voice.sources.base import AudioSource
from app.voice.sources.microphone import MicrophoneAudioSource
from app.voice.sources.null import NullAudioSource

logger = get_logger("voice.sources")


def build_audio_source(settings) -> AudioSource:
    """Construct the audio source named in settings."""
    name = (getattr(settings, "audio_source", "microphone") or "microphone").lower()
    if name in ("", "null", "none"):
        return NullAudioSource()

    if name == "microphone":
        try:
            source = MicrophoneAudioSource(
                sample_rate=getattr(settings, "audio_sample_rate", 16_000),
                channels=getattr(settings, "audio_channels", 1),
                block_duration_ms=getattr(settings, "audio_block_ms", 100),
                device=getattr(settings, "audio_device", None) or None,
                silence_rms=getattr(settings, "audio_silence_rms", 80.0),
            )
            if source.is_available():
                logger.info(
                    "audio source ready",
                    extra={
                        "event": "AUDIO_SOURCE_READY",
                        "source": source.name,
                    },
                )
                return source
            logger.warning(
                "microphone not available — falling back to null source",
                extra={"event": "AUDIO_MIC_UNAVAILABLE"},
            )
            return NullAudioSource()
        except Exception:
            logger.exception(
                "failed to build microphone source",
                extra={"event": "AUDIO_BUILD_FAILED"},
            )
            return NullAudioSource()

    logger.warning(
        "unknown audio source %r — using null", name,
        extra={"event": "AUDIO_SOURCE_UNKNOWN"},
    )
    return NullAudioSource()


__all__ = [
    "AudioSource",
    "MicrophoneAudioSource",
    "NullAudioSource",
    "build_audio_source",
]