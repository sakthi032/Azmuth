"""Faster-Whisper speech recognition provider.

Runs Whisper locally via the `faster-whisper` package — CTranslate2-based,
4-8× faster than the reference implementation, and CPU-friendly. Default
model is `base` (~140 MB): accurate enough for English and Indian English,
fast enough that a 3-second utterance transcribes in ~1 second on a
modern CPU.

Model is lazy-loaded on first transcribe call. This keeps Azmuth's startup
fast and avoids loading a 140 MB model into memory unless voice is used.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from app.core.errors import ProviderError
from app.voice.frames import AudioFrame
from app.voice.stt.base import SpeechToTextProvider
from app.voice.transcript import Transcript

logger = logging.getLogger(__name__)


class FasterWhisperSTTProvider(SpeechToTextProvider):
    """Local speech recognition via faster-whisper."""

    name = "faster_whisper"

    def __init__(
        self,
        *,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
        beam_size: int = 1,
        vad_filter: bool = True,
        download_root: str | None = None,
        initial_prompt: str | None = None,       # ← ADD
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language or None
        self._beam_size = max(1, beam_size)
        self._vad_filter = vad_filter
        self._download_root = download_root
        self._initial_prompt = initial_prompt       # ← ADD

        self._model: Any = None
        self._load_lock = threading.Lock()

    # -- introspection -----------------------------------------------------
    def is_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            import numpy  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def model_size(self) -> str:
        return self._model_size

    def supported_languages(self) -> tuple[str, ...]:
        # Whisper supports ~99 languages; the ones we care about here.
        return (
            "en", "ta", "hi", "te", "ml", "kn", "mr", "bn", "gu", "pa",
            "ur", "zh", "ja", "ko", "es", "fr", "de", "it", "pt", "ru",
            "ar", "tr", "vi", "th", "id",
        )

    # -- transcription -----------------------------------------------------
    async def transcribe(
        self,
        frame: AudioFrame,
        *,
        language: str | None = None,
    ) -> Transcript:
        if frame.is_empty:
            return Transcript.empty(provider=self.name)

        started = time.perf_counter()
        try:
            text, detected_lang, confidence = await asyncio.to_thread(
                self._transcribe_sync, frame, language
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "faster-whisper transcription failed",
                extra={"event": "STT_TRANSCRIBE_FAILED"},
            )
            return Transcript(
                text="",
                language=None,
                confidence=0.0,
                provider=self.name,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "transcription complete",
            extra={
                "event": "STT_TRANSCRIBED",
                "chars": len(text),
                "language": detected_lang,
                "confidence": round(confidence, 3),
                "elapsed_ms": round(elapsed_ms, 1),
            },
        )
        return Transcript(
            text=text,
            language=detected_lang,
            confidence=confidence,
            provider=self.name,
            duration_ms=elapsed_ms,
        )

    # -- internals ---------------------------------------------------------
    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ProviderError(
                    "faster-whisper is not installed. "
                    "Install it with: pip install faster-whisper"
                ) from exc

            logger.info(
                "loading whisper model",
                extra={
                    "event": "STT_MODEL_LOAD",
                    "model": self._model_size,
                    "device": self._device,
                },
            )
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                download_root=self._download_root,
            )
            return self._model

    def _transcribe_sync(
        self, frame: AudioFrame, language: str | None
    ) -> tuple[str, str | None, float]:
        import numpy as np
        from scipy.signal import resample_poly

        if frame.sample_width != 2:
            raise ProviderError(
                f"faster-whisper expects 16-bit PCM (sample_width=2), "
                f"got sample_width={frame.sample_width}"
            )

        # PCM bytes → int16 → float32 in [-1, 1]
        audio = np.frombuffer(frame.data, dtype=np.int16)

        # Stereo → mono
        if frame.channels > 1:
            audio = audio.reshape(-1, frame.channels).mean(axis=1).astype(
                np.int16
            )

        # Resample to 16 kHz — Whisper's required input rate.
        # resample_poly uses polyphase filtering, which is high quality
        # and fast enough (a 5-second clip resamples in <10 ms).
        if frame.sample_rate != 16_000:
            # Express the ratio as coprime integers for resample_poly.
            from math import gcd
            g = gcd(frame.sample_rate, 16_000)
            up = 16_000 // g
            down = frame.sample_rate // g
            audio = resample_poly(audio, up, down).astype(np.int16)

        audio_f32 = audio.astype(np.float32) / 32768.0

        model = self._load_model()
        effective_language = language or self._language

        segments, info = model.transcribe(
            audio_f32,
            language=effective_language,
            beam_size=self._beam_size,
            vad_filter=self._vad_filter,
            condition_on_previous_text=False,
            word_timestamps=False,
            initial_prompt=self._initial_prompt,     # ← ADD
        )

        parts: list[str] = []
        for segment in segments:
            parts.append(segment.text)

        text = " ".join(parts).strip()
        detected = getattr(info, "language", None)
        # language_probability measures language ID confidence, not
        # transcript accuracy. Report a neutral 0.9 for English/matching
        # language and 0.5 otherwise — anything else is a proxy that
        # misleads the caller.
        lang_prob = float(
            getattr(info, "language_probability", 0.9) or 0.9
        )
        if detected and self._language and detected == self._language:
            confidence = 0.9
        elif detected and self._language and detected != self._language:
            confidence = 0.5
        else:
            confidence = round(0.5 + lang_prob * 0.4, 2)
        return text, detected, confidence


__all__ = ["FasterWhisperSTTProvider"]