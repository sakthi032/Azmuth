"""Audio frame primitive.

An :class:`AudioFrame` is the unit of data that flows from an
:class:`~app.voice.sources.base.AudioSource` into an
:class:`~app.voice.stt.base.SpeechToTextProvider`. It is deliberately
byte-oriented and library-agnostic: any source — ``sounddevice``,
``pyaudio``, a WAV file, a test fixture — can produce frames without the
rest of the subsystem knowing what library produced them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """A chunk of PCM audio.

    ``data`` is raw PCM bytes. The three parameters needed to interpret
    them — sample rate, channel count, bytes-per-sample — are carried on
    the frame so a provider never has to guess.
    """

    data: bytes
    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2  # bytes per sample
    captured_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate must be > 0, got {self.sample_rate}")
        if self.channels <= 0:
            raise ValueError(f"channels must be > 0, got {self.channels}")
        if self.sample_width <= 0:
            raise ValueError(f"sample_width must be > 0, got {self.sample_width}")

    # -- queries -----------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        return len(self.data) == 0

    @property
    def frame_count(self) -> int:
        """Number of PCM samples per channel."""
        if self.is_empty:
            return 0
        return len(self.data) // (self.sample_width * self.channels)

    @property
    def duration_seconds(self) -> float:
        if self.is_empty:
            return 0.0
        return self.frame_count / self.sample_rate

    # -- composition -------------------------------------------------------
    def concat(self, *others: "AudioFrame") -> "AudioFrame":
        """Concatenate frames into one.

        Raises :class:`ValueError` if the audio formats differ — silently
        mixing sample rates would produce garbage downstream.
        """
        parts = [self, *others]
        for other in parts[1:]:
            if (
                other.sample_rate != self.sample_rate
                or other.channels != self.channels
                or other.sample_width != self.sample_width
            ):
                raise ValueError(
                    "cannot concatenate frames with different audio formats"
                )
        return AudioFrame(
            data=b"".join(f.data for f in parts),
            sample_rate=self.sample_rate,
            channels=self.channels,
            sample_width=self.sample_width,
            captured_at=parts[-1].captured_at,
        )

    @classmethod
    def silence(
        cls,
        *,
        sample_rate: int = 16_000,
        channels: int = 1,
        sample_width: int = 2,
    ) -> "AudioFrame":
        """An empty frame with a declared format. Useful as a sentinel."""
        return cls(
            data=b"",
            sample_rate=sample_rate,
            channels=channels,
            sample_width=sample_width,
        )