"""Wake-word detection via short rolling transcription.

Runs the configured STT provider on short audio chunks and looks for the
wake phrase in the resulting text. This is not a dedicated wake-word
engine — those are usually more efficient and more accurate — but it
works with any STT provider Azmuth already has, including the local one
that will be configured in a later step.

**Independence from the LLM** is preserved: the STT provider is a local
speech recognizer, not a language model. No audio leaves the machine via
this detector.

The detector maintains a rolling buffer:

* Frames accumulate until ``chunk_seconds`` of audio is available.
* The chunk is transcribed as a whole. Full-chunk transcription is what
  makes word-boundary matching reliable; per-frame transcription would
  split the phrase.
* On a match, the buffer is cleared and the detector returns True.
* On no match, only the trailing ``overlap_seconds`` of audio is retained
  so a phrase split across a chunk boundary still gets a chance to be
  recognized. The rest is discarded.

Failures from the STT provider are logged and swallowed. A detector that
crashed on transient STT errors would take down the voice pipeline; a
detector that reports "not detected" is simply silent for that chunk.
"""

from __future__ import annotations

import re

from app.config.logging_setup import get_logger
from app.voice.frames import AudioFrame
from app.voice.stt.base import SpeechToTextProvider
from app.voice.stt.null import NullSpeechToTextProvider
from app.voice.wake.base import WakeWordDetector

logger = get_logger("voice.wake.transcription")

#: Word tokenization used for phrase matching. ``\w`` with ``re.UNICODE``
#: matches Tamil and Latin letters alike, so the same matcher works for
#: both primary languages.
_WORDS = re.compile(r"\w+", re.UNICODE)


def matches_phrase(text: str, phrase: str) -> bool:
    """True if ``phrase`` appears in ``text`` as a whole word sequence.

    Matching is:

    * **case-insensitive** — "Azmuth", "azmuth", "AZMUTH" all match.
    * **punctuation-tolerant** — "azmuth," and "azmuth!" match.
    * **word-boundary** — "azmuthing" does NOT match; the phrase must be
      a sequence of whole words.
    * **subsequence-aware** — "Azmuth open Chrome" matches a phrase of
      "azmuth", and "hey Azmuth" matches a phrase of "hey azmuth".

    Empty input on either side returns ``False``. Pure function; no state.
    """
    if not text or not phrase:
        return False

    text_words = _WORDS.findall(text.lower())
    phrase_words = _WORDS.findall(phrase.lower())
    if not text_words or not phrase_words:
        return False

    phrase_len = len(phrase_words)
    if phrase_len == 1:
        return phrase_words[0] in text_words

    # Multi-word: contiguous subsequence.
    for index in range(len(text_words) - phrase_len + 1):
        if text_words[index : index + phrase_len] == phrase_words:
            return True
    return False


class TranscriptionWakeWordDetector(WakeWordDetector):
    """Detect the wake phrase by transcribing short rolling chunks."""

    name = "transcription"

    def __init__(
        self,
        *,
        stt: SpeechToTextProvider | None = None,
        phrase: str = "azmuth",
        chunk_seconds: float = 1.5,
        overlap_seconds: float = 0.5,
    ) -> None:
        phrase = (phrase or "").strip()
        if not phrase:
            raise ValueError("phrase must be non-empty")
        if not _WORDS.findall(phrase):
            raise ValueError(
                f"phrase {phrase!r} contains no matchable word characters"
            )
        if chunk_seconds <= 0:
            raise ValueError(f"chunk_seconds must be > 0, got {chunk_seconds}")
        if overlap_seconds < 0 or overlap_seconds >= chunk_seconds:
            raise ValueError(
                f"overlap_seconds must be in [0, {chunk_seconds}), "
                f"got {overlap_seconds}"
            )

        self._stt: SpeechToTextProvider = stt or NullSpeechToTextProvider()
        self._phrase = phrase
        self._chunk_seconds = chunk_seconds
        self._overlap_seconds = overlap_seconds

        self._buffer: list[AudioFrame] = []
        self._buffer_seconds = 0.0

    # -- introspection -----------------------------------------------------
    @property
    def phrase(self) -> str:
        return self._phrase

    @property
    def chunk_seconds(self) -> float:
        return self._chunk_seconds

    @property
    def overlap_seconds(self) -> float:
        return self._overlap_seconds

    @property
    def stt(self) -> SpeechToTextProvider:
        return self._stt

    def is_available(self) -> bool:
        try:
            return self._stt.is_available()
        except Exception:  # noqa: BLE001 - availability must never raise
            logger.exception(
                "wake word STT availability check failed",
                extra={"event": "WAKE_STT_AVAILABILITY_FAILED"},
            )
            return False

    # -- detection ---------------------------------------------------------
    async def detect(self, frame: AudioFrame) -> bool:
        if frame.is_empty:
            return False

        self._buffer.append(frame)
        self._buffer_seconds += frame.duration_seconds

        if self._buffer_seconds < self._chunk_seconds:
            return False

        return await self._transcribe_and_check()

    def reset(self) -> None:
        self._buffer.clear()
        self._buffer_seconds = 0.0

    # -- internals ---------------------------------------------------------
    async def _transcribe_and_check(self) -> bool:
        if not self._buffer:
            return False

        audio = (
            self._buffer[0]
            if len(self._buffer) == 1
            else self._buffer[0].concat(*self._buffer[1:])
        )

        try:
            transcript = await self._stt.transcribe(audio)
        except Exception:  # noqa: BLE001 - STT must never crash the detector
            logger.exception(
                "wake word transcription failed",
                extra={"event": "WAKE_STT_FAILED"},
            )
            self.reset()
            return False

        if matches_phrase(transcript.text, self._phrase):
            logger.info(
                "wake word detected",
                extra={
                    "event": "WAKE_WORD_DETECTED",
                    "phrase": self._phrase,
                    "transcript_length": len(transcript.text),
                },
            )
            self.reset()
            return True

        self._trim_to_overlap()
        return False

    def _trim_to_overlap(self) -> None:
        """Keep the most recent ``overlap_seconds`` of audio in the buffer."""
        if self._overlap_seconds <= 0 or not self._buffer:
            self._buffer.clear()
            self._buffer_seconds = 0.0
            return

        kept: list[AudioFrame] = []
        kept_seconds = 0.0
        # Walk backwards, taking frames until adding the next one would
        # exceed the overlap window. Keeps the *most recent* audio, which
        # is where a split phrase would begin.
        for frame in reversed(self._buffer):
            if kept_seconds + frame.duration_seconds > self._overlap_seconds:
                break
            kept.append(frame)
            kept_seconds += frame.duration_seconds
        kept.reverse()

        self._buffer = kept
        self._buffer_seconds = kept_seconds