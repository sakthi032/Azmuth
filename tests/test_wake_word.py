from __future__ import annotations

import asyncio

import pytest

from app.config.settings import Settings, load_settings
from app.language import build_language_manager
from app.voice import (
    AudioFrame,
    AudioSource,
    InputManager,
    NullSpeechToTextProvider,
    NullWakeWordDetector,
    SpeechToTextProvider,
    Transcript,
    VoiceInputManager,
    build_wake_word_detector,
)
from app.voice.wake import (
    ManualWakeWordDetector,
    TranscriptionWakeWordDetector,
    matches_phrase,
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


def _frame(seconds: float = 0.25, *, rate: int = 16_000) -> AudioFrame:
    """Mono 16-bit frame of the requested duration."""
    samples = int(seconds * rate)
    return AudioFrame(data=b"\x00" * (samples * 2), sample_rate=rate)


class FakeSTT(SpeechToTextProvider):
    """STT provider that returns a scripted sequence of transcripts."""

    name = "fake"

    def __init__(self, transcripts: list[str] | str = "hello", *, available: bool = True) -> None:
        self._script = [transcripts] if isinstance(transcripts, str) else list(transcripts)
        self._available = available
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    async def transcribe(
        self, frame: AudioFrame, *, language: str | None = None
    ) -> Transcript:
        del frame, language
        self.calls += 1
        text = self._script.pop(0) if self._script else ""
        return Transcript(text=text, confidence=0.9, provider=self.name)

    def supported_languages(self) -> tuple[str, ...]:
        return ("ta", "en")


# ---------------------------------------------------------------------------
# matches_phrase — the pure matcher
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "azmuth",
        "Azmuth",
        "AZMUTH",
        "  azmuth  ",
        "azmuth,",
        "azmuth!",
        "hello azmuth",
        "azmuth open chrome",
        "hey azmuth open Chrome",
        "Azmuth, chrome open pannu",
        "please call azmuth now",
    ],
)
def test_matches_phrase_positive(text: str) -> None:
    assert matches_phrase(text, "azmuth") is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "azmuthing",
        "notazmuth",
        "hello world",
        "a z m u t h",
    ],
)
def test_matches_phrase_negative(text: str) -> None:
    assert matches_phrase(text, "azmuth") is False

def test_matches_phrase_treats_hyphens_as_word_boundaries() -> None:
    # ``-`` is not a word character, so "pre-azmuth-post" contains the
    # whole word "azmuth" and is a legitimate match. This is the correct
    # behaviour: someone saying "pre azmuth post" out loud did say the
    # wake word.
    assert matches_phrase("pre-azmuth-post", "azmuth") is True
    
    
def test_matches_phrase_empty_phrase_is_false() -> None:
    assert matches_phrase("anything", "") is False


def test_matches_phrase_multi_word_contiguous() -> None:
    assert matches_phrase("hey azmuth open chrome", "hey azmuth") is True
    assert matches_phrase("hey, azmuth!", "hey azmuth") is True


def test_matches_phrase_multi_word_requires_contiguity() -> None:
    # "hey" and "azmuth" separated by another word — not the phrase.
    assert matches_phrase("hey, please, azmuth", "hey azmuth") is False


def test_matches_phrase_tamil_script() -> None:
    assert matches_phrase("அஸ்மத், வானிலை என்ன?", "அஸ்மத்") is True


# ---------------------------------------------------------------------------
# TranscriptionWakeWordDetector — construction
# ---------------------------------------------------------------------------
def test_transcription_detector_rejects_empty_phrase() -> None:
    with pytest.raises(ValueError, match="phrase"):
        TranscriptionWakeWordDetector(phrase="")


def test_transcription_detector_rejects_phrase_without_words() -> None:
    with pytest.raises(ValueError, match="matchable word"):
        TranscriptionWakeWordDetector(phrase="!!!")


def test_transcription_detector_rejects_bad_chunk_seconds() -> None:
    with pytest.raises(ValueError, match="chunk_seconds"):
        TranscriptionWakeWordDetector(chunk_seconds=0)


def test_transcription_detector_rejects_bad_overlap() -> None:
    with pytest.raises(ValueError, match="overlap_seconds"):
        TranscriptionWakeWordDetector(chunk_seconds=1.0, overlap_seconds=-0.1)
    with pytest.raises(ValueError, match="overlap_seconds"):
        TranscriptionWakeWordDetector(chunk_seconds=1.0, overlap_seconds=1.0)


def test_transcription_detector_defaults_to_null_stt() -> None:
    detector = TranscriptionWakeWordDetector()
    assert detector.is_available() is False  # Null STT is unavailable


def test_transcription_detector_availability_follows_stt() -> None:
    available = TranscriptionWakeWordDetector(stt=FakeSTT("", available=True))
    unavailable = TranscriptionWakeWordDetector(stt=FakeSTT("", available=False))
    assert available.is_available() is True
    assert unavailable.is_available() is False


def test_transcription_detector_availability_never_raises() -> None:
    class ExplodingSTT(SpeechToTextProvider):
        name = "boom"
        def is_available(self) -> bool:
            raise RuntimeError("kaboom")
        async def transcribe(self, frame, *, language=None):  # noqa: ANN001
            return Transcript(text="", provider="boom")
        def supported_languages(self) -> tuple[str, ...]:
            return ()

    assert TranscriptionWakeWordDetector(stt=ExplodingSTT()).is_available() is False


# ---------------------------------------------------------------------------
# TranscriptionWakeWordDetector — detection
# ---------------------------------------------------------------------------
def test_transcription_detector_does_not_fire_below_chunk_threshold() -> None:
    detector = TranscriptionWakeWordDetector(
        stt=FakeSTT("azmuth"),
        chunk_seconds=1.0,
        overlap_seconds=0.2,
    )
    # Feed 0.5s total — below threshold.
    fired = asyncio.run(_feed(detector, [_frame(0.25), _frame(0.25)]))
    assert fired is False


def test_transcription_detector_fires_on_phrase() -> None:
    detector = TranscriptionWakeWordDetector(
        stt=FakeSTT(["hello", "please call azmuth now"]),
        chunk_seconds=0.5,
        overlap_seconds=0.1,
    )
    # First chunk (0.5s) → "hello", no match, kept overlap.
    # Second chunk → "please call azmuth now", match.
    frames = [_frame(0.25) for _ in range(4)]
    fired = asyncio.run(_feed(detector, frames))
    assert fired is True


def test_transcription_detector_no_fire_on_unrelated_transcript() -> None:
    detector = TranscriptionWakeWordDetector(
        stt=FakeSTT("something else entirely"),
        chunk_seconds=0.5,
        overlap_seconds=0.1,
    )
    fired = asyncio.run(_feed(detector, [_frame(0.25) for _ in range(4)]))
    assert fired is False


def test_transcription_detector_resets_buffer_after_fire() -> None:
    detector = TranscriptionWakeWordDetector(
        stt=FakeSTT("azmuth"),
        chunk_seconds=0.5,
        overlap_seconds=0.1,
    )
    asyncio.run(_feed(detector, [_frame(0.25) for _ in range(2)]))
    # After fire, feeding a fresh below-threshold amount must not fire.
    fired_again = asyncio.run(_feed(detector, [_frame(0.25)]))
    assert fired_again is False


def test_transcription_detector_keeps_overlap_after_no_match() -> None:
    stt = FakeSTT(["no match here", "azmuth please"])
    detector = TranscriptionWakeWordDetector(
        stt=stt,
        chunk_seconds=0.5,
        overlap_seconds=0.25,
    )
    # First chunk fires STT once with a negative result.
    asyncio.run(_feed(detector, [_frame(0.25) for _ in range(2)]))
    assert stt.calls == 1

    # Add just enough to reach the threshold again (0.25 overlap + 0.25 new).
    fired = asyncio.run(_feed(detector, [_frame(0.25), _frame(0.25)]))
    assert fired is True
    assert stt.calls == 2


def test_transcription_detector_handles_stt_exception() -> None:
    class ExplodingSTT(SpeechToTextProvider):
        name = "boom"
        def is_available(self) -> bool:
            return True
        async def transcribe(self, frame, *, language=None):  # noqa: ANN001
            raise RuntimeError("network")
        def supported_languages(self) -> tuple[str, ...]:
            return ()

    detector = TranscriptionWakeWordDetector(
        stt=ExplodingSTT(),
        chunk_seconds=0.5,
        overlap_seconds=0.1,
    )
    # Must not raise; must return False and reset the buffer.
    fired = asyncio.run(_feed(detector, [_frame(0.25) for _ in range(2)]))
    assert fired is False
    # Buffer was cleared, so a subsequent single frame does not trigger STT.
    assert asyncio.run(_feed(detector, [_frame(0.25)])) is False


def test_transcription_detector_ignores_empty_frames() -> None:
    detector = TranscriptionWakeWordDetector(
        stt=FakeSTT("azmuth"),
        chunk_seconds=0.5,
        overlap_seconds=0.1,
    )
    fired = asyncio.run(
        _feed(detector, [AudioFrame(data=b""), AudioFrame(data=b"")])
    )
    assert fired is False


def test_transcription_detector_reset_clears_buffer() -> None:
    detector = TranscriptionWakeWordDetector(
        stt=FakeSTT("azmuth"),
        chunk_seconds=1.0,
        overlap_seconds=0.2,
    )
    asyncio.run(_feed(detector, [_frame(0.5)]))  # below threshold
    detector.reset()
    # Now feeding 0.5s again should still be below the 1.0s threshold.
    fired = asyncio.run(_feed(detector, [_frame(0.5)]))
    assert fired is False


# ---------------------------------------------------------------------------
# ManualWakeWordDetector
# ---------------------------------------------------------------------------
def test_manual_detector_is_available_and_default_phrase() -> None:
    detector = ManualWakeWordDetector()
    assert detector.is_available() is True
    assert detector.phrase == "azmuth"


def test_manual_detector_does_not_fire_without_trigger() -> None:
    detector = ManualWakeWordDetector()
    assert asyncio.run(detector.detect(_frame())) is False
    assert asyncio.run(detector.detect(_frame())) is False


def test_manual_detector_fires_after_trigger() -> None:
    detector = ManualWakeWordDetector()
    detector.trigger()
    assert asyncio.run(detector.detect(_frame())) is True
    # Consumed — second call must not fire.
    assert asyncio.run(detector.detect(_frame())) is False


def test_manual_detector_trigger_is_idempotent_while_pending() -> None:
    detector = ManualWakeWordDetector()
    detector.trigger()
    detector.trigger()  # ignored
    assert asyncio.run(detector.detect(_frame())) is True
    assert asyncio.run(detector.detect(_frame())) is False


def test_manual_detector_reset_clears_pending() -> None:
    detector = ManualWakeWordDetector()
    detector.trigger()
    detector.reset()
    assert asyncio.run(detector.detect(_frame())) is False
    assert detector.is_pending is False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_factory_returns_null_for_default(settings: Settings) -> None:
    detector = build_wake_word_detector(settings)
    assert isinstance(detector, NullWakeWordDetector)
    assert detector.phrase == "azmuth"


def test_factory_reads_phrase_from_settings(tmp_path) -> None:
    s = load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "WAKE_WORD_PHRASE": "jarvis",
        },
        create_directories=False,
    )
    detector = build_wake_word_detector(s)
    assert detector.phrase == "jarvis"


def test_factory_returns_manual_for_manual_provider(tmp_path) -> None:
    s = load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "WAKE_WORD_PROVIDER": "manual",
        },
        create_directories=False,
    )
    assert isinstance(build_wake_word_detector(s), ManualWakeWordDetector)


def test_factory_returns_transcription_with_stt(tmp_path) -> None:
    s = load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "WAKE_WORD_PROVIDER": "transcription",
        },
        create_directories=False,
    )
    stt = FakeSTT("azmuth")
    detector = build_wake_word_detector(s, stt=stt)
    assert isinstance(detector, TranscriptionWakeWordDetector)
    assert detector.stt is stt
    assert detector.is_available() is True


def test_factory_transcription_without_stt_still_constructs(tmp_path) -> None:
    s = load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "WAKE_WORD_PROVIDER": "transcription",
        },
        create_directories=False,
    )
    detector = build_wake_word_detector(s)
    assert isinstance(detector, TranscriptionWakeWordDetector)
    # Null STT → detector unavailable, but construction succeeded.
    assert detector.is_available() is False


def test_factory_unknown_provider_falls_back_to_null(tmp_path) -> None:
    s = load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "WAKE_WORD_PROVIDER": "does-not-exist",
        },
        create_directories=False,
    )
    assert isinstance(build_wake_word_detector(s), NullWakeWordDetector)


# ---------------------------------------------------------------------------
# Integration — a detector plugged into VoiceInputManager
# ---------------------------------------------------------------------------
class ScriptedSource(AudioSource):
    name = "scripted"

    def __init__(self, script: list[AudioFrame | None]) -> None:
        self._script = list(script)
        self._on = False

    def is_available(self) -> bool:
        return True

    @property
    def is_streaming(self) -> bool:
        return self._on

    async def start(self) -> None:
        self._on = True

    async def stop(self) -> None:
        self._on = False

    async def read_frame(self, timeout: float = 1.0) -> AudioFrame | None:
        # Real sources always yield — they wait for the device. Yielding
        # here lets tests interleave with the pipeline (triggering a
        # manual wake word, for example) deterministically instead of
        # racing against synchronous frame consumption.
        await asyncio.sleep(0)
        return self._script.pop(0) if self._script else None


def test_voice_manager_with_manual_wake(settings: Settings) -> None:
    """A manual trigger fires the pipeline; STT then transcribes the command.

    The first entry in the script is ``None`` on purpose. The pipeline
    consumes frames synchronously until it hits an empty frame (which is
    where ``_wait_for_wake`` yields to the loop). The trigger task
    therefore needs one ``None`` frame at the start to give it room to
    run before the wake-frame is consumed.
    """
    language = build_language_manager(settings)
    inputs = InputManager(language)
    wake = ManualWakeWordDetector()

    frames: list[AudioFrame | None] = [
        None,                            # yields loop → trigger task runs
        _frame(0.25),                    # wake detect consumes pending → True
        _frame(0.25),                    # command audio
        None, None, None, None, None,    # silence
    ]
    manager = VoiceInputManager(
        source=ScriptedSource(frames),
        stt=FakeSTT("open Chrome"),
        wake=wake,
        language=language,
        input_manager=inputs,
        silence_frames_to_stop=5,
    )

    async def run() -> object:
        async def fire() -> None:
            wake.trigger()
        asyncio.get_running_loop().call_soon(asyncio.create_task, fire())
        return await manager.listen_once()

    result = asyncio.run(run())
    assert result is not None
    assert result.text == "open Chrome"
    assert result.source.value == "voice"
    
    
    
def test_voice_manager_with_transcription_wake(settings: Settings) -> None:
    """The transcription detector runs alongside the pipeline's STT."""
    language = build_language_manager(settings)
    inputs = InputManager(language)

    wake_stt = FakeSTT("azmuth")       # wake detector sees this
    command_stt = FakeSTT("open Chrome")  # then main STT sees the command

    wake = TranscriptionWakeWordDetector(
        stt=wake_stt,
        chunk_seconds=0.5,
        overlap_seconds=0.1,
    )

    frames: list[AudioFrame | None] = [
        _frame(0.25), _frame(0.25),      # wake word chunks
        _frame(0.25),                    # command audio
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=ScriptedSource(frames),
        stt=command_stt,
        wake=wake,
        language=language,
        input_manager=inputs,
        silence_frames_to_stop=5,
    )

    result = asyncio.run(manager.listen_once())
    assert result is not None
    assert result.text == "open Chrome"
    assert wake_stt.calls == 1
    assert command_stt.calls == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _feed(detector, frames: list[AudioFrame]) -> bool:  # noqa: ANN001
    """Push frames through a detector; return True if any frame fired."""
    for frame in frames:
        if await detector.detect(frame):
            return True
    return False