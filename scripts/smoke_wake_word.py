import asyncio

from app.config.settings import load_settings
from app.voice import (
    AudioFrame,
    ManualWakeWordDetector,
    SpeechToTextProvider,
    Transcript,
    TranscriptionWakeWordDetector,
    build_wake_word_detector,
)


class EchoSTT(SpeechToTextProvider):
    name = "echo"
    def is_available(self) -> bool:
        return True
    async def transcribe(self, frame, *, language=None):  # noqa: ANN001
        # Pretend the audio said "azmuth open Chrome".
        return Transcript(text="azmuth open Chrome", confidence=0.9, provider="echo")
    def supported_languages(self):
        return ("ta", "en")


def _frame(seconds: float = 0.25) -> AudioFrame:
    samples = int(seconds * 16_000)
    return AudioFrame(data=b"\x00" * (samples * 2))


async def main() -> None:
    settings = load_settings()
    detector = build_wake_word_detector(settings)
    print(f"default provider: {detector.name} | available: {detector.is_available()}")

    manual = ManualWakeWordDetector()
    print(f"manual before trigger: {await manual.detect(_frame())}")
    manual.trigger()
    print(f"manual after trigger:  {await manual.detect(_frame())}")
    print(f"manual again:          {await manual.detect(_frame())}")

    auto = TranscriptionWakeWordDetector(
        stt=EchoSTT(), phrase="azmuth", chunk_seconds=0.5, overlap_seconds=0.1
    )
    print(f"transcription available: {auto.is_available()}")
    fired = False
    for _ in range(2):
        if await auto.detect(_frame(0.25)):
            fired = True
            break
    print(f"transcription fired: {fired}")


if __name__ == "__main__":
    asyncio.run(main())