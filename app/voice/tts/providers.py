"""TTS provider registry factory.

Ships with just the null provider. Real backends — Windows SAPI via
``comtypes``, ``pyttsx3``, ``piper`` for offline synthesis, an HTTP call
to a cloud engine, a custom Azmuth voice — register here. The manager,
the brain, and the UI do not change when a new one is added.
"""

from __future__ import annotations

from app.core.registry import Registry
from app.voice.tts.base import TextToSpeechProvider
from app.voice.tts.null import NullTextToSpeechProvider


def build_tts_registry() -> Registry[TextToSpeechProvider]:
    registry: Registry[TextToSpeechProvider] = Registry("TTS")
    registry.register(NullTextToSpeechProvider.name, NullTextToSpeechProvider)
    return registry


__all__ = ["NullTextToSpeechProvider", "build_tts_registry"]