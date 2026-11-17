"""Show raw microphone RMS/peak so we can tell silent vs. quiet vs. loud.

Run with Azmuth closed:
    py -m scripts.check_mic_level
"""

from __future__ import annotations

import asyncio
import time

import numpy as np


async def main() -> None:
    from app.voice.sources import build_audio_source
    from app.config.settings import load_settings

    settings = load_settings()
    source = build_audio_source(settings)

    if not source.is_available():
        print("FAIL: microphone not available")
        return

    print("=== starting microphone ===")
    await source.start()
    print("  streaming — speak continuously for 5 seconds")
    print()

    samples: list[np.ndarray] = []
    peak = 0.0
    rms_max = 0.0
    started = time.perf_counter()
    last_print = 0.0

    while time.perf_counter() - started < 5.0:
        frame = await source.read_frame(timeout=0.3)
        if frame is None:
            continue

        if frame.is_empty:
            elapsed = time.perf_counter() - started
            if elapsed - last_print > 0.25:
                print(f"  {elapsed:4.1f}s  silent (gated)")
                last_print = elapsed
            continue

        arr = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32)
        if arr.size == 0:
            continue

        rms = float(np.sqrt(np.mean(arr ** 2)))
        pk = float(np.max(np.abs(arr)))
        rms_max = max(rms_max, rms)
        peak = max(peak, pk)
        samples.append(arr)

        elapsed = time.perf_counter() - started
        if elapsed - last_print > 0.25:
            print(f"  {elapsed:4.1f}s  rms={rms:7.1f}  peak={pk:7.1f}")
            last_print = elapsed

    await source.stop()

    print()
    print("=== summary ===")
    print(f"  frames captured:  {len(samples)}")
    print(f"  max RMS:          {rms_max:.1f}")
    print(f"  max peak:         {peak:.1f}")
    print()

    if not samples:
        print("VERDICT: NOTHING captured. Silence gate filtered everything.")
        print("  → Your audio may be too quiet, OR the 16kHz resample is broken")
        return

    if rms_max < 200:
        print("VERDICT: mic far too quiet. Whisper will hallucinate.")
    elif rms_max < 800:
        print("VERDICT: mic very quiet — marginal for Whisper.")
    elif rms_max < 3000:
        print("VERDICT: mic level healthy.")
    else:
        print("VERDICT: mic level strong.")

    print()
    print("Range guide (16-bit int):")
    print("  <200    silence")
    print("  200-800 quiet speech")
    print("  800-3000 normal speech")
    print("  >3000   loud speech")


if __name__ == "__main__":
    asyncio.run(main())