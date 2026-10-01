"""Null audio source — the Phase 1 default.

Always unavailable. Reading a frame always returns ``None`` immediately.
This makes the voice subsystem usable at the API level without
committing to a specific audio backend, and it makes the failure mode
loud and clear: the voice pipeline simply reports "unavailable" rather
than silently pretending to listen.
"""

from __future__ import annotations

from app.voice.frames import AudioFrame
from app.voice.sources.base import AudioSource


class NullAudioSource(AudioSource):
    """A source that never produces audio."""

    name = "null"

    def __init__(self) -> None:
        self._streaming = False

    def is_available(self) -> bool:
        return False

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    async def start(self) -> None:
        # Deliberately does not set ``_streaming = True``: the source is
        # not actually capturing anything, and claiming otherwise would
        # mislead callers that check the flag as a proxy for health.
        return None

    async def stop(self) -> None:
        self._streaming = False

    async def read_frame(self, timeout: float = 1.0) -> AudioFrame | None:  # noqa: ARG002
        return None