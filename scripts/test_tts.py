"""Test edge-tts playback."""
from __future__ import annotations
import asyncio


async def main() -> None:
    from app.config.settings import load_settings
    from app.voice.tts import build_tts_manager

    settings = load_settings()
    manager = build_tts_manager(settings)

    print(f"provider: {manager.provider_name}")
    print(f"available: {manager.is_available()}")
    print()

    if not manager.is_available():
        print("FAIL: TTS not available")
        return 1

    print("Speaking: 'Hello, this is Azmuth speaking.'")
    result = await manager.speak_response(
        "Hello, this is Azmuth speaking."
    )
    print(f"spoken: {result.spoken}")
    if result.error:
        print(f"error: {result.error}")

    print()
    print("Speaking Tamil: 'வணக்கம், நான் அஸ்முத்.'")
    result = await manager.speak_response(
        "வணக்கம், நான் ஆஸ்மத்.", language="ta"
    )
    print(f"spoken: {result.spoken}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))