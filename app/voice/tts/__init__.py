"""Text-to-speech subsystem public API.

    from app.voice.tts import TTSManager, SpeechOutcome, build_tts_manager

The manager guarantees the property the spec calls out explicitly: if
TTS fails, the text response is still available to the caller.
"""

from app.voice.tts.base import (
    SynthesisResult,
    TextToSpeechProvider,
    VoiceGender,
    VoiceProfile,
)
from app.voice.tts.manager import SpeechOutcome, TTSManager, build_tts_manager
from app.voice.tts.null import NullTextToSpeechProvider
from app.voice.tts.providers import build_tts_registry

__all__ = [
    "NullTextToSpeechProvider",
    "SpeechOutcome",
    "SynthesisResult",
    "TTSManager",
    "TextToSpeechProvider",
    "VoiceGender",
    "VoiceProfile",
    "build_tts_manager",
    "build_tts_registry",
]