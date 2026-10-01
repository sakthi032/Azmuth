"""Speech-to-text providers."""

from app.voice.stt.base import SpeechToTextProvider
from app.voice.stt.null import NullSpeechToTextProvider

__all__ = ["NullSpeechToTextProvider", "SpeechToTextProvider"]