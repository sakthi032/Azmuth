"""Null STT provider — the Phase 1 default.

Always unavailable, always returns an empty transcript. Registered so the
voice pipeline is *complete* without committing to a speech engine, and
so tests can substitute a fake without touching the manager.
"""

from __future__ import annotations

from app.voice.frames import AudioFrame
from app.voice.stt.base import SpeechToTextProvider
from app.voice.transcript import Transcript


class NullSpeechToTextProvider(SpeechToTextProvider):
    """An STT provider that never recognizes anything."""

    name = "null"

    def is_available(self) -> bool:
        return False

    async def transcribe(
        self,
        frame: AudioFrame,
        *,
        language: str | None = None,
    ) -> Transcript:
        del frame, language
        return Transcript.empty(provider=self.name)

    def supported_languages(self) -> tuple[str, ...]:
        return ()