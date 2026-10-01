"""The output of speech recognition.

A :class:`Transcript` is what the STT stage produces and what the input
manager consumes. It is the *only* type that crosses the boundary from
the voice subsystem into the brain — the brain never sees audio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Transcript:
    """One recognized utterance.

    ``language`` is the STT provider's best guess at what language the
    audio contained, if the provider reports one. It is **advisory** — the
    language manager (STEP 4) makes the final decision about which
    language to respond in, because that decision must also account for
    session state and Tanglish.
    """

    text: str
    language: str | None = None
    confidence: float = 0.0
    is_final: bool = True
    provider: str = "unknown"
    duration_ms: float = 0.0
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )
        if self.duration_ms < 0:
            raise ValueError(f"duration_ms must be >= 0, got {self.duration_ms}")

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "language": self.language,
            "confidence": round(self.confidence, 3),
            "is_final": self.is_final,
            "provider": self.provider,
            "duration_ms": round(self.duration_ms, 3),
        }

    @classmethod
    def empty(cls, *, provider: str = "none") -> "Transcript":
        return cls(text="", provider=provider, confidence=0.0)