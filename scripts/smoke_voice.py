import asyncio

from app.config.settings import load_settings
from app.language import build_language_manager
from app.voice import (
    AudioFrame,
    AudioSource,
    InputManager,
    SpeechToTextProvider,
    Transcript,
    VoiceInputManager,
    WakeWordDetector,
    build_voice_input_manager,
)


async def main() -> None:
    settings = load_settings()
    language = build_language_manager(settings)
    inputs = InputManager(language)

    # 1. Default pipeline (all null) — reports unavailable.
    default = build_voice_input_manager(language=language)
    print(f"default manager available: {default.is_available()}")
    print(f"listen_once result: {await default.listen_once()}")

    # 2. Text path works regardless.
    print()
    for text in ["Open Chrome", "இன்று weather என்ன?", "Chrome open pannu"]:
        u = inputs.from_text(text, session_language="ta")
        print(f"  {text!r} -> lang={u.language} source={u.source.value}")

    # 3. Fake pipeline — full flow end to end.
    class Source(AudioSource):
        name = "fake"
        def __init__(self, script): self._s = list(script); self._on = False
        def is_available(self) -> bool: return True
        @property
        def is_streaming(self) -> bool: return self._on
        async def start(self): self._on = True
        async def stop(self): self._on = False
        async def read_frame(self, timeout=1.0):
            return self._s.pop(0) if self._s else None

    class Wake(WakeWordDetector):
        name = "fake-wake"
        def __init__(self): self._n = 1
        @property
        def phrase(self): return "azmuth"
        def is_available(self): return True
        async def detect(self, frame): self._n -= 1; return self._n <= 0
        def reset(self): pass

    class STT(SpeechToTextProvider):
        name = "fake-stt"
        def is_available(self): return True
        async def transcribe(self, frame, *, language=None):
            return Transcript(text="Azmuth, open Chrome", confidence=0.94, provider="fake")
        def supported_languages(self): return ("ta", "en")

    frames = [
        AudioFrame(data=b"\x01"), AudioFrame(data=b"\x02\x03"),
        None, None, None, None, None,
    ]
    manager = VoiceInputManager(
        source=Source(frames), stt=STT(), wake=Wake(),
        language=language, input_manager=inputs, silence_frames_to_stop=5,
    )
    print()
    print(f"fake pipeline available: {manager.is_available()}")
    result = await manager.listen_once()
    print(f"transcribed: {result.text!r} -> lang={result.language} conf={result.confidence}")


if __name__ == "__main__":
    asyncio.run(main())