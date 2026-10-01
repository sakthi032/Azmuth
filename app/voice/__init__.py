"""Voice subsystem public API.

    from app.voice import build_voice_input_manager, UserInput, InputManager
    from app.voice import build_tts_manager, TTSManager, SpeechOutcome

The brain consumes :class:`UserInput` objects and never sees audio.
Voice and text converge at :class:`InputManager`, which is why a voice
failure cannot block text input. On the output side, :class:`TTSManager`
guarantees the response text is preserved regardless of TTS status.
"""

from app.voice.frames import AudioFrame
from app.voice.input_manager import InputManager, InputSource, UserInput
from app.voice.manager import VoiceInputManager, build_voice_input_manager
from app.voice.sources import AudioSource, NullAudioSource
from app.voice.stt import NullSpeechToTextProvider, SpeechToTextProvider
from app.voice.transcript import Transcript
from app.voice.tts import (
    NullTextToSpeechProvider,
    SpeechOutcome,
    SynthesisResult,
    TTSManager,
    TextToSpeechProvider,
    VoiceGender,
    VoiceProfile,
    build_tts_manager,
    build_tts_registry,
)
from app.voice.wake import (
    ManualWakeWordDetector,
    NullWakeWordDetector,
    TranscriptionWakeWordDetector,
    WakeWordDetector,
    build_wake_word_detector,
    matches_phrase,
)

__all__ = [
    "AudioFrame",
    "AudioSource",
    "InputManager",
    "InputSource",
    "ManualWakeWordDetector",
    "NullAudioSource",
    "NullSpeechToTextProvider",
    "NullTextToSpeechProvider",
    "NullWakeWordDetector",
    "SpeechOutcome",
    "SpeechToTextProvider",
    "SynthesisResult",
    "TTSManager",
    "TextToSpeechProvider",
    "Transcript",
    "TranscriptionWakeWordDetector",
    "UserInput",
    "VoiceGender",
    "VoiceInputManager",
    "VoiceProfile",
    "WakeWordDetector",
    "build_tts_manager",
    "build_tts_registry",
    "build_voice_input_manager",
    "build_wake_word_detector",
    "matches_phrase",
]