"""Standalone STT test — record 5 seconds from the mic, transcribe, print.

    py -m scripts.test_stt

Speak clearly into your microphone when prompted. Everything runs
locally — no network calls, no cloud APIs.
"""

from __future__ import annotations

import asyncio
import sys
import time


async def main() -> int:
    from app.voice.sources import build_audio_source
    from app.voice.stt import build_stt_provider
    from app.config.settings import load_settings

    settings = load_settings()

    print("=== loading configuration ===")
    print(f"  STT provider:  {settings.stt_provider}")
    print(f"  STT model:     {settings.stt_model}")
    print(f"  STT language:  {settings.stt_language or 'auto-detect'}")
    print(f"  Audio source:  {settings.audio_source}")
    print()

    source = build_audio_source(settings)
    if not source.is_available():
        print("FAIL: no microphone available.")
        return 1

    print("=== loading STT model (first time downloads ~140 MB) ===")
    stt = build_stt_provider(settings)
    if not stt.is_available():
        print("FAIL: STT provider not available.")
        return 1
    print(f"  provider ready: {stt.name}")
    print()

    print("=== starting microphone ===")
    await source.start()
    print("  streaming")
    print()

    print(">>> Speak into your microphone now. Recording 5 seconds...")
    print()

    frames = []
    started = time.perf_counter()
    while time.perf_counter() - started < 5.0:
        frame = await source.read_frame(timeout=0.5)
        if frame is not None and not frame.is_empty:
            frames.append(frame)

    await source.stop()

    if not frames:
        print("FAIL: no audio captured. Is your microphone muted?")
        return 1

    print(f"  captured {len(frames)} frames "
          f"({sum(f.duration_seconds for f in frames):.2f}s of audio)")

    if len(frames) == 1:
        audio = frames[0]
    else:
        audio = frames[0].concat(*frames[1:])

    print()
    print("=== transcribing (this takes ~1-2 seconds) ===")
    transcript = await stt.transcribe(audio)

    print()
    print("=== result ===")
    print(f"  text:       {transcript.text!r}")
    print(f"  language:   {transcript.language}")
    print(f"  confidence: {transcript.confidence:.3f}")
    print(f"  duration:   {transcript.duration_ms:.0f}ms")
    print(f"  provider:   {transcript.provider}")

    if not transcript.text.strip():
        print()
        print("NOTE: empty transcript. The model may not have heard you — "
              "try again, speaking louder or closer to the mic.")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))