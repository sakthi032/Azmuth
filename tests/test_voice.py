from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config.settings import Settings, load_settings
from app.core.events import Event, EventBus, EventName
from app.language import build_language_manager
from app.voice import (
    AudioFrame,
    AudioSource,
    InputManager,
    InputSource,
    NullAudioSource,
    NullSpeechToTextProvider,
    NullWakeWordDetector,
    SpeechToTextProvider,
    Transcript,
    UserInput,
    VoiceInputManager,
    WakeWordDetector,
    build_voice_input_manager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path) -> Settings:
    return load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
        },
        create_directories=False,
    )


@pytest.fixture()
def language(settings: Settings):
    return build_language_manager(settings)


@pytest.fixture()
def input_manager(language) -> InputManager:
    return InputManager(language)


def _frame(text: bytes = b"\x01\x02", *, rate: int = 16_000) -> AudioFrame:
    return AudioFrame(data=text, sample_rate=rate)


# ---------------------------------------------------------------------------
# Fakes used to drive the pipeline without real hardware
# ---------------------------------------------------------------------------
class FakeAudioSource(AudioSource):
    """Yields a fixed script of frames (or ``None`` on demand)."""

    name = "fake"

    def __init__(self, script: list[AudioFrame | None]) -> None:
        self._script = list(script)
        self._started = False

    def is_available(self) -> bool:
        return True

    @property
    def is_streaming(self) -> bool:
        return self._started

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def read_frame(self, timeout: float = 1.0) -> AudioFrame | None:
        if not self._script:
            return None
        return self._script.pop(0)


class FakeSTT(SpeechToTextProvider):
    name = "fake"

    def __init__(self, text: str, *, confidence: float = 0.9) -> None:
        self._text = text
        self._confidence = confidence
        self.calls: list[AudioFrame] = []

    def is_available(self) -> bool:
        return True

    async def transcribe(
        self, frame: AudioFrame, *, language: str | None = None
    ) -> Transcript:
        self.calls.append(frame)
        return Transcript(
            text=self._text,
            confidence=self._confidence,
            provider=self.name,
            duration_ms=frame.duration_seconds * 1000,
        )

    def supported_languages(self) -> tuple[str, ...]:
        return ("ta", "en")


class FakeWake(WakeWordDetector):
    name = "fake-wake"

    def __init__(self, phrase: str = "azmuth", fire_on_call: int = 1) -> None:
        self._phrase = phrase
        self._remaining = fire_on_call
        self.reset_calls = 0

    @property
    def phrase(self) -> str:
        return self._phrase

    def is_available(self) -> bool:
        return True

    async def detect(self, frame: AudioFrame) -> bool:
        self._remaining -= 1
        return self._remaining <= 0

    def reset(self) -> None:
        self.reset_calls += 1


# ---------------------------------------------------------------------------
# AudioFrame
# ---------------------------------------------------------------------------
def test_audio_frame_validates_parameters() -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        AudioFrame(data=b"", sample_rate=0)
    with pytest.raises(ValueError, match="channels"):
        AudioFrame(data=b"", channels=0)
    with pytest.raises(ValueError, match="sample_width"):
        AudioFrame(data=b"", sample_width=0)


def test_audio_frame_is_empty_for_empty_bytes() -> None:
    assert AudioFrame(data=b"").is_empty is True
    assert AudioFrame(data=b"\x00").is_empty is False


def test_audio_frame_duration_is_correct() -> None:
    # 16 kHz mono, 2 bytes/sample → 32 000 bytes = 1 second.
    frame = AudioFrame(data=b"\x00" * 32_000, sample_rate=16_000)
    assert frame.frame_count == 16_000
    assert frame.duration_seconds == pytest.approx(1.0)


def test_audio_frame_duration_zero_for_empty() -> None:
    assert AudioFrame(data=b"").duration_seconds == 0.0
    assert AudioFrame(data=b"").frame_count == 0


def test_audio_frame_concat_preserves_format() -> None:
    a = AudioFrame(data=b"\x01\x02")
    b = AudioFrame(data=b"\x03\x04")
    combined = a.concat(b)
    assert combined.data == b"\x01\x02\x03\x04"
    assert combined.sample_rate == a.sample_rate


def test_audio_frame_concat_rejects_format_mismatch() -> None:
    a = AudioFrame(data=b"\x01", sample_rate=16_000)
    b = AudioFrame(data=b"\x02", sample_rate=44_100)
    with pytest.raises(ValueError, match="different audio formats"):
        a.concat(b)


def test_audio_frame_silence_sentinel() -> None:
    s = AudioFrame.silence(sample_rate=44_100, channels=2)
    assert s.is_empty is True
    assert s.sample_rate == 44_100
    assert s.channels == 2


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------
def test_transcript_confidence_bounds() -> None:
    with pytest.raises(ValueError, match="confidence"):
        Transcript(text="hi", confidence=1.5)
    with pytest.raises(ValueError, match="confidence"):
        Transcript(text="hi", confidence=-0.1)


def test_transcript_duration_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="duration_ms"):
        Transcript(text="hi", duration_ms=-1.0)


def test_transcript_is_empty_for_whitespace_only() -> None:
    assert Transcript(text="   ").is_empty is True
    assert Transcript(text="hello").is_empty is False


def test_transcript_empty_factory() -> None:
    t = Transcript.empty(provider="stub")
    assert t.is_empty is True
    assert t.provider == "stub"
    assert t.confidence == 0.0


def test_transcript_as_dict_shape() -> None:
    payload = Transcript(text="hi", confidence=0.75, language="ta").as_dict()
    assert payload["text"] == "hi"
    assert payload["language"] == "ta"
    assert payload["confidence"] == 0.75


# ---------------------------------------------------------------------------
# Null providers
# ---------------------------------------------------------------------------
def test_null_audio_source_is_unavailable() -> None:
    src = NullAudioSource()
    assert src.is_available() is False
    assert src.is_streaming is False


def test_null_audio_source_reads_none() -> None:
    src = NullAudioSource()
    assert asyncio.run(src.read_frame()) is None


def test_null_audio_source_start_stop_is_safe() -> None:
    src = NullAudioSource()
    asyncio.run(src.start())
    asyncio.run(src.stop())
    asyncio.run(src.stop())  # idempotent


def test_null_stt_is_unavailable_and_returns_empty_transcript() -> None:
    provider = NullSpeechToTextProvider()
    assert provider.is_available() is False
    transcript = asyncio.run(provider.transcribe(_frame()))
    assert transcript.is_empty is True
    assert transcript.provider == "null"


def test_null_wake_is_unavailable_and_never_fires() -> None:
    detector = NullWakeWordDetector()
    assert detector.is_available() is False
    assert detector.phrase == "azmuth"
    assert asyncio.run(detector.detect(_frame())) is False
    detector.reset()  # must not raise


# ---------------------------------------------------------------------------
# InputManager — text
# ---------------------------------------------------------------------------
def test_input_manager_from_text_english(input_manager: InputManager) -> None:
    user_input = input_manager.from_text("What is the weather?")
    assert user_input.source is InputSource.TEXT
    assert user_input.language == "en"
    assert user_input.confidence == 1.0
    assert user_input.transcript is None


def test_input_manager_from_text_tamil(input_manager: InputManager) -> None:
    user_input = input_manager.from_text("இன்று வானிலை என்ன?")
    assert user_input.language == "ta"
    assert user_input.source is InputSource.TEXT


def test_input_manager_from_text_respects_session_language(
    input_manager: InputManager,
) -> None:
    # Tanglish is mixed; the established session language wins when it is
    # among the detected set.
    user_input = input_manager.from_text("Chrome open pannu", session_language="ta")
    assert user_input.language == "ta"


def test_input_manager_from_text_unknown_falls_back_to_default(
    input_manager: InputManager,
) -> None:
    assert input_manager.from_text("...").language == "en"


# ---------------------------------------------------------------------------
# InputManager — transcript
# ---------------------------------------------------------------------------
def test_input_manager_from_transcript_voice(input_manager: InputManager) -> None:
    t = Transcript(text="hello world", confidence=0.87, provider="fake")
    user_input = input_manager.from_transcript(t)
    assert user_input.source is InputSource.VOICE
    assert user_input.text == "hello world"
    assert user_input.confidence == 0.87
    assert user_input.transcript is t
    assert user_input.language == "en"


def test_input_manager_from_transcript_ignores_provider_language_hint(
    input_manager: InputManager,
) -> None:
    # Provider claims "en"; text is Tamil — the language policy must win.
    t = Transcript(text="வணக்கம்", language="en", confidence=0.9, provider="fake")
    user_input = input_manager.from_transcript(t)
    assert user_input.language == "ta"


def test_input_manager_from_transcript_zero_confidence_becomes_one(
    input_manager: InputManager,
) -> None:
    # A provider that does not report confidence gets the benefit of the
    # doubt, since a positive transcript is more informative than a
    # zero-confidence score.
    t = Transcript(text="hello", confidence=0.0, provider="fake")
    assert input_manager.from_transcript(t).confidence == 1.0


def test_user_input_is_empty_for_whitespace(input_manager: InputManager) -> None:
    assert input_manager.from_text("   ").is_empty is True


def test_user_input_as_dict(input_manager: InputManager) -> None:
    payload = input_manager.from_text("hello").as_dict()
    assert payload == {
        "text": "hello",
        "language": "en",
        "source": "text",
        "confidence": 1.0,
    }


# ---------------------------------------------------------------------------
# VoiceInputManager — availability
# ---------------------------------------------------------------------------
def test_manager_unavailable_with_null_providers(language) -> None:
    manager = build_voice_input_manager(language=language)
    assert manager.is_available() is False


def test_manager_available_with_fakes(
    language, input_manager: InputManager
) -> None:
    manager = VoiceInputManager(
        source=FakeAudioSource([_frame()]),
        stt=FakeSTT("hi"),
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
    )
    assert manager.is_available() is True


def test_manager_without_wake_word_can_skip_that_stage(
    language, input_manager: InputManager
) -> None:
    manager = VoiceInputManager(
        source=FakeAudioSource([_frame()]),
        stt=FakeSTT("hi"),
        wake=NullWakeWordDetector(),
        language=language,
        input_manager=input_manager,
    )
    assert manager.is_available(require_wake_word=False) is True
    assert manager.is_available(require_wake_word=True) is False


# ---------------------------------------------------------------------------
# VoiceInputManager — listen_once happy path
# ---------------------------------------------------------------------------
def test_listen_once_completes_full_pipeline(
    language, input_manager: InputManager
) -> None:
    # Wake fires on first frame. Then a few frames of audio, then
    # silence long enough to end the utterance.
    frames: list[AudioFrame | None] = [
        _frame(b"\x01\x02"),           # wake-word frame
        _frame(b"\x03\x04"),           # first command frame
        _frame(b"\x05\x06"),           # second command frame
        None, None, None, None, None,  # silence x5 (silence_frames_to_stop=5)
    ]
    source = FakeAudioSource(frames)
    stt = FakeSTT("open chrome", confidence=0.92)

    manager = VoiceInputManager(
        source=source,
        stt=stt,
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
        silence_frames_to_stop=5,
    )

    result = asyncio.run(manager.listen_once())
    assert result is not None
    assert isinstance(result, UserInput)
    assert result.text == "open chrome"
    assert result.source is InputSource.VOICE
    assert result.confidence == 0.92
    # STT saw a concatenated frame containing all command bytes.
    assert len(stt.calls) == 1
    assert stt.calls[0].data == b"\x03\x04\x05\x06"


def test_listen_once_tamil_input(language, input_manager: InputManager) -> None:
    frames: list[AudioFrame | None] = [
        _frame(), _frame(b"\x01\x02"),
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=FakeAudioSource(frames),
        stt=FakeSTT("இன்று வானிலை என்ன?"),
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
        silence_frames_to_stop=5,
    )
    result = asyncio.run(manager.listen_once())
    assert result is not None
    assert result.language == "ta"


def test_listen_once_skips_wake_when_disabled(
    language, input_manager: InputManager
) -> None:
    frames: list[AudioFrame | None] = [
        _frame(b"\x01\x02"),
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=FakeAudioSource(frames),
        stt=FakeSTT("hello"),
        wake=NullWakeWordDetector(),
        language=language,
        input_manager=input_manager,
        silence_frames_to_stop=5,
    )
    result = asyncio.run(manager.listen_once(require_wake_word=False))
    assert result is not None and result.text == "hello"


# ---------------------------------------------------------------------------
# VoiceInputManager — graceful failures
# ---------------------------------------------------------------------------
def test_listen_once_returns_none_when_unavailable(language) -> None:
    manager = build_voice_input_manager(language=language)
    assert asyncio.run(manager.listen_once()) is None


def test_listen_once_returns_none_when_wake_times_out(
    language, input_manager: InputManager
) -> None:
    # Source yields silent None forever; wake never fires.
    manager = VoiceInputManager(
        source=FakeAudioSource([None, None, None, None, None]),
        stt=FakeSTT("never reached"),
        wake=FakeWake(fire_on_call=999),
        language=language,
        input_manager=input_manager,
        wake_timeout_seconds=0.05,
        frame_timeout=0.001,
    )
    assert asyncio.run(manager.listen_once()) is None


def test_listen_once_returns_none_when_no_audio_after_wake(
    language, input_manager: InputManager
) -> None:
    # Wake fires, but no command audio follows.
    frames: list[AudioFrame | None] = [
        _frame(),
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=FakeAudioSource(frames),
        stt=FakeSTT("unused"),
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
        silence_frames_to_stop=3,
    )
    assert asyncio.run(manager.listen_once()) is None


def test_listen_once_returns_none_for_empty_transcript(
    language, input_manager: InputManager
) -> None:
    frames: list[AudioFrame | None] = [
        _frame(), _frame(b"\x01"),
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=FakeAudioSource(frames),
        stt=FakeSTT("   "),  # whitespace-only transcript
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
        silence_frames_to_stop=5,
    )
    assert asyncio.run(manager.listen_once()) is None


def test_listen_once_never_raises_on_stt_failure(
    language, input_manager: InputManager
) -> None:
    class ExplodingSTT(SpeechToTextProvider):
        name = "boom"
        def is_available(self) -> bool:
            return True
        async def transcribe(self, frame, *, language=None):  # noqa: ANN001
            raise RuntimeError("stt exploded")
        def supported_languages(self) -> tuple[str, ...]:
            return ()

    frames: list[AudioFrame | None] = [
        _frame(), _frame(b"\x01"),
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=FakeAudioSource(frames),
        stt=ExplodingSTT(),
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
        silence_frames_to_stop=5,
    )
    # Crucial property: an STT crash does not surface as an exception.
    assert asyncio.run(manager.listen_once()) is None


def test_listen_once_never_raises_on_source_failure(
    language, input_manager: InputManager
) -> None:
    class ExplodingSource(FakeAudioSource):
        name = "explode"
        async def start(self) -> None:
            raise RuntimeError("device missing")

    manager = VoiceInputManager(
        source=ExplodingSource([_frame()]),
        stt=FakeSTT("unused"),
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
    )
    assert asyncio.run(manager.listen_once()) is None


def test_source_is_stopped_after_listen(
    language, input_manager: InputManager
) -> None:
    source = FakeAudioSource([
        _frame(), _frame(b"\x01"),
        None, None, None, None, None,
    ])
    manager = VoiceInputManager(
        source=source,
        stt=FakeSTT("hello"),
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
        silence_frames_to_stop=5,
    )
    asyncio.run(manager.listen_once())
    assert source.is_streaming is False


# ---------------------------------------------------------------------------
# VoiceInputManager — events
# ---------------------------------------------------------------------------
def test_events_emitted_on_full_pipeline(
    language, input_manager: InputManager
) -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(EventName.WAKE_WORD_DETECTED, seen.append)
    bus.subscribe(EventName.VOICE_DETECTED, seen.append)

    frames: list[AudioFrame | None] = [
        _frame(), _frame(b"\x01"),
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=FakeAudioSource(frames),
        stt=FakeSTT("hello"),
        wake=FakeWake(phrase="azmuth"),
        language=language,
        input_manager=input_manager,
        event_bus=bus,
        silence_frames_to_stop=5,
    )
    asyncio.run(manager.listen_once())
    asyncio.run(asyncio.sleep(0))

    names = [e.name for e in seen]
    assert EventName.WAKE_WORD_DETECTED in names
    assert EventName.VOICE_DETECTED in names
    wake_event = next(e for e in seen if e.name is EventName.WAKE_WORD_DETECTED)
    assert wake_event.payload["phrase"] == "azmuth"


def test_event_bus_failure_does_not_break_listen(
    language, input_manager: InputManager
) -> None:
    class BrokenBus(EventBus):
        async def publish(self, event: Event) -> None:  # type: ignore[override]
            raise RuntimeError("bus down")

    frames: list[AudioFrame | None] = [
        _frame(), _frame(b"\x01"),
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=FakeAudioSource(frames),
        stt=FakeSTT("hello"),
        wake=FakeWake(),
        language=language,
        input_manager=input_manager,
        event_bus=BrokenBus(),
        silence_frames_to_stop=5,
    )
    result = asyncio.run(manager.listen_once())
    assert result is not None and result.text == "hello"


# ---------------------------------------------------------------------------
# VoiceInputManager — iter_inputs
# ---------------------------------------------------------------------------
def test_iter_inputs_yields_multiple_utterances(
    language, input_manager: InputManager
) -> None:
    # Two utterances back to back.
    frames: list[AudioFrame | None] = [
        _frame(), _frame(b"\x01"),           # first wake + audio
        None, None, None, None, None,        # end first utterance
        _frame(), _frame(b"\x02"),           # second wake + audio
        None, None, None, None, None,        # end second utterance
    ]

    class CountingSTT(FakeSTT):
        def __init__(self) -> None:
            super().__init__("hello")
            self.n = 0
        async def transcribe(self, frame, *, language=None):  # noqa: ANN001
            self.n += 1
            return Transcript(text=f"utterance {self.n}", confidence=0.9, provider="fake")

    manager = VoiceInputManager(
        source=FakeAudioSource(frames),
        stt=CountingSTT(),
        wake=FakeWake(fire_on_call=1),
        language=language,
        input_manager=input_manager,
        silence_frames_to_stop=5,
        wake_timeout_seconds=0.1,
        frame_timeout=0.001,
    )

    async def collect_two() -> list[UserInput]:
        out: list[UserInput] = []
        async for item in manager.iter_inputs():
            out.append(item)
            if len(out) >= 2:
                break
        return out

    items = asyncio.run(collect_two())
    assert [i.text for i in items] == ["utterance 1", "utterance 2"]
    # Wake needs to reset between utterances so the second fires.
    assert all(i.source is InputSource.VOICE for i in items)


# ---------------------------------------------------------------------------
# The property the spec cares about
# ---------------------------------------------------------------------------
def test_text_input_remains_available_when_voice_is_unavailable(
    language, input_manager: InputManager
) -> None:
    """Voice pipeline being unavailable must not affect text at all."""
    manager = build_voice_input_manager(language=language)
    assert manager.is_available() is False

    # Text input goes through InputManager directly — voice subsystem is
    # not in the path at all.
    user_input = input_manager.from_text("hello there")
    assert user_input.text == "hello there"
    assert user_input.source is InputSource.TEXT