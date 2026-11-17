"""Probe every device at every common sample rate.

Tells us which (device, rate) combinations actually open.
    py -m scripts.probe_rates
"""

from __future__ import annotations

import sounddevice as sd

RATES = [8000, 16000, 22050, 32000, 44100, 48000, 96000]
FORMATS = ["int16", "float32"]


def probe(device: int, rate: int, fmt: str) -> tuple[bool, str]:
    try:
        sd.check_input_settings(
            device=device, samplerate=rate, channels=1, dtype=fmt
        )
        return True, ""
    except Exception as exc:
        return False, str(exc).split("\n")[0]


def main() -> None:
    devices = sd.query_devices()
    # Only probe devices with input channels
    inputs = [
        (idx, d) for idx, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]
    print(f"Found {len(inputs)} input devices. Probing each...")
    print()

    for idx, d in inputs:
        name = d["name"][:50]
        hostapi_name = sd.query_hostapis(d["hostapi"])["name"]
        print(f"[{idx}] {name}")
        print(f"      hostapi: {hostapi_name}  "
              f"default_rate: {d['default_samplerate']:.0f}")
        any_ok = False
        for rate in RATES:
            for fmt in FORMATS:
                ok, err = probe(idx, rate, fmt)
                if ok:
                    print(f"      ✓ {rate:>6} Hz  {fmt}")
                    any_ok = True
                    break  # one format is enough for this rate
        if not any_ok:
            print("      ✗ no working rate")
        print()


if __name__ == "__main__":
    main()