"""Wake-word detection triggered externally.

Fires when :meth:`trigger` is called — from a hotkey handler, a UI
button, a TTS confirmation prompt, or a test. No audio is involved.

The detector reports ``is_available() = True`` unconditionally. Whether
it *fires* is entirely under the caller's control, which is exactly what
makes it useful for tests and for environments where audio is not
practical (headless CI, keyboard-only interaction).
"""

from __future__ import annotations

from app.voice.frames import AudioFrame
from app.voice.wake.base import WakeWordDetector


class ManualWakeWordDetector(WakeWordDetector):
    """Wake on an explicit external signal."""

    name = "manual"

    def __init__(self, *, phrase: str = "azmuth") -> None:
        self._phrase = (phrase or "azmuth").strip() or "azmuth"
        self._pending = False

    # -- introspection -----------------------------------------------------
    @property
    def phrase(self) -> str:
        return self._phrase

    @property
    def is_pending(self) -> bool:
        return self._pending

    def is_available(self) -> bool:
        return True

    # -- signalling --------------------------------------------------------
    def trigger(self) -> None:
        """Signal that the wake phrase was 'spoken'.

        Idempotent while a trigger is already pending — a second call
        before :meth:`detect` consumes the first is a no-op.
        """
        self._pending = True

    # -- detection ---------------------------------------------------------
    async def detect(self, frame: AudioFrame) -> bool:
        # The frame's content is irrelevant — manual detection is external.
        # We return True on the next frame boundary after trigger() so the
        # caller sees a consistent "one frame → one decision" contract.
        del frame
        if self._pending:
            self._pending = False
            return True
        return False

    def reset(self) -> None:
        self._pending = False