"""Null text-to-speech provider — the Phase 1 default.

Unavailable, silent, no voices. Chosen over "silently pretend to succeed"
because the caller needs to distinguish *voice not configured* from
*voice configured but silent for this utterance*. The distinction is what
lets the UI show a meaningful indicator and what makes the manager's
success reporting honest.
"""

from __future__ import annotations

from app.voice.tts.base import (
    SynthesisResult,
    TextToSpeechProvider,
    VoiceProfile,
)


class NullTextToSpeechProvider(TextToSpeechProvider):
    """A provider that never synthesizes and never speaks."""

    name = "null"

    def is_available(self) -> bool:
        return False

    async def synthesize(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> SynthesisResult:
        del language, voice_id
        return SynthesisResult(
            text=text,
            audio=None,
            provider=self.name,
            error="text-to-speech is not configured",
        )

    async def speak(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> bool:
        del text, language, voice_id
        return False

    def list_voices(self, language: str | None = None) -> tuple[VoiceProfile, ...]:
        del language
        return ()

    def supports_language(self, language: str) -> bool:
        del language
        return False