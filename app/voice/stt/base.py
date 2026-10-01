"""The speech-to-text interface."""

from __future__ import annotations

from abc import abstractmethod

from app.core.interfaces import Provider
from app.voice.frames import AudioFrame
from app.voice.transcript import Transcript


class SpeechToTextProvider(Provider):
    """Base class for every speech recognition backend.

    Providers are async because real ones do either network I/O
    (cloud) or substantial local compute (Whisper). Implementations that
    block should run the blocking part in a worker thread so the event
    loop stays responsive.

    ``transcribe`` is called once per utterance with the full audio — the
    pipeline buffers frames before calling it, so providers do not need
    to implement streaming. A future streaming STT can still be added by
    wrapping a provider and consuming
    :meth:`~app.voice.manager.VoiceInputManager.iter_inputs` instead.
    """

    @abstractmethod
    async def transcribe(
        self,
        frame: AudioFrame,
        *,
        language: str | None = None,
    ) -> Transcript:
        """Transcribe one utterance.

        ``language`` is a hint, not a contract. Providers that support
        multiple languages may use it to narrow search; providers that
        auto-detect may ignore it. A provider that cannot transcribe the
        input must return an empty :class:`Transcript` rather than raise.
        """

    @abstractmethod
    def supported_languages(self) -> tuple[str, ...]:
        """BCP-47 primary subtags this provider can recognise.

        Return ``()`` when the set is unknown or open-ended. Callers use
        this only for diagnostics and provider selection — they must not
        assume a missing language is unsupported.
        """