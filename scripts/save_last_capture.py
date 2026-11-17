"""Capture 5 seconds and save as WAV. Listen to it after."""

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

    print(f"device={settings.audio_device}  rate={settings.audio_sample_rate}")
    print()

    await source.start()
    print("Recording 5 seconds — say 'hello azmuth this is a test'")
    print()

    frames = []
    import time
    started = time.perf_counter()
    while time.perf_counter() - started < 5.0:
        frame = await source.read_frame(timeout=0.3)
        if frame is not None and not frame.is_empty:
            frames.append(frame)
            arr = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0
            peak = float(np.max(np.abs(arr))) if arr.size else 0
            print(f"  rms={rms:7.1f}  peak={peak:7.1f}")

    await source.stop()

    if not frames:
        print("NO AUDIO CAPTURED")
        return

    audio = frames[0].concat(*frames[1:]) if len(frames) > 1 else frames[0]
    out = Path("last_capture.wav")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(audio.channels)
        w.setsampwidth(audio.sample_width)
        w.setframerate(audio.sample_rate)
        w.writeframes(audio.data)

    print()
    print(f"saved: {out.resolve()}")
    print()
    print("Play it now:")
    print(f"  py -c \"import winsound; winsound.PlaySound('last_capture.wav', winsound.SND_FILENAME)\"")
    print()
    print("Listen: does it sound like your normal voice?")
    print("  YES → mic is fine, problem is Whisper config")
    print("  NO (fast/pitched) → sample rate mismatch persists")
    print("  NO (silent) → audio not reaching the app")


if __name__ == "__main__":
    asyncio.run(main())