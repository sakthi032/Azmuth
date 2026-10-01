"""Null wake-word detector.

Always unavailable, never detects anything. Registered so the pipeline
has a well-defined default and the manager can distinguish
"wake word not configured" from "wake word configured but silent".
"""

from __future__ import annotations

from app.voice.frames import AudioFrame
from app.voice.wake.base import WakeWordDetector


class NullWakeWordDetector(WakeWordDetector):
    """A detector that never fires."""

    name = "null"

    def __init__(self, *, phrase: str = "azmuth") -> None:
        self._phrase = phrase

    @property
    def phrase(self) -> str:
        return self._phrase

    def is_available(self) -> bool:
        return False

    async def detect(self, frame: AudioFrame) -> bool:  # noqa: ARG002
        return False

    def reset(self) -> None:
        return None