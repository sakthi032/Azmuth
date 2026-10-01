"""The voice pipeline.

Orchestrates the spec's exact flow:

    microphone
        ↓
    audio source
        ↓
    wake word detector
        ↓
    speech recognition
        ↓
    language detection (via InputManager)
        ↓
    UserInput

Every stage is injectable. Every stage defaulting to "unavailable" means
the pipeline reports unavailability honestly and never fabricates a
result. A voice failure — device error, STT error, timeout, empty
transcript — is logged and results in ``None``, never an exception that
reaches the brain.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from app.config.logging_setup import get_logger
from app.core.events import EventBus, EventName, emit
from app.language.manager import LanguageManager
from app.voice.frames import AudioFrame
from app.voice.input_manager import InputManager, UserInput
from app.voice.sources.base import AudioSource
from app.voice.stt.base import SpeechToTextProvider
from app.voice.transcript import Transcript
from app.voice.wake.base import WakeWordDetector

logger = get_logger("voice")


class VoiceInputManager:
    """Capture, recognize, and hand off one utterance at a time."""

    def __init__(
        self,
        *,
        source: AudioSource,
        stt: SpeechToTextProvider,
        wake: WakeWordDetector,
        language: LanguageManager,
        input_manager: InputManager | None = None,
        event_bus: EventBus | None = None,
        frame_timeout: float = 1.0,
        wake_timeout_seconds: float = 30.0,
        max_utterance_seconds: float = 15.0,
        silence_frames_to_stop: int = 5,
    ) -> None:
        self._source = source
        self._stt = stt
        self._wake = wake
        self._language = language
        self._input_manager = input_manager or InputManager(language)
        self._event_bus = event_bus

        self._frame_timeout = frame_timeout
        self._wake_timeout = wake_timeout_seconds
        self._max_utterance = max_utterance_seconds
        self._silence_to_stop = silence_frames_to_stop

    # -- diagnostics -------------------------------------------------------
    @property
    def source(self) -> AudioSource:
        return self._source

    @property
    def stt(self) -> SpeechToTextProvider:
        return self._stt

    @property
    def wake(self) -> WakeWordDetector:
        return self._wake

    @property
    def input_manager(self) -> InputManager:
        return self._input_manager

    def is_available(self, *, require_wake_word: bool = True) -> bool:
        """True if the pipeline can produce a :class:`UserInput`.

        A ``False`` result is not an error — callers (the UI, the brain)
        simply route through text input only. This is the check that
        makes "voice failure does not block text" a single boolean away.
        """
        if not self._source.is_available():
            return False
        if not self._stt.is_available():
            return False
        if require_wake_word and not self._wake.is_available():
            return False
        return True

    # -- single-shot -------------------------------------------------------
    async def listen_once(
        self,
        *,
        session_language: str | None = None,
        require_wake_word: bool = True,
    ) -> UserInput | None:
        """Wait for one utterance and return its :class:`UserInput`.

        Returns ``None`` for every recoverable failure:

        * pipeline unavailable (source, STT, or required wake)
        * wake word not heard within :attr:`wake_timeout_seconds`
        * empty audio captured
        * transcription returned an empty transcript
        * any exception from a stage — logged, not raised

        Raises nothing. Callers can loop on this without try/except.
        """
        if not self.is_available(require_wake_word=require_wake_word):
            logger.info(
                "voice pipeline unavailable",
                extra={"event": "VOICE_UNAVAILABLE"},
            )
            return None

        started_streaming = False
        try:
            await self._source.start()
            started_streaming = True
            self._wake.reset()

            if require_wake_word:
                detected = await self._wait_for_wake()
                if not detected:
                    return None

            audio = await self._capture_utterance()
            if audio is None or audio.is_empty:
                logger.info(
                    "no audio captured",
                    extra={"event": "VOICE_EMPTY_AUDIO"},
                )
                return None

            transcript = await self._stt.transcribe(audio)
            if transcript.is_empty:
                logger.info(
                    "empty transcript",
                    extra={"event": "VOICE_EMPTY_TRANSCRIPT"},
                )
                return None

            await self._emit(
                EventName.VOICE_DETECTED,
                text_length=len(transcript.text),
                provider=transcript.provider,
                confidence=round(transcript.confidence, 3),
            )

            return self._input_manager.from_transcript(
                transcript, session_language=session_language
            )

        except Exception:  # noqa: BLE001 - last line of defence
            # Voice must never bring down the app. Log the full trace and
            # return None so text input remains available.
            logger.exception(
                "voice pipeline error",
                extra={"event": "VOICE_PIPELINE_ERROR"},
            )
            return None
        finally:
            if started_streaming:
                try:
                    await self._source.stop()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "failed to stop audio source",
                        extra={"event": "VOICE_SOURCE_STOP_FAILED"},
                    )

    # -- continuous --------------------------------------------------------
    async def iter_inputs(
        self,
        *,
        session_language: str | None = None,
        require_wake_word: bool = True,
    ) -> AsyncIterator[UserInput]:
        """Yield :class:`UserInput` objects as they are recognized.

        Runs forever until the consumer stops iterating or the pipeline
        becomes unavailable. Failures inside a single iteration are
        logged and skipped — the generator does not raise.
        """
        while self.is_available(require_wake_word=require_wake_word):
            user_input = await self.listen_once(
                session_language=session_language,
                require_wake_word=require_wake_word,
            )
            if user_input is not None:
                yield user_input

    # -- internals ---------------------------------------------------------
    async def _wait_for_wake(self) -> bool:
        deadline = time.monotonic() + self._wake_timeout
        while time.monotonic() < deadline:
            frame = await self._source.read_frame(timeout=self._frame_timeout)
            if frame is None:
                await asyncio.sleep(0)
                continue
            try:
                if await self._wake.detect(frame):
                    self._wake.reset()
                    logger.info(
                        "wake word detected",
                        extra={
                            "event": "WAKE_WORD_DETECTED",
                            "phrase": self._wake.phrase,
                        },
                    )
                    await self._emit(
                        EventName.WAKE_WORD_DETECTED,
                        phrase=self._wake.phrase,
                    )
                    return True
            except Exception:  # noqa: BLE001 - keep listening on detector errors
                logger.exception(
                    "wake word detector error",
                    extra={"event": "WAKE_WORD_ERROR"},
                )
                self._wake.reset()
                return False
        return False

    async def _capture_utterance(self) -> AudioFrame | None:
        frames: list[AudioFrame] = []
        consecutive_silence = 0
        deadline = time.monotonic() + self._max_utterance

        while time.monotonic() < deadline:
            frame = await self._source.read_frame(timeout=self._frame_timeout)
            if frame is None or frame.is_empty:
                consecutive_silence += 1
                if frames and consecutive_silence >= self._silence_to_stop:
                    break
                await asyncio.sleep(0)
                continue

            consecutive_silence = 0
            frames.append(frame)

        if not frames:
            return None
        if len(frames) == 1:
            return frames[0]
        try:
            return frames[0].concat(*frames[1:])
        except ValueError:
            # Frames disagreed on format mid-capture. Fall back to the
            # largest single frame rather than failing the whole capture.
            logger.warning(
                "audio format changed mid-capture; using last frame",
                extra={"event": "VOICE_FORMAT_CHANGE"},
            )
            return frames[-1]

    async def _emit(self, name: EventName, **payload: Any) -> None:
        if self._event_bus is None:
            return
        try:
            await emit(self._event_bus, name, source="voice", **payload)
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to emit voice event",
                extra={"event": name.value},
            )


def build_voice_input_manager(
    *,
    language: LanguageManager,
    source: AudioSource | None = None,
    stt: SpeechToTextProvider | None = None,
    wake: WakeWordDetector | None = None,
    input_manager: InputManager | None = None,
    event_bus: EventBus | None = None,
) -> VoiceInputManager:
    """Construct a voice manager using null providers for anything missing.

    This is the Phase 1 default. The manager will report unavailable via
    :meth:`VoiceInputManager.is_available`, and text input continues to
    work — the exact property the spec requires when voice is not
    configured.
    """
    from app.voice.sources.null import NullAudioSource
    from app.voice.stt.null import NullSpeechToTextProvider
    from app.voice.wake.null import NullWakeWordDetector

    return VoiceInputManager(
        source=source or NullAudioSource(),
        stt=stt or NullSpeechToTextProvider(),
        wake=wake or NullWakeWordDetector(),
        language=language,
        input_manager=input_manager,
        event_bus=event_bus,
    )