"""TTS provider registry factory."""

from __future__ import annotations

from app.core.registry import Registry
from app.voice.tts.base import TextToSpeechProvider
from app.voice.tts.edge_tts_provider import EdgeTTSProvider
from app.voice.tts.null import NullTextToSpeechProvider


def build_tts_registry() -> Registry[TextToSpeechProvider]:
    registry: Registry[TextToSpeechProvider] = Registry("TTS")
    registry.register(NullTextToSpeechProvider.name, NullTextToSpeechProvider)
    registry.register(EdgeTTSProvider.name, EdgeTTSProvider)
    return registry



__all__ = [
    "EdgeTTSProvider",
    "NullTextToSpeechProvider",
    "build_tts_registry",
]