"""Audio sources."""

from app.voice.sources.base import AudioSource
from app.voice.sources.null import NullAudioSource

__all__ = ["AudioSource", "NullAudioSource"]