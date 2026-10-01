"""Wake-word detectors.

Three concrete detectors and one factory:

* :class:`ManualWakeWordDetector` — fires on an external signal.
* :class:`TranscriptionWakeWordDetector` — fires when an STT provider
  recognizes the phrase in a rolling audio buffer.
* :class:`NullWakeWordDetector` — never fires; the default.
* :func:`build_wake_word_detector` — construct from settings.

None of these send audio to a language model. The wake-word stage is
local-only by design (see STEP 12).
"""

from app.voice.wake.base import WakeWordDetector
from app.voice.wake.factory import build_wake_word_detector
from app.voice.wake.manual import ManualWakeWordDetector
from app.voice.wake.null import NullWakeWordDetector
from app.voice.wake.transcription import (
    TranscriptionWakeWordDetector,
    matches_phrase,
)

__all__ = [
    "ManualWakeWordDetector",
    "NullWakeWordDetector",
    "TranscriptionWakeWordDetector",
    "WakeWordDetector",
    "build_wake_word_detector",
    "matches_phrase",
]