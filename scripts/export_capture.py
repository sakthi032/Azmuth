"""Capture 5 seconds via the async source and save to a WAV.

Play the WAV back. If it sounds like your voice at normal speed → the
sample rate is correct. If it sounds fast/pitched-up → the sample rate
is wrong (44.1k labeled as 16k, or vice versa).

    py -m scripts.export_capture
"""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import numpy as np


async def main() -> None:
    from app.voice.sources import build_audio_source
    from app.config.settings import load_settings

    settings = load_settings()
    source = build_audio_source(settings)

    if not source.is_available():
        print("FAIL: microphone not available")
        return

    print(f"source: {source.name}")
    print(f"declared sample_rate: {getattr(source, 'sample_rate', 'unknown')}")
    print()

    await source.start()
    print("Recording 5 seconds — speak now: 'hello azmuth this is a test'")
    print()

    frames = []
    import time
    started = time.perf_counter()
    while time.perf_counter() - started < 5.0:
        frame = await source.read_frame(timeout=0.3)
        if frame is not None and not frame.is_empty:
            frames.append(frame)

    await source.stop()

    if not frames:
        print("FAIL: no frames captured")
        return

    first = frames[0]
    print(f"frame count: {len(frames)}")
    print(f"frame.sample_rate: {first.sample_rate}")
    print(f"frame.channels:    {first.channels}")
    print(f"frame.sample_width:{first.sample_width}")

    # Concatenate and write
    audio = frames[0].concat(*frames[1:]) if len(frames) > 1 else frames[0]
    print(f"total bytes: {len(audio.data)}")
    print(f"declared duration: {audio.duration_seconds:.2f}s")
    print()

    # Write WAV at the DECLARED rate
    out = Path("capture_declared.wav")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(audio.channels)
        w.setsampwidth(audio.sample_width)
        w.setframerate(audio.sample_rate)
        w.writeframes(audio.data)
    print(f"saved: {out.resolve()}")

    # Also write one at 44100 Hz in case the underlying audio is native
    out44 = Path("capture_44100.wav")
    with wave.open(str(out44), "wb") as w:
        w.setnchannels(audio.channels)
        w.setsampwidth(audio.sample_width)
        w.setframerate(44100)
        w.writeframes(audio.data)
    print(f"saved: {out44.resolve()}")

    print()
    print("=== now play both files ===")
    print(f"  start {out.name}        — if THIS sounds like your voice, "
          f"sample rate is correct")
    print(f"  start {out44.name}      — if THIS sounds normal and "
          f"the first sounds fast, sample rate is wrong")
    print()
    print("On Windows: right-click the file → Open with → Media Player")
    print("Or: py -c \"import winsound; winsound.PlaySound('capture_declared.wav', winsound.SND_FILENAME)\"")


if __name__ == "__main__":
    asyncio.run(main())